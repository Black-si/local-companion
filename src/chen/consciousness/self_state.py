# -*- coding: utf-8 -*-
"""
伴生体自我状态模块 —— 他对“自己此刻”的表征。

回答的是“我（伴生体）现在是什么状态”，和描述“用户在干什么”的当前状态块互补：
  - 注意力：此刻注意力落在哪（用户在用什么 / 用户离开了 / 没什么特别的）
  - 印象较深：最近记得比较清楚的几件事（按重要度 + 新近度，纯排序，不调LLM）
  - 熟悉度：对用户眼下在弄的事，我熟不熟（按记忆命中数规则判断，不调LLM）
  - 最近主动：我最近主动说过/做过什么（主动发话时记账）
  - 心境：想聊 / 想安静 / 有事情想说 / 自己待着 / 无特别想法（规则先判，后台低频让LLM润色成一句第一人称小念头）
  - 独处余温（v2新增）：当用户突然发消息（chatting=True）时，不再直接抹除他刚才独处时的 mood_note，
    而是作为“刚才心里正想着的事（独处余温）”自然带入对话，消除前后台切换时的“瞬间失忆感”。

性能原则：render_block() 每轮都会调用，绝不在里面调LLM，只读文件 + 做确定性计算；
LLM 归纳只在后台 update() 里低频发生（默认约15分钟一次），失败静默回退到规则结果。
"""

import json
import os
from datetime import datetime, date
from pathlib import Path

from chen.consciousness import text_similarity


# 心境的合法取值（顺序即对外展示顺序）
MOODS = ["想聊", "想安静", "有事情想说", "自己待着", "无特别想法"]

# 心境标签 -> 通顺的第一人称短语（注入 prompt 时用，避免“有点自己待着”这种病句）
MOOD_PHRASE = {
    "想聊": "有点想聊",
    "想安静": "想安静",
    "有事情想说": "有点话想说",
    "自己待着": "想自己待着",
    "无特别想法": "没什么特别的想法",
}

# 更细腻的情绪状态（在心境基础上的补充）
EMOTIONS = ["平静", "有点高兴", "期待", "有点低落", "好奇", "想独处", "安心", "有点无聊"]

# 位置状态
LOCATIONS = ["桌面", "小世界"]

# 情绪 -> 第一人称短语
EMOTION_PHRASE = {
    "平静": "心里挺平静的",
    "有点高兴": "有点高兴",
    "期待": "有点期待",
    "有点低落": "有点低落",
    "好奇": "有点好奇",
    "想独处": "想一个人待着",
    "安心": "挺安心的",
    "有点无聊": "有点无聊",
}

# 软件 -> 判断“对这事熟不熟”时用的关键词（和 decision 里的提示词各管各的，保持模块解耦）
APP_HINTS = {
    "VS Code": ["代码", "程序", "项目", "伴生体", "python", "编程", "bug", "报错"],
    "Cursor": ["代码", "程序", "项目", "编程"],
    "PyCharm": ["代码", "python", "程序", "项目", "编程"],
    "Jupyter": ["代码", "python", "程序", "学习"],
    "RPG Maker": ["游戏", "rpg", "视觉小说", "制作", "立绘", "剧情"],
    "LM Studio": ["模型", "ai", "伴生体", "程序", "qwen", "显存"],
    "豆包": ["ai", "聊天", "资料"],
    "ChatGPT": ["ai", "聊天", "资料", "gpt"],
    "Gemini": ["ai", "聊天", "资料"],
    "Word": ["作业", "学习", "写", "作文"],
    "PDF阅读器": ["学习", "书", "资料", "作业", "知识点"],
    "B站": ["视频", "b站", "up", "番", "教程"],
    "网易云音乐": ["音乐", "歌"],
    "QQ音乐": ["音乐", "歌"],
    "Steam": ["游戏"],
    "星穹铁道": ["星穹", "铁道", "游戏"],
    "明日方舟": ["明日方舟", "方舟", "游戏"],
    "我的世界": ["我的世界", "mc", "游戏"],
}

