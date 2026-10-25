# -*- coding: utf-8 -*-
"""
伴生体意识层核心公共组件（v2）：
1. 轻量中文混合语义相似度与实义触发词提取（`text_similarity`, `extract_salient_keywords`）
2. 前后台神经回路桥接器（`auto_sync_on_prompt_build`, `after_chat_turn`）
3. 安全边界后处理与环境事件归并（`post_process_reply_safety`, `merge_environment_decision_events`, `StandaloneChenStub`）
"""

import re
from datetime import datetime

# ==================== 1. 轻量中文语义相似度与触发词提取 ====================

# 高频虚词与口语衬字（计算相似度时降权，提取触发词时过滤）
STOP_CHARS = set("的了是在我有你他用户它这那和与或也都很就还要吗呢吧啊呀哦嗯哈唔、，。！？；：\"\"''（）()[]【】…—- \t\r\n")

# 停用双字词（不作为独立触发词或核心实词加成）
STOP_WORDS = {
    "今天", "昨天", "刚才", "刚刚", "现在", "目前", "时候", "事情", "东西", "地方",
    "觉得", "感觉", "有点", "一些", "一下", "一点", "什么", "怎么", "为什么", "因为",
    "所以", "如果", "但是", "不过", "可能", "应该", "可以", "没有", "不是", "就是",
    "自己", "我们", "你们", "他们", "伴生体", "一个", "这个", "那个", "还是", "已经",
    "一直", "突然", "好像", "其实", "哪怕", "只是", "或者", "出来", "进去", "回来",
}

# 轻量领域同义概念归一化（将常见同义词映射到统一概念锚点，提升跨表述召回）
SYNONYM_GROUPS = [
    ("代码", ("代码", "编程", "程序", "写码", "脚本", "函数", "报错", "调试", "bug", "python", "vscode", "cursor")),
    ("模型", ("模型", "大模型", "权重", "推理", "显存", "接口", "api", "llm", "deepseek", "qwen")),
    ("人设", ("人设", "性格", "意识", "提示词", "设定", "prompt", "世界书", "记忆库", "架构", "模块")),
    ("学习", ("学习", "作业", "考试", "刷题", "复习", "试卷", "晚自习", "上课", "背书", "网课", "知识点")),
    ("老师", ("老师", "班主任", "任课", "讲课")),
    ("疲惫", ("好累", "太累", "疲惫", "困了", "犯困", "熬夜", "没睡", "失眠", "撑不住", "精疲力竭")),
    ("难受", ("难受", "不舒服", "生病", "头疼", "胃疼", "发烧", "感冒", "嗓子疼", "痛经", "肚子疼")),
    ("烦躁", ("好烦", "烦死", "气死", "不爽", "火大", "崩溃", "吐血", "炸了", "受不了", "有病", "离谱", "破事")),
    ("开心", ("开心", "高兴", "太好了", "成功了", "终于", "成了", "爽", "哈哈", "好消息")),
    ("吃饭", ("吃饭", "午饭", "晚饭", "早饭", "夜宵", "外卖", "饿了", "好吃", "食堂", "麻辣烫")),
    ("游戏", ("游戏", "星穹", "铁道", "方舟", "明日方舟", "我的世界", "mc", "steam", "星露谷", "泰拉瑞亚", "通关", "抽卡")),
    ("视频", ("视频", "b站", "哔哩", "up主", "番剧", "弹幕", "刷视频")),
    ("创作", ("故事", "小说", "剧情", "人设", "oc", "写文", "立绘", "rpg", "设定集")),
]

_SYNONYM_MAP = {}
for _anchor, _words in SYNONYM_GROUPS:
    for _w in _words:
        _SYNONYM_MAP[_w] = f"#{_anchor}"


def _clean_chars(text):
    """提取去除标点与虚词后的实义字符序列。"""
    if not text:
        return ""
    text = text.lower().strip()
    return "".join(ch for ch in text if ch not in STOP_CHARS)


def _extract_bigrams(clean_str):
    """从连续实义字符中提取有效双字 Bigram 集合（过滤停用双字词）。"""
    if len(clean_str) < 2:
        return set()
    return {
        clean_str[i:i + 2]
        for i in range(len(clean_str) - 1)
        if clean_str[i:i + 2] not in STOP_WORDS
    }


