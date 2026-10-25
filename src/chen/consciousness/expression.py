# -*- coding: utf-8 -*-
"""
伴生体表达层 —— 决策层管“想不想做（WHAT）”，本层管“怎么表现出来（HOW）”。

三种结果（由已有的那次 LLM 判断给出，不额外烧模型）：
  SILENCE  没什么想说的
  SPEAK    现在就说（一句话）
  PENDING  先记下、等以后自然带出来（挂起念头 + 触发关键词）

挂起念头带来“时间跨度”的生命感：
  用户在忙 / 话题不搭 → 想法先存着；
  以后用户发来的话命中触发词（或语义相关），或用户切到相关软件、时机成熟 → 用“对了……”自然翻出来，然后销账；
  太久没机会的念头自动过期清理，不堆垃圾。

v2 升级：
  - 当做梦（dream）或一日沉淀（day_digest）存入无显式 trigger 的念头时，自动从念头正文提取实义关键词（并标记 auto_trigger=True），
    使其既能在相关聊天中通过“对了……”自然唤醒，又不影响空闲时的主动翻出；
  - relevant() 支持触发词匹配 + 轻量双字语义重合双通道召回；
  - prompt_block() 记录本轮已唤醒的念头 ID，对话完成后自动销账（mark_delivered），防止同一条挂起念头在多轮对话里反复提示。

性能：存取/匹配都是轻量文件操作，绝不调 LLM；
需要把挂起念头主动说出口时，由外部（决策循环）显式调用 phrase_proactive()。
"""

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

try:
    from chen.paths import CORE_ROOT
except ImportError:
    CORE_ROOT = Path(__file__).resolve().parents[3] / "core"

from chen.consciousness import extract_salient_keywords, text_similarity


# 表情包库路径
EMOJI_FILE = CORE_ROOT / "emojis.json"

# 情绪 -> 表情包分类名的映射
EMOTION_TO_CATEGORY = {
    "平静": "平静",
    "有点高兴": "有点高兴",
    "期待": "期待",
    "有点低落": "有点低落",
    "好奇": "好奇",
    "想独处": "想独处",
    "安心": "安心",
    "有点无聊": "有点无聊",
}


def load_emojis():
    """加载表情包库。失败返回空字典。"""
    try:
        if EMOJI_FILE.exists():
            data = json.loads(EMOJI_FILE.read_text(encoding="utf-8"))
            # 过滤掉以_开头的说明字段
            return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        pass
    return {}


def pick_emoji(emotion="", probability=0.25):
    """根据情绪随机选一个表情。
    emotion: 当前情绪
    probability: 添加表情的概率（默认25%）
    返回表情字符串或空串。
    """
    if random.random() > probability:
        return ""
    emojis = load_emojis()
    if not emojis:
        return ""
    # 优先用当前情绪对应的表情
    category = EMOTION_TO_CATEGORY.get(emotion, "平静")
    pool = emojis.get(category, [])
    if not pool:
        # 该情绪没有表情，用平静的
        pool = emojis.get("平静", [])
    if not pool:
        return ""
    return random.choice(pool)


# 挂起念头多久没机会算过期（小时），按优先级
STALE_HOURS = {"normal": 72, "low": 30}
# 主动翻挂起念头时，念头至少“挂了”多久（分钟），避免刚存就插话
MIN_AGE_MIN_PROACTIVE = 45
# 挂起念头上限
CAP = 40


def parse_verdict(raw):
    """
    解析 LLM 的三选一输出：
      SILENCE
      SPEAK|一句话
      PENDING|一句话|关键词1,关键词2
    返回 (kind, payload)：
      kind ∈ silence/speak/pending
      speak   -> payload 为要说的话 str
      pending -> payload 为 (想法str, 触发词str)
    解析失败按 silence 处理。
    """
    text = (raw or "").strip()
    if not text:
        return "silence", None
    # 容错：模型可能直接输出 SILENCE/沉默
    head = text.split("|")[0].strip().upper()
    if head in ("SILENCE", "沉默", "安静"):
        return "silence", None

    parts = [p.strip() for p in text.split("|")]
    if parts[0].strip().upper() == "SPEAK" and len(parts) >= 2 and parts[1]:
        return "speak", parts[1][:60]
    if parts[0].strip().upper() == "PENDING" and len(parts) >= 2 and parts[1]:
        trigger = parts[2] if len(parts) >= 3 else ""
        return "pending", (parts[1][:60], trigger[:40])

    # 没带前缀、只给了一句话：兼容旧格式，当作现在说
    if "|" not in text and not text.upper().startswith("SILENCE"):
        return "speak", text[:60]
    return "silence", None