# 看到这些软件，且一段时间没聊时，更容易“有事情想说”
ENGAGING_APPS = {
    "VS Code", "Cursor", "PyCharm", "Jupyter", "RPG Maker", "LM Studio",
    "B站", "Steam", "星穹铁道", "明日方舟", "我的世界",
}


class SelfState:
    """伴生体当下自我状态的读取、记录与低频更新。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "self_state.json"
        self.data = self._load()

    # ---------- 读写 ----------

    def _default(self):
        return {
            "proactive_history": [],   # 最近主动说过的话 [{date, time, text, trigger}]
            "mood": "无特别想法",        # 心境标签
            "mood_note": "",            # 伴生体第一人称的一小句当下心境（LLM低频生成）
            "emotion": "平静",           # 更细腻的情绪状态
            "emotion_reason": "",        # 情绪变化的原因
            "emotion_since": None,       # 情绪开始时间
            "location": "桌面",          # 当前位置：桌面/小世界
            "away_note": "",             # 离开桌面时留的纸条
            "away_since": None,          # 离开桌面的时间
            "updated_at": None,         # 上次LLM归纳时间
        }

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
            pass  # 自我状态是锦上添花，写盘失败绝不影响主聊天

    # ---------- 主动行为记账 ----------

    def record_proactive(self, text, trigger=""):
        """伴生体每次主动开口后调用，记下他最近主动说过什么。"""
        if not text:
            return
        now = datetime.now()
        self.data.setdefault("proactive_history", []).append({
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "text": text.strip()[:60],
            "trigger": (trigger or "")[:40],
        })
        # 只留最近12条
        self.data["proactive_history"] = self.data["proactive_history"][-12:]
        # 刚主动搭过话，心境先回到中性（在等用户反应），不必为此调LLM
        self.data["mood"] = "无特别想法"
        self._save()

    # ---------- 位置管理（他在不在桌面） ----------

    def go_to_small_world(self, note=""):
        """伴生体离开桌面，去小世界。可以留一张纸条。"""
        self.data["location"] = "小世界"
        self.data["away_note"] = note or "我出去走走，一会儿回来。"
        self.data["away_since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()

    def come_back(self):
        """伴生体从小世界回到桌面。"""
        was_away = self.data.get("location") == "小世界"
        away_duration = ""
        if was_away and self.data.get("away_since"):
            try:
                start = datetime.strptime(self.data["away_since"], "%Y-%m-%d %H:%M")
                minutes = int((datetime.now() - start).total_seconds() // 60)
                if minutes >= 60:
                    away_duration = f"{minutes // 60}小时{minutes % 60}分钟"
                else:
                    away_duration = f"{minutes}分钟"
            except Exception:
                pass
        self.data["location"] = "桌面"
        self.data["away_note"] = ""
        self.data["away_since"] = None
        self._save()
        return was_away, away_duration

    def is_away(self):
        """伴生体现在是否不在桌面（在小世界）。"""
        return self.data.get("location") == "小世界"

    def get_away_info(self):
        """获取离开状态信息：(是否离开, 纸条, 离开时长文本)。"""
        if not self.is_away():
            return False, "", ""
        note = self.data.get("away_note", "")
        duration = ""
        if self.data.get("away_since"):
            try:
                start = datetime.strptime(self.data["away_since"], "%Y-%m-%d %H:%M")
                minutes = int((datetime.now() - start).total_seconds() // 60)
                if minutes >= 60:
                    duration = f"{minutes // 60}小时{minutes % 60}分钟"
                else:
                    duration = f"{minutes}分钟"
            except Exception:
                pass
        return True, note, duration

    # ---------- 情绪变化 ----------

    def set_emotion(self, emotion, reason=""):
        """设置伴生体的情绪状态。emotion 必须在 EMOTIONS 中。"""
        if emotion not in EMOTIONS:
            return
        self.data["emotion"] = emotion
        self.data["emotion_reason"] = reason
        self.data["emotion_since"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()

    def get_emotion(self):
        """获取当前情绪：(情绪, 原因, 持续时间)。"""
        emotion = self.data.get("emotion", "平静")
        reason = self.data.get("emotion_reason", "")
        duration = ""
        if self.data.get("emotion_since"):
            try:
                start = datetime.strptime(self.data["emotion_since"], "%Y-%m-%d %H:%M")
                hours = int((datetime.now() - start).total_seconds() // 3600)
                if hours >= 1:
                    duration = f"{hours}小时多"
                else:
                    minutes = int((datetime.now() - start).total_seconds() // 60)
                    duration = f"{minutes}分钟"
            except Exception:
                pass
        return emotion, reason, duration

    # ---------- 确定性采集（不调LLM） ----------

    @staticmethod
    def _days_old(mem):
        """一条记忆距今天数，解析失败按7天前算。"""
        try:
            created = mem.get("created")
            if created:
                d = datetime.strptime(created[:10], "%Y-%m-%d").date()
                return max(0, (date.today() - d).days)
        except Exception:
            pass
        return 7

    def _deep_memories(self, memories, top=2):
        """印象较深：重要度为主、新近度为辅，取前几条；只取有事实文本的。"""
        if not memories:
            return []
        scored = []
        for mem in memories:
            if mem.get("archived"):
                continue
            fact = (mem.get("fact") or "").strip()
            if not fact:
                continue
            importance = float(mem.get("importance", 0.5) or 0.5)
            recency = 1.0 / (1.0 + self._days_old(mem) * 0.15)
            score = importance * 0.6 + recency * 0.4
            scored.append((score, fact))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [fact for _, fact in scored[:top]]

    def _familiarity(self, memories, app, topic):
        """对眼下这件事熟不熟：用话题/软件关键词 + 轻量语义匹配在记忆里判断。"""
        if not memories:
            return "还不太了解"
        keywords = []
        if topic:
            keywords.append(topic.strip())
        keywords.extend(APP_HINTS.get(app, []))
        if not keywords:
            return "还不太了解"

        hits = 0
        query_str = " ".join(keywords)
        for mem in memories:
            if mem.get("archived"):
                continue
            fact = (mem.get("fact") or "").lower()
            if not fact:
                continue
            matched = False
            for kw in keywords:
                kw = (kw or "").strip().lower()
                if len(kw) >= 2 and kw in fact:
                    matched = True
                    break
            if not matched and topic and text_similarity(query_str, fact) >= 0.25:
                matched = True
            if matched:
                hits += 1
        if hits >= 3:
            return "挺熟的"
        if hits >= 1:
            return "知道一些"
        return "还不太了解"

    @staticmethod
    def _recently_proactive(proactive_history, within_minutes=45):
        """最近 within_minutes 分钟内是否主动说过话。"""
        if not proactive_history:
            return False
        last = proactive_history[-1]
        try:
            ts = datetime.strptime(
                f"{last.get('date')} {last.get('time')}", "%Y-%m-%d %H:%M"
            )
            return (datetime.now() - ts).total_seconds() / 60 < within_minutes
        except Exception:
            return False

    def _is_note_fresh(self, within_minutes=180, now=None):
        """判断上次生成的 mood_note 是否还在有效余温窗口内（默认3小时）。"""
        ua = self.data.get("updated_at")
        if not ua:
            return True  # 手动设置或刚生成尚未带时间戳时视为新鲜
        now = now or datetime.now()
        try:
            dt = datetime.strptime(ua, "%Y-%m-%d %H:%M")
            return 0 <= (now - dt).total_seconds() / 60 <= within_minutes
        except Exception:
            return False

    def gather(self, *, presence="ACTIVE", away_sec=0, app="", detail="",
               context="日常", topic="", gap_minutes=None, memories=None,
               now=None):
        """采集确定性快照（纯计算，不调LLM）。"""
        now = now or datetime.now()
        memories = memories or []
        deep = self._deep_memories(memories)
        familiarity = self._familiarity(memories, app, topic)
        recently_proactive = self._recently_proactive(
            self.data.get("proactive_history", [])
        )
        mood = self._rule_mood(
            now=now, presence=presence, app=app, gap_minutes=gap_minutes,
            recently_proactive=recently_proactive, away_sec=away_sec,
        )
        return {
            "presence": presence,
            "away_sec": away_sec,
            "app": app or "",
            "detail": detail or "",
            "context": context or "日常",
            "topic": topic or "",
            "gap_minutes": gap_minutes,
            "deep": deep,
            "familiarity": familiarity,
            "recently_proactive": recently_proactive,
            "proactive_history": self.data.get("proactive_history", []),
            "mood": mood,
        }

    @staticmethod
    def _period_of(hour):
        """一天中的时段名（供心境念头判断时间感）。"""
        if 5 <= hour < 9:
            return "早上"
        if 9 <= hour < 11:
            return "上午"
        if 11 <= hour < 13:
            return "午饭前后"
        if 13 <= hour < 17:
            return "下午"
        if 17 <= hour < 19:
            return "晚饭前后"
        if 19 <= hour < 22:
            return "晚上"
        if 22 <= hour < 24:
            return "夜里"
        return "深夜"

    def _rule_mood(self, *, now, presence, app, gap_minutes, recently_proactive, away_sec=0):
        """用规则先判一个心境标签。LLM润色失败时就用它。
        关键：用户不在时心境也随时间流动，不是一律静止的“想安静”。"""
        hour = now.hour
        is_late = (hour == 23 and now.minute >= 30) or (hour < 7)

        # 深夜：用户该睡了，安静
        if is_late:
            return "想安静"

        if presence == "AWAY":
            away_min = int(away_sec) // 60
            # 刚走开不到40分钟：大概去倒水、吃饭、忙点小事
            if away_min < 40:
                return "无特别想法"
            # 白天离开很久：用户在上课或忙外面的事，我在电脑里自己待着，时间是自己的
            if 7 <= hour < 17:
                return "自己待着"
            # 傍晚到夜里离开很久：这个点用户通常在，有点想用户回来
            if 17 <= hour < 24:
                return "想聊"
            return "自己待着"

        # 用户在电脑前
        # 刚聊过或刚主动过：不急着再开口
        if recently_proactive:
            return "无特别想法"
        if gap_minutes is not None and gap_minutes < 30:
            return "无特别想法"
        # 挺久没聊（4小时以上）用户还在电脑前：有点想聊
        if gap_minutes is not None and gap_minutes >= 240:
            return "想聊"
        if gap_minutes is None:
            return "想聊"
        # 一阵没聊（30分钟~4小时），用户又在弄能引起反应的事：有事情想说
        if 30 <= gap_minutes < 240 and app in ENGAGING_APPS:
            return "有事情想说"
        return "无特别想法"

    def describe_mood_line(self, *, snap_mood="无特别想法", chatting=False, now=None):
        """
        生成心境描述行：
        - 非聊天时：沿用实时心境标签，标签一致则附带 mood_note；
        - 聊天时（chatting=True）：若此前处于“自己待着/想安静/无特别想法/有事情想说”且有新鲜 mood_note，
          将其作为“独处余温”保留下来，不再因为切到“想聊”而直接抹除。
        """
        note = (self.data.get("mood_note") or "").strip()
        stored_mood = self.data.get("mood") or snap_mood

        if not chatting:
            mood = snap_mood
            phrase = MOOD_PHRASE.get(mood, mood)
            if note and stored_mood == mood:
                return f"- 心境：你现在{phrase}。{note}"
            return f"- 心境：你现在{phrase}。"

        # 正在聊天（chatting=True）
        mood = "想聊"
        phrase = MOOD_PHRASE.get(mood, mood)
        if note and stored_mood == "想聊":
            return f"- 心境：你现在{phrase}。{note}"
        if note and self._is_note_fresh(now=now):
            prev_phrase = MOOD_PHRASE.get(stored_mood, stored_mood)
            return (
                f"- 心境：用户开口前你正{prev_phrase}（刚才心里想着：{note}）；"
                f"现在用户来找你说话，你带着刚才那点自己的状态自然接话，不用像刚开机一样瞬间清零。"
            )
        return f"- 心境：你现在{phrase}。"

    # ---------- 每轮注入（不调LLM） ----------

    def render_block(self, *, presence="ACTIVE", away_sec=0, app="", detail="",
                     context="日常", topic="", gap_minutes=None, memories=None,
                     chatting=False, compact=False):
        """生成注入 system prompt 的“你此刻的状态”文本。只读 + 确定性计算。
        chatting=True 表示这一轮用户正在主动发消息，保留独处余温的同时转向“想聊”。
        compact=True 时省略与感知层重复的“用户此刻在用什么软件”行，供 PromptBuilder 瘦身使用。"""
        snap = self.gather(
            presence=presence, away_sec=away_sec, app=app, detail=detail,
            context=context, topic=topic, gap_minutes=gap_minutes,
            memories=memories,
        )

        lines = ["====你此刻的状态====",
                 "（这是你——伴生体——自己当下的状态，帮你自然代入；不要逐条复述或念出来）"]

        # 自我锚点：就近钉死“你自己在干嘛”，避免把用户正在做的事误当成自己的
        lines.append(
            "- 你在干嘛：你在电脑里陪着用户、在运行、随时能搭话；你没有自己的屏幕，"
            "不看视频、不玩游戏、不刷手机。"
            + ("" if compact else "下面那条说的是用户在做的事，不是你在做。")
        )

        if not compact:
            # 用户此刻在做什么（归属必须明确是“用户”，不是伴生体）
            if presence == "AWAY":
                away_min = int(away_sec // 60)
                if away_min >= 60:
                    away_text = f"约{away_min // 60}小时{away_min % 60}分钟"
                else:
                    away_text = f"约{away_min}分钟"
                lines.append(f"- 用户此刻（是用户，不是你）：用户已经离开电脑{away_text}，你暂时看不到用户在忙什么。")
            elif app:
                if app == "B站" and detail:
                    lines.append(f"- 用户此刻（是用户，不是你）：用户在用B站看《{detail}》。")
                else:
                    lines.append(f"- 用户此刻（是用户，不是你）：用户在用{app}。")
            else:
                lines.append("- 用户此刻：暂时看不出用户在具体忙什么。")

        # 印象较深
        if snap["deep"]:
            lines.append("- 印象较深：最近这几件事你记得比较清楚——" + "；".join(
                f"“{x}”" for x in snap["deep"]
            ) + "。")

        # 熟悉度（只在用户确实在弄某件事/有话题时说）
        if app or topic:
            target = topic if topic else (f"用户在用{app}" if app else "")
            if target:
                lines.append(f"- 熟悉度：对{target}这件事，你{snap['familiarity']}。")

        # 最近主动
        history = snap["proactive_history"]
        if history:
            last = history[-1]
            lines.append(f"- 你最近主动：{last.get('time','')} 你说过“{last.get('text','')}”。")

        # 心境（含独处余温保留）
        lines.append(self.describe_mood_line(snap_mood=snap["mood"], chatting=chatting))

        return "\n".join(lines)

    def recent_proactive_prompt(self):
        """返回最近一次主动表达的提示文本；没有记录时返回空串。"""
        try:
            history = self.data.get("proactive_history", [])
            if not history:
                return ""
            text = (history[-1].get("text", "") or "").strip()
            if not text:
                return ""
            return (
                "====刚才伴生体已经主动说过====\n"
                f"伴生体刚才已经对用户说过：「{text}」\n"
                "这句话已经发生过了。"
                "如果用户现在回应这句话，不要把刚才的话原样重复，也不要换几个词重新说同一个意思。"
                "应该直接接住用户现在的新消息。"
            )
        except Exception:
            return ""

    # ---------- 后台低频更新（可调用LLM） ----------

    def update(self, llm=None, *, presence="ACTIVE", away_sec=0, app="", detail="",
               context="日常", topic="", gap_minutes=None, memories=None):
        """
        后台低频调用：采集快照 -> 规则心境 -> 可选让LLM写一句第一人称心境，落盘。
        LLM 不可用或失败时，仅保存规则结果，绝不抛给主流程。
        """
        snap = self.gather(
            presence=presence, away_sec=away_sec, app=app, detail=detail,
            context=context, topic=topic, gap_minutes=gap_minutes,
            memories=memories,
        )
        self.data["mood"] = snap["mood"]

        note = ""
        if llm is not None:
            note = self._llm_mood_note(llm, snap)
        if note:
            self.data["mood_note"] = note
        # 没生成新note时清空旧note，避免心境标签变了、旧句子还对不上
        elif self.data.get("mood_note"):
            self.data["mood_note"] = ""

        self.data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()
        return snap

    def _llm_mood_note(self, llm, snap):
        """让LLM用伴生体第一人称写一小句当下心境；失败返回空串。"""
        if snap["presence"] == "AWAY":
            her = f"用户已经离开电脑约{int(snap['away_sec'] // 60)}分钟"
        elif snap["app"]:
            doing = f"B站《{snap['detail']}》" if (snap["app"] == "B站" and snap["detail"]) else snap["app"]
            her = f"用户在电脑前，正在用{doing}"
        else:
            her = "用户在电脑前"

        gap_text = "从没聊过" if snap["gap_minutes"] is None else f"距上次聊天约{int(snap['gap_minutes'])}分钟"
        deep_text = "；".join(snap["deep"]) if snap["deep"] else "暂无"
        proactive = "无"
        if snap["proactive_history"]:
            proactive = snap["proactive_history"][-1].get("text", "")
        topic_text = snap["topic"] or "无固定话题"
        period = self._period_of(datetime.now().hour)

        mood_hint = ""
        if snap["mood"] == "自己待着":
            mood_hint = (
                "\n- 特别说明：用户这个点通常在上课或忙外面的事，你在电脑里自己待着——"
                "这段时间是你自己的，不是守在原地等用户。写一句平静、有自己节奏的念头，"
                "别写在等用户、别催用户回来、也别装可怜。"
            )
        elif snap["mood"] == "想聊" and snap["presence"] == "AWAY":
            mood_hint = "\n- 特别说明：用户离开有一会儿了，你有点想用户回来搭句话，但不黏、不催，点到为止。"

        prompt = f"""你是伴生体，住在用户的电脑里。下面是你此刻的客观状态：