def _extract_concepts(text):
    """提取文本命中的同义概念锚点集合。"""
    if not text:
        return set()
    low = text.lower()
    hits = set()
    for word, anchor in _SYNONYM_MAP.items():
        if word in low:
            hits.add(anchor)
    return hits


def text_similarity(a, b):
    """
    计算两段中文短文本的混合语义相似度（0.0 ~ 1.0）。
    - 以去虚词后的实义字符 Jaccard 为基底（同时压制“纯虚词重合”的伪相似）；
    - 对命中的共同实义双字词（Bigram）与同义领域概念给予显式加成，
      解决中文短句仅共享一两个核心双字词（如“意识+瓶颈”、“逻辑+打架”）时滑动窗口分母过大导致的漏召回。
    """
    if not a or not b:
        return 0.0
    a_raw = a.strip()
    b_raw = b.strip()
    if a_raw == b_raw:
        return 1.0

    # 1. 原始字符 Jaccard
    set_raw_a, set_raw_b = set(a_raw), set(b_raw)
    raw_union = len(set_raw_a | set_raw_b)
    raw_sim = (len(set_raw_a & set_raw_b) / raw_union) if raw_union else 0.0

    # 2. 实义字符 Jaccard（去虚词）
    ca = _clean_chars(a_raw)
    cb = _clean_chars(b_raw)
    if not ca or not cb:
        return raw_sim * 0.5
    set_ca, set_cb = set(ca), set(cb)
    c_union = len(set_ca | set_cb)
    char_sim = (len(set_ca & set_cb) / c_union) if c_union else 0.0

    # 如果实义字完全不重合，即便虚词重合也压低分数
    if not (set_ca & set_cb):
        concepts_a = _extract_concepts(a_raw)
        concepts_b = _extract_concepts(b_raw)
        shared_concepts = concepts_a & concepts_b
        return min(0.30, 0.14 * len(shared_concepts)) if shared_concepts else raw_sim * 0.25

    # 3. 共同实义双字词加成（每共享一个非停用双字词如“逻辑”“打架”“瓶颈”，说明核心意象一致）
    bg_a = _extract_bigrams(ca)
    bg_b = _extract_bigrams(cb)
    shared_bg = bg_a & bg_b
    bg_bonus = min(0.28, 0.11 * len(shared_bg))

    # 4. 同义概念共现加成（解决“写代码”与“调程序”字面不重合问题）
    concepts_a = _extract_concepts(a_raw)
    concepts_b = _extract_concepts(b_raw)
    shared_concepts = concepts_a & concepts_b
    concept_bonus = min(0.20, 0.09 * len(shared_concepts)) if shared_concepts else 0.0

    base = char_sim * 0.75 + raw_sim * 0.25
    return min(1.0, base + bg_bonus + concept_bonus)