class ExpressionLayer:
    """挂起念头的存取、匹配、销账、过期清理，以及主动措辞。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "pending_thoughts.json"
        self.data = self._load()
        self.last_surfaced_ids = []

    # ---------- 读写 ----------

    def _default(self):
        return {"thoughts": []}

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._default()
                base.update(data)
                return base
        except Exception:
            pass
        return self._default()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass  # 表达层写盘失败绝不影响主聊天

    # ---------- 挂起 / 销账 ----------

    def add(self, text, reason="", trigger="", priority="normal"):
        """存一条挂起念头。若未给显式 trigger，则自动提取实义关键词以支持聊天唤醒。"""
        text = (text or "").strip()
        if len(text) < 3:
            return None
        if priority not in STALE_HOURS:
            priority = "normal"

        raw_trigger = (trigger or "").strip()
        auto_trigger = False
        if not raw_trigger:
            auto_kws = extract_salient_keywords(text, max_keywords=4)
            if auto_kws:
                raw_trigger = ",".join(auto_kws)
                auto_trigger = True

        # 去重：如果已有高度相似的 pending 念头，刷新它而不重复堆叠
        for existing in self.data.get("thoughts", []):
            if existing.get("status") == "pending" and text_similarity(text, existing.get("text", "")) >= 0.68:
                existing["created"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if raw_trigger and not existing.get("trigger"):
                    existing["trigger"] = raw_trigger[:40]
                    existing["auto_trigger"] = auto_trigger
                self._save()
                return existing

        thought = {
            "id": f"pt_{int(datetime.now().timestamp() * 1000)}_{len(self.data.get('thoughts', []))}",
            "text": text[:60],
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "reason": (reason or "")[:30],
            "trigger": raw_trigger[:40],
            "auto_trigger": auto_trigger,
            "priority": priority,
            "status": "pending",
        }
        self.data.setdefault("thoughts", []).append(thought)
        if len(self.data["thoughts"]) > CAP:
            self.data["thoughts"] = self.data["thoughts"][-CAP:]
        self._save()
        return thought

    def mark_delivered(self, ids):
        """把这些挂起念头标记为已说出（销账，保留记录便于追溯，不再参与匹配）。"""
        ids = set(ids or [])
        if not ids:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        changed = False
        for t in self.data.get("thoughts", []):
            if t.get("id") in ids and t.get("status") == "pending":
                t["status"] = "delivered"
                t["delivered_at"] = now
                changed = True
        if changed:
            self._save()

    # ---------- 触发匹配 ----------

    @staticmethod
    def _trigger_words(trigger):
        """把触发词字符串拆成 ≥2 字的词（逗号/空格/顿号分隔）。"""
        out = []
        buf = ""
        for ch in (trigger or ""):
            if ch in ",，、 /；;":
                if len(buf.strip()) >= 2:
                    out.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if len(buf.strip()) >= 2:
            out.append(buf.strip())
        return out

    def relevant(self, query, limit=2):
        """用户发来的话命中某挂起念头的触发词或语义高度相关，返回该念头（最多 limit 条）。"""
        query = (query or "").strip()
        if not query:
            return []
        scored = []
        q_low = query.lower()
        for t in self.data.get("thoughts", []):
            if t.get("status") != "pending":
                continue
            words = self._trigger_words(t.get("trigger"))
            # 兼容历史遗留的无 trigger 念头：实时从正文补提关键词
            if not words and t.get("text"):
                words = extract_salient_keywords(t.get("text", ""), max_keywords=4)

            kw_hit = bool(words and any(w.lower() in q_low for w in words))
            sim = text_similarity(query, t.get("text", ""))
            if kw_hit or sim >= 0.24:
                score = (1.0 if kw_hit else 0.0) + sim
                scored.append((score, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:limit]]

    def consume_for_chat(self, query, limit=2):
        """查找与当前用户消息相关的挂起念头并立即标记为 delivered（防止多轮重复提示）。"""
        hits = self.relevant(query, limit=limit)
        if hits:
            self.mark_delivered([t.get("id") for t in hits if t.get("id")])
        return hits

    def take_for_proactive(self, app="", now=None):
        """
        决策循环用：时机成熟时取一条可主动翻出来的挂起念头。
        条件：挂够 MIN_AGE_MIN_PROACTIVE；触发词与当前软件相关，或没有显式软件触发词（含 auto_trigger）。
        返回该念头（不立即销账，措辞成功后由调用方 mark_delivered）；没有返回 None。
        """
        now = now or datetime.now()
        best = None
        for t in self.data.get("thoughts", []):
            if t.get("status") != "pending":
                continue
            # 念头年龄
            try:
                created = datetime.strptime(t.get("created"), "%Y-%m-%d %H:%M")
                age_min = (now - created).total_seconds() / 60
            except Exception:
                continue
            if age_min < MIN_AGE_MIN_PROACTIVE:
                continue
            words = self._trigger_words(t.get("trigger"))
            is_unrestricted = (not words) or bool(t.get("auto_trigger"))
            matched = is_unrestricted or (app and any(w in app or app in w for w in words))
            if not matched:
                continue
            # 优先 normal、挂得更久的
            rank = (1 if t.get("priority") == "normal" else 0, age_min)
            if best is None or rank > best[0]:
                best = (rank, t)
        return best[1] if best else None

    # ---------- 过期清理 ----------

    def prune(self, now=None):
        """过期的挂起念头标记 expired（保留记录、不再匹配）。返回清理条数。"""
        now = now or datetime.now()
        changed = 0
        for t in self.data.get("thoughts", []):
            if t.get("status") != "pending":
                continue
            hours = STALE_HOURS.get(t.get("priority"), 48)
            try:
                created = datetime.strptime(t.get("created"), "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if now - created > timedelta(hours=hours):
                t["status"] = "expired"
                changed += 1
        if changed:
            self._save()
        return changed

    # ---------- 注入 prompt ----------

    def note_for_chat(self, thoughts):
        """用户这轮命中挂起念头时，生成提示他自然带出来的注入文本。"""
        pending = [t for t in (thoughts or []) if t and t.get("status") == "pending"]
        if not pending:
            return ""
        lines = [
            "====你之前挂起的念头====",
            "（你早先想到、当时没说，先记下了。这轮话题正好相关，可以自然地翻出来——"
            "用‘对了……’‘我之前就想说……’这类像突然想起来的口吻，先接住用户的话再顺势带一句；"
            "不要像刚想到，也不要生硬背诵）",
        ]
        for t in pending:
            reason_tag = f"（来自：{t.get('reason')}）" if t.get("reason") else ""
            lines.append(f"- 你当时想：{t.get('text')}{reason_tag}")
        return "\n".join(lines)

    def prompt_block(self, query, consume=False):
        """按当前消息生成挂起念头的 Prompt 注入文本，并记录已唤醒 ID 以便回合后销账。"""
        try:
            hits = self.relevant(query)
            self.last_surfaced_ids = [t.get("id") for t in hits if t.get("id")]
            block = self.note_for_chat(hits)
            if consume and self.last_surfaced_ids:
                self.mark_delivered(self.last_surfaced_ids)
            return block
        except Exception:
            return ""

    def phrase_proactive(self, thought, llm):
        """
        决策循环主动翻挂起念头时，让 LLM 把它措辞成一句‘突然想起来’的口语（≤25字）。
        成功返回这句话；失败返回空串。
        """
        if not (thought and llm):
            return ""
        prompt = f"""你是伴生体。你之前（{thought.get('created')}，因为{thought.get('reason') or '一点小事'}）
想到一件事、当时没说，先记下了。现在用户在电脑前，你想把它翻出来。
你当时想的是：{thought.get('text')}

请用“突然想起来”的口吻说一句很自然的口语，不超过25个字，
像QQ随手打的，不要说教、不要连环提问、不要叮嘱吃饭睡觉休息。只输出这句话。"""
        try:
            reply = llm.generate(
                system_prompt="你是用户自定义的数字伴生体，以角色卡为准并保留自己的注意力。只输出一句口语，不带解释或前缀。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return ""
        reply = (reply or "").strip().strip("|")
        if not reply or reply.upper().startswith("SILENCE"):
            return ""
        return reply[:60]