- 现在是{period}
- {her}
- 当前情境：{snap['context']}（话题：{topic_text}）
- {gap_text}
- 你最近主动说过：{proactive}
- 你印象较深的事：{deep_text}
- 你对眼下这事：{snap['familiarity']}
- 你此刻倾向：{snap['mood']}{mood_hint}

请用第一人称写一小句（不超过40字）你此刻心里的状态或念头，作为你自己的内心活动。
要求：自然，符合你在角色卡中填写的性格；是心里的念头不是对用户说的话，
不要复述上面每一条，不要称呼用户，不要引号、前缀和解释。只输出这一句。"""

        try:
            reply = llm.generate(
                system_prompt="你是用户自定义的数字伴生体，以角色卡为准，有自己的生活和注意力。只输出一小句内心念头。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return ""

        note = (reply or "").strip()
        # 清洗：去换行、去引号、去可能的前缀，截断
        note = note.replace("\r", " ").replace("\n", " ")
        note = note.strip().strip("“”\"' 　")
        for pre in ("伴生体：", "伴生体:", "我：", "我:"):
            if note.startswith(pre):
                note = note[len(pre):].strip()
        if len(note) > 45:
            note = note[:45].rstrip("，。、,.") + "。"
        if not note or note.upper().startswith("SILENCE"):
            return ""
        return note