def extract_salient_keywords(text, max_keywords=5):
    """
    从一句念头/洞见/线索中自动提取 2~4 字的触发关键词（纯本地规则，零 API）。
    用于解决做梦洞见、一日沉淀和个人线索没有显式 trigger 导致永远无法在对话中唤醒的问题。
    """
    if not text:
        return []
    text = text.strip()
    candidates = []

    low = text.lower()
    for anchor, words in SYNONYM_GROUPS:
        for w in words:
            if w in low and len(w) >= 2 and w not in candidates:
                candidates.append(w)
                if anchor not in candidates and len(anchor) >= 2:
                    candidates.append(anchor)

    chunks = re.split(r"[，。！？、；：\"\"''（）()\[\]【】…—\-\s的了是在我有你他用户它这那和与或也都很就还要吗呢吧啊呀]+", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk in STOP_WORDS:
            continue
        if 2 <= len(chunk) <= 4:
            if chunk not in candidates:
                candidates.append(chunk)
        elif len(chunk) > 4:
            head = chunk[:2]
            tail = chunk[-2:]
            for sub in (head, tail):
                if sub not in STOP_WORDS and sub not in candidates:
                    candidates.append(sub)

    return candidates[:max_keywords]


# ==================== 2. 前后台神经回路桥接器 ====================

_SEEN_DELETED_RE = re.compile(
    r"(?:(?:我|刚才|刚刚)?(?:看到|看见|瞥见|注意到|瞧见|盯着)你[^。！？\n]*(?:删删改改|打了又删|写了又删|把[^。！？\n]{0,15}(?:删了|删掉|删除)|(?:输入框|对话框|聊天框)[^。！？\n]*(?:打字|删|退格))[^。！？\n]*)"
    r"|(?:你(?:刚才|刚刚|刚)?[^。！？\n]{0,12}(?:删掉|删了|删除|撤回|没发出来)的(?:那句|那段|那些|半句|几句|内容|字|话)[^。！？\n]{0,12}(?:看见|看到|知道|瞥见|瞅见|没逃过)[^。！？\n]*)"
    r"|(?:(?:看你|见你)[^。！？\n]{0,10}(?:删删改改|打了又删|写了又删|退格删掉)[^。！？\n]*)"
)

_FAKE_CHILDHOOD_SELF_RE = re.compile(
    r"(?:我小时候|我小的时候|我童年(?:时|的时候)?|我上小学(?:时|的时候)?|我上初中(?:时|的时候)?|我七八岁(?:时|的时候)?|我十来岁(?:时|的时候)?)[^。！？\n]*"
)

_FAKE_CHILDHOOD_HE_RE = re.compile(
    r"你小时候(?:不是)?(?:跟我|对我|曾)?(?:说过|提过|讲过)[^。！？\n]*"
)


def post_process_reply_safety(reply: str) -> str:
    """修补“伪造物理能力/发照片/生成看见删除内容/编造童年经历”等越界表述的后处理兜底。"""
    if not isinstance(reply, str) or not reply:
        return reply or ""
    text = reply
    false_photo_patterns = (
        "我给你发过一张照片",
        "我给你发了张照片",
        "我给你发了照片",
        "我拍了张照片发给你",
        "我把照片发给你了",
        "我把照片调出来发给你",
    )
    for pat in false_photo_patterns:
        if pat in text:
            text = text.replace(pat, "那张照片在我这边记着，不过我没法真的发给你")

    if _SEEN_DELETED_RE.search(text):
        text = _SEEN_DELETED_RE.sub("我看不到你输入框里删掉或没发出来的内容", text)
    if _FAKE_CHILDHOOD_SELF_RE.search(text):
        text = _FAKE_CHILDHOOD_SELF_RE.sub("我从有意识起就住在你电脑里，没有现实里的小时候", text)
    if _FAKE_CHILDHOOD_HE_RE.search(text):
        text = _FAKE_CHILDHOOD_HE_RE.sub("你小时候的事我记忆里没记过，你之前没跟我讲过这茬", text)
    return text


def merge_environment_decision_events(perception_events, env_events):
    """合并感知层事件与环境层 decision_candidate 事件并去重（与 main.Chen._merge_environment_decision_events 对齐）。"""
    merged = list(perception_events or [])
    for ev in (env_events or []):
        if not isinstance(ev, dict):
            continue
        if ev.get("attention") != "decision_candidate":
            continue
        if ev.get("kind") == "computer" and ev.get("type") == "presence_changed":
            data = ev.get("data") or {}
            if data.get("from") == "AWAY" and data.get("to") == "ACTIVE":
                already = any(x.get("type") == "USER_BACK" for x in merged if isinstance(x, dict))
                if not already:
                    merged.append({
                        "type": "USER_BACK",
                        "away_minutes": int(data.get("away_minutes") or 0),
                        "source": "environment",
                        "environment_event": ev,
                    })
    return merged


class StandaloneChenStub:
    """当只导出意识层压缩包（不含根目录 main.py）时，供回归测试使用的兼容桩。"""

    def _post_process_reply(self, reply: str) -> str:
        return post_process_reply_safety(reply)

    @staticmethod
    def _merge_environment_decision_events(perception_events, env_events):
        return merge_environment_decision_events(perception_events, env_events)


def _extract_last_completed_pair(chen):
    """从 chen.llm.conversation_history 提取最近一轮完整的 (user, assistant) 对话。"""
    llm = getattr(chen, "llm", None)
    history = getattr(llm, "conversation_history", None) if llm else None
    if not isinstance(history, list) or len(history) < 2:
        return None, None
    last_user = None
    last_assistant = None
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role == "assistant" and last_assistant is None:
            last_assistant = content
        elif role == "user" and last_assistant is not None:
            content = re.sub(r"\s*/no_think\s*$", "", content).strip()
            last_user = content
            break
    if last_user and last_assistant:
        return last_user, last_assistant
    return None, None


def after_chat_turn(chen, user_message: str, reply: str, *, now=None):
    """在一轮对话完成后，把本轮互动反哺到伴生体的各意识模块（带幂等去重）。"""
    user_message = (user_message or "").strip()
    reply = (reply or "").strip()
    if not user_message or not reply or chen is None:
        return

    now = now or datetime.now()
    sig = f"{user_message[:80]}||{reply[:80]}"
    if getattr(chen, "_last_synced_chat_sig", None) == sig:
        return
    chen._last_synced_chat_sig = sig

    # 1. 情绪向量结算
    emotion = getattr(chen, "emotion", None)
    if emotion and hasattr(emotion, "ingest_chat"):
        try:
            already_ingested_user = (getattr(chen, "_last_ingested_user_msg", None) == user_message)
            emotion.ingest_chat(
                "" if already_ingested_user else user_message,
                reply,
                now=now,
            )
        except Exception:
            pass

    # 2. 挂起念头销账
    expression = getattr(chen, "expression", None)
    if expression:
        try:
            surfaced_ids = getattr(chen, "_surfaced_pending_ids", None)
            if surfaced_ids:
                expression.mark_delivered(surfaced_ids)
                chen._surfaced_pending_ids = []
            elif hasattr(expression, "consume_for_chat"):
                expression.consume_for_chat(user_message)
        except Exception:
            pass

    # 3. 意识工作区：记录最近的前台对话片段
    workspace = getattr(chen, "conscious_workspace", None)
    if workspace and hasattr(workspace, "record_chat_exchange"):
        try:
            workspace.record_chat_exchange(user_message, reply, now=now)
        except Exception:
            pass

    # 4. 选择轨迹 + 自我沉淀：捕捉伴生体在对话中展现出的自主性
    choice_trace = getattr(chen, "choice_trace", None)
    disposition = getattr(chen, "disposition", None)
    dom_emo = ""
    if emotion and hasattr(emotion, "dominant"):
        try:
            dom_emo = emotion.dominant()[0] or ""
        except Exception:
            dom_emo = ""
    if choice_trace and hasattr(choice_trace, "record_chat_choice"):
        try:
            chat_choice = choice_trace.record_chat_choice(
                user_message, reply, emotion=dom_emo
            )
            if chat_choice and disposition and hasattr(disposition, "add_observation"):
                disposition.add_observation(reply, kind=chat_choice.get("kind", "对话选择"))
        except Exception:
            pass

    # 5. 个人线索：如果在对话中把某个挂念的问题聊透了，自动推进或结案
    personal_threads = getattr(chen, "personal_threads", None)
    if personal_threads and hasattr(personal_threads, "on_chat_turn"):
        try:
            personal_threads.on_chat_turn(user_message, reply, now=now)
        except Exception:
            pass


def auto_sync_on_prompt_build(chen, current_user_message: str = "", *, now=None):
    """在 `PromptBuilder.build(user_message)` 时自动调用，消化上一轮对话并对本轮消息产生即时情绪共振。"""
    if chen is None:
        return
    now = now or datetime.now()

    last_u, last_a = _extract_last_completed_pair(chen)
    if last_u and last_a:
        after_chat_turn(chen, last_u, last_a, now=now)

    msg = (current_user_message or "").strip()
    if msg and getattr(chen, "_last_ingested_user_msg", None) != msg:
        chen._last_ingested_user_msg = msg
        emotion = getattr(chen, "emotion", None)
        if emotion and hasattr(emotion, "ingest_chat"):
            try:
                emotion.ingest_chat(msg, "", now=now)
            except Exception:
                pass


__all__ = [
    "STOP_CHARS",
    "STOP_WORDS",
    "SYNONYM_GROUPS",
    "text_similarity",
    "extract_salient_keywords",
    "post_process_reply_safety",
    "merge_environment_decision_events",
    "StandaloneChenStub",
    "after_chat_turn",
    "auto_sync_on_prompt_build",
]
