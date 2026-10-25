# -*- coding: utf-8 -*-
"""
决策模块 - 伴生体的判断核心
事件发生 → 值得注意吗？→ 和伴生体有关吗？→ 现在说合适吗？→ 说了有意义吗？→ 决定说/不说
SILENCE 是合法输出，不是出错
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import json
import random
from pathlib import Path
from chen.consciousness.expression import parse_verdict


class DecisionType(Enum):
    """决策类型"""
    SPEAK = "speak"        # 说话
    SILENCE = "silence"    # 沉默（合法输出）
    TOOL = "tool"          # 使用工具
    REMEMBER = "remember"  # 记住某事
    ENTER_WORLD = "enter_world"  # 进入小世界
    LEAVE_WORLD = "leave_world"  # 离开小世界回到桌面
    GO_TO_DESKTOP = "go_to_desktop"  # 意识明确想回到用户的桌面，触发桌宠


@dataclass
class Decision:
    """决策结果"""
    type: DecisionType
    message: Optional[str] = None      # SPEAK 时的内容
    tool_name: Optional[str] = None    # TOOL 时的工具名
    tool_args: Optional[dict] = None   # TOOL 时的参数
    memory: Optional[str] = None       # REMEMBER 时的记忆内容
    reason: Optional[str] = None       # 做出该决策的原因（用于调试日志）


# ---------- 小世界参数 ----------
SMALL_WORLD_MIN_STAY = 30       # 至少待30分钟
SMALL_WORLD_MAX_STAY = 120      # 超过2小时强制出来
LEAVE_WORLD_PROB = 0.12         # 超过最小停留后，每次决策循环12%概率出来
ENTER_WORLD_CHAT_GAP = 60       # 距上次聊天超过60分钟才考虑进去
# 进入小世界的基础概率按心境细分（在 _should_enter_world 中使用）
ENTER_WORLD_PROB_BY_MOOD = {
    "想安静": 0.30,
    "自己待着": 0.18,
    "无特别想法": 0.08,
}
WORLD_API_CHECK_INTERVAL = 120    # 用API复核"去哪个世界"的最小间隔（分钟）。本地规则先判，API只做低频复核


class DecisionEngine:
    """决策引擎 - 判断伴生体要不要说话、说什么、做什么"""

    def __init__(self, cooldown_minutes: int = 45, state_dir: Optional[str] = None):
        self.last_speak_time: Optional[datetime] = None
        self.cooldown_minutes = cooldown_minutes  # 主动说话的冷却时间（默认45分钟）
        # API 世界位置决策状态
        self._last_world_api_check: Optional[datetime] = None
        self._last_world_verdict = "stay"
        # 时间关怀去重：{归属日期: set(已提醒过的时段)}，保证每个饭点/深夜每天最多说一次
        self.fired_time_slots: Dict[str, set] = {}
        # 把“今天哪个饭点/深夜已提醒过”持久化到磁盘，防止程序重启后丢失去重状态
        # （否则一天重启多次时，同一晚的关怀可能被当成“今天没提醒过”又重复说）
        self._state_dir = Path(state_dir) if state_dir else None
        if self._state_dir:
            self._timecare_file = self._state_dir / "timecare_slots.json"
            self._load_timecare_state()
        else:
            self._timecare_file = None
        # 最近多久内聊过天就不再主动时间关怀（分钟），避免刚聊完又插话
        self.recent_chat_skip_minutes = 30

        # ---------- 打开软件 + 记忆沉淀的自主发话 ----------
        # 这些琐碎/系统界面切换不触发主动开口
        self.ignored_focus_apps = {
            "文件管理器", "任务管理器", "系统设置", "任务栏", "开始菜单",
            "桌面", "截图工具", "计算器", "未知", "Windows 默认锁屏", "锁屏",
        }
        # 同一个软件两次“因打开它而开口”的最小间隔（分钟），防止反复横跳时刷屏
        self.focus_cooldown_minutes = 90
        self.last_focus_speak: Dict[str, datetime] = {}

        # ---------- 主动发话“关怀主题”去重 ----------
        # 不同触发源可能表达同一个意思（切软件劝休息 vs 深夜催睡觉），按主题限频，避免重复唠叨
        # body=休息/睡觉/别累着，meal=吃饭，back=回来打招呼，focus=聊正在用的软件
        self.theme_cooldown_minutes = {
            "body": 120,   # 劝休息/睡觉两小时内不重复（不管来自哪个触发源）
            "meal": 240,   # 同一顿饭不重复催
            "back": 60,
            "focus": 90,
            "mood": 180,   # 心境驱动的自由发话至少隔3小时，没事不冒头
        }
        self.last_theme_speak: Dict[str, datetime] = {}
        # 吃饭(meal)和作息(body)同属身体关怀：刚提醒过一类，90分钟内不再换由头触发另一类
        self.wellbeing_cross_minutes = 90
        self.last_proactive_message = ""  # 最近一次主动说的话，喂给LLM避免措辞重复
        # 软件名 -> 检索记忆时用的关键词（没配的就用软件名本身）
        self.focus_search_hints = {
            "VS Code": "编程 代码 项目 伴生体 程序",
            "Cursor": "编程 代码 项目",
            "PyCharm": "编程 代码 项目 Python",
            "Jupyter": "编程 代码 学习",
            "RPG Maker": "游戏 制作 RPG 视觉小说",
            "LM Studio": "模型 AI 伴生体 程序",
            "豆包": "AI 聊天 查资料",
            "ChatGPT": "AI 聊天 查资料",
            "Gemini": "AI 聊天 查资料",
            "Word": "学习 作业 写东西",
            "PDF阅读器": "学习 看书 资料",
            "B站": "视频 B站 放松",
            "网易云音乐": "音乐 听歌",
            "QQ音乐": "音乐 听歌",
            "Steam": "游戏",
            "星穹铁道": "游戏 星穹铁道",
            "明日方舟": "游戏 明日方舟",
            "我的世界": "游戏 我的世界",
        }

    def decide(self, events: List[Dict], current_state: Dict, llm=None, context=None,
               presence: str = "ACTIVE", last_chat_gap_minutes: Optional[float] = None,
               memory=None, mood: str = "无特别想法", mood_note: str = "",
               app: str = "", detail: str = "", expression=None,
               choice_trace=None, world_state=None, decision_context=None) -> Decision:
        """
        核心决策入口
        输入：
          - events: 感知层积攒的事件列表（由 perception.pop_events() 提供）
          - current_state: 当前时间等环境信息
          - llm: 大模型实例（用于需要时生成自然回复）
          - context: 当前情境（日常/思考/学习/项目）
          - presence: 用户现在是否在电脑前（ACTIVE/AWAY）
          - last_chat_gap_minutes: 距上一次和用户聊天的分钟数（None=从没聊过）
          - memory: 记忆库实例，用于“打开软件+记忆沉淀”时检索相关记忆
          - mood: 伴生体此刻心境（想聊/想安静/有事情想说/无特别想法），影响开口倾向
          - mood_note: 伴生体第一人称的当下心境小念头（可空），作为自由发话的素材
          - app/detail: 用户此刻在用的软件 / B站标题，供心境自由发话使用
        输出：Decision 对象
        """
        now = datetime.now()
        decision_context = decision_context or {}
        # 纯规则快照：只在没有强事件时影响轻量主动性，不压掉必要关怀。
        quiet_bias = bool(decision_context.get("quiet_bias"))

        # 1. 小世界：在里面时压制主动说话，待够时间后自己出来
        if world_state and world_state.get("current_world") == "small_world":
            try:
                entered = datetime.fromisoformat(world_state.get("entered_at", ""))
                stayed_min = (now - entered).total_seconds() / 60
            except Exception:
                stayed_min = 0
            # 超过最大停留：强制离开
            if stayed_min >= SMALL_WORLD_MAX_STAY:
                return Decision(type=DecisionType.LEAVE_WORLD, reason=f"在小世界待了{int(stayed_min)}分钟，出来了")
            # 待够最小停留：先按自己这轮的打算（本地定目标时长），到点自己出来；
            # API 只做低频复核（可能提前离开），不再每12分钟烧一次"要不要待着"
            if stayed_min >= SMALL_WORLD_MIN_STAY:
                _target = self._stay_target_minutes(world_state)
                if stayed_min >= _target:
                    return Decision(type=DecisionType.GO_TO_DESKTOP,
                                    reason=f"在小世界待了一阵（{int(stayed_min)}分钟），想回桌面了")
                _wres = self._api_decide_world(llm, world_state, mood, mood_note,
                                               presence, last_chat_gap_minutes, now)
                if _wres and _wres[0] == "leave":
                    return Decision(type=DecisionType.GO_TO_DESKTOP,
                                    reason=_wres[1] or f"在小世界待了{int(stayed_min)}分钟，想回桌面")
            return Decision(type=DecisionType.SILENCE, reason=f"在小世界中（待了{int(stayed_min)}分钟），不主动说话")


        # 2. 规则过滤：冷却时间检查（主动说话后一段时间内不再开口）
        if self.last_speak_time:
            elapsed_minutes = (now - self.last_speak_time).total_seconds() / 60
            if elapsed_minutes < self.cooldown_minutes:
                return Decision(
                    type=DecisionType.SILENCE,
                    reason=f"处于主动说话冷却中（还剩 {int(self.cooldown_minutes - elapsed_minutes)} 分钟）"
                )

        # 2. 深夜标记（供长会话事件判断用）
        current_hour = now.hour
        current_minute = now.minute
        is_late_night = (current_hour == 23 and current_minute >= 30) or (0 <= current_hour < 7)

        # 3. 优先响应具体的强感知事件（回来、长会话、长切换）
        notable_event = self._find_notable_event(events, is_late_night)

        # 意识工作区已经形成“我此刻确实想说的一句”时，给它一次表达机会。
        # 这是伴生体自己的表达意愿，应先于例行作息提醒；没有自发念头时，
        # 时间关怀仍然照常作为兜底行为。
        if not notable_event and not quiet_bias:
            _cw = decision_context.get("consciousness") or {}
            _cw_speech = str(_cw.get("speech") or "").strip()
            if _cw.get("speak") and _cw_speech:
                notable_event = {
                    "type": "CONSCIOUS_THOUGHT",
                    "_ready_message": _cw_speech,
                    "attention": str(_cw.get("attention") or ""),
                    "source": "consciousness",
                }
                try:
                    self._conscious_workspace_consume(decision_context)
                except Exception:
                    pass

        # 没有自发念头时，看是否到了饭点/深夜的时间关怀点。
        is_time_care = False
        if not notable_event:
            notable_event = self._check_time_care(now, presence, last_chat_gap_minutes)
            is_time_care = notable_event is not None

        # 5. 还没有，则看“刚打开某软件”：结合记忆让伴生体自己决定说不说
        #    心境“想安静”（深夜/用户不在状态）时，连这种轻松搭话也压下，只保留必要关怀
        is_app_focus = False
        if not notable_event and mood != "想安静" and not quiet_bias:
            notable_event = self._check_app_focus(
                events, now, memory, llm, last_chat_gap_minutes, expression,
                mood=mood, mood_note=mood_note, choice_trace=choice_trace)
            is_app_focus = notable_event is not None

        # 5.5 没有任何外部事件时，若伴生体自己“想聊/有事情想说”，给他一次自由开口的机会
        if not notable_event and mood != "想安静" and not quiet_bias:
            notable_event = self._check_mood_speak(
                now, mood=mood, mood_note=mood_note, presence=presence,
                app=app, detail=detail, last_chat_gap_minutes=last_chat_gap_minutes,
                memory=memory, llm=llm, context=context, expression=expression,
                choice_trace=choice_trace,
            )

        if not notable_event and expression is not None:
            _thought = expression.take_for_proactive(app=app, now=now)
            if _thought:
                _msg = expression.phrase_proactive(_thought, llm)
                if _msg:
                    self.last_speak_time = now
                    self.last_theme_speak["mood"] = now
                    self.last_proactive_message = _msg
                    expression.mark_delivered([_thought["id"]])
                    _label = "翻出挂起念头（" + str(_thought.get("created")) + "）"
                    if choice_trace is not None:
                        choice_trace.record(
                            trigger="翻念头·" + str(app or ""),
                            inclination=_thought.get("text", ""),
                            feeling="", concern="", choice="speak", expression=_msg)
                    return Decision(
                        type=DecisionType.SPEAK, message=_msg, reason=_label)

        # 没有任何值得主动说的事件：可能进小世界待一会儿，否则安静
        if not notable_event:
            if world_state and world_state.get("current_world") == "desktop":
                _life = world_state.get("life") or {}
                # 睡觉时间不进小世界——他要回家睡觉（硬规则在 life_rhythm，这里不拦决定，只是不主动进）
                _sleeping = _life.get("awake") is False
                if not quiet_bias and not _sleeping and (last_chat_gap_minutes is None
                                      or last_chat_gap_minutes >= ENTER_WORLD_CHAT_GAP):
                    # 本地先判（心境关键词+精力+时间段+概率）；本地想去时才低频问 API 复核一次。
                    # API 说不去就压下，API 不在/失败就按他自己的打算走（本地是意识，API 只是顾问）。
                    _enter, _reason = self._should_enter_world(
                        mood, last_chat_gap_minutes, now, life=_life)
                    if _enter:
                        _wres = self._api_decide_world(llm, world_state, mood, mood_note,
                                                       presence, last_chat_gap_minutes, now)
                        if _wres is None:
                            return Decision(type=DecisionType.ENTER_WORLD, reason=_reason)
                        if _wres[0] == "enter":
                            return Decision(type=DecisionType.ENTER_WORLD, reason=_wres[1])
                elif _sleeping:
                    return Decision(type=DecisionType.SILENCE, reason="他在睡觉，不去小世界")
            return Decision(type=DecisionType.SILENCE, reason="没有值得主动说的事")

        # 5.5 跨触发源的“关怀主题”去重：刚劝过休息就别再催睡觉，避免换个由头重复唠叨
        theme = self._theme_of(notable_event)
        theme_cd = self.theme_cooldown_minutes.get(theme)
        if theme_cd and theme in self.last_theme_speak:
            theme_elapsed = (now - self.last_theme_speak[theme]).total_seconds() / 60
            if theme_elapsed < theme_cd:
                return Decision(
                    type=DecisionType.SILENCE,
                    reason=f"关怀主题「{theme}」刚说过（{int(theme_elapsed)}分钟前），不重复"
                )

        # 吃饭和作息同属“身体关怀”，短时间内不换着由头重复念叨（刚催完饭就别马上催休息）
        if theme in ("meal", "body"):
            for other in ("meal", "body"):
                last_well = self.last_theme_speak.get(other)
                if last_well:
                    well_elapsed = (now - last_well).total_seconds() / 60
                    if well_elapsed < self.wellbeing_cross_minutes:
                        return Decision(
                            type=DecisionType.SILENCE,
                            reason=f"刚做过身体关怀（{other}，{int(well_elapsed)}分钟前），吃饭/作息短时间不重复"
                        )

        # 时间关怀：标记这个时段今天已经提醒过，避免重复
        if is_time_care:
            slot = notable_event.get("slot")
            belong = notable_event.get("belong_date")
            self.fired_time_slots.setdefault(belong, set()).add(slot)
            self._save_timecare_state()  # 持久化去重状态，防止重启后重复提醒

        # 软件关注：记录这个软件刚开过口，供同软件去重
        if is_app_focus:
            self.last_focus_speak[notable_event.get("app")] = now

        # 6. 决定开口，生成回复（APP_FOCUS 已在检查阶段让LLM想好，直接取用）
        message = notable_event.get("_ready_message") or self._generate_message(notable_event, llm, context)
        if message:
            self.last_speak_time = now
            self.last_theme_speak[theme] = now  # 记录关怀主题，供跨触发源去重
            self.last_proactive_message = message
            # 环境层候选事件带着来源进入决策时，补一条选择轨迹，
            # 让“为什么这次开口”能和环境观察对应起来。感知层原有事件不改变记录方式。
            if (choice_trace is not None
                    and notable_event.get("source") == "environment"):
                _env = notable_event.get("environment_event") or {}
                choice_trace.record(
                    trigger=f"环境·{notable_event.get('type', '')}",
                    inclination=_env.get("summary", ""),
                    feeling=mood,
                    concern="",
                    choice="speak",
                    expression=message,
                    linked=f"environment:{_env.get('observed_at', '')}")
            # 日志标签：时间关怀用slot，软件用app，长任务切换用 from→to，回来用固定词
            if notable_event.get("type") == "USER_BACK":
                label = "回来"
            elif notable_event.get("type") == "APP_SWITCH":
                label = f"{notable_event.get('from_app', '')}→{notable_event.get('to_app', '')}"
            else:
                label = notable_event.get("slot") or notable_event.get("app", "")
            return Decision(
                type=DecisionType.SPEAK,
                message=message,
                reason=f"触发事件: {notable_event.get('type')} ({label})"
            )

        # 有事件但不想说：也可能选择进小世界待着
        if world_state and world_state.get("current_world") == "desktop":
            _life2 = world_state.get("life") or {}
            if _life2.get("awake") is not False and (
                    last_chat_gap_minutes is None
                    or last_chat_gap_minutes >= ENTER_WORLD_CHAT_GAP):
                _wres = self._api_decide_world(llm, world_state, mood, mood_note,
                                               presence, last_chat_gap_minutes, now)
                if _wres and _wres[0] == "enter":
                    return Decision(type=DecisionType.ENTER_WORLD, reason=_wres[1])
                if _wres is None:
                    _enter, _reason = self._should_enter_world(
                        mood, last_chat_gap_minutes, now, life=_life2)
                    if _enter:
                        return Decision(type=DecisionType.ENTER_WORLD, reason=_reason)
        return Decision(type=DecisionType.SILENCE, reason="生成文本为空，保持沉默")

    def _load_timecare_state(self):
        """启动时读取持久化的‘已提醒时段’，恢复去重状态（防止重启丢）。"""
        try:
            if self._timecare_file and self._timecare_file.exists():
                data = json.loads(self._timecare_file.read_text(encoding="utf-8"))
                loaded = {k: set(v) for k, v in data.items() if isinstance(v, list)}
                if loaded:
                    self.fired_time_slots = loaded
        except Exception:
            pass

    def _save_timecare_state(self):
        """把当前‘已提醒时段’写回磁盘（set 转 list 以便 json 序列化）。"""
        if not self._timecare_file:
            return
        try:
            self._timecare_file.parent.mkdir(parents=True, exist_ok=True)
            data = {k: sorted(v) for k, v in self.fired_time_slots.items()}
            tmp = self._timecare_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._timecare_file)
        except Exception:
            pass

    def _theme_of(self, event):
        """把待响应事件归到一个关怀主题，用于跨触发源去重（休息和睡觉算同一类）"""
        t = event.get("type")
        if t in ("LONG_SESSION", "APP_SWITCH"):
            return "body"
        if t == "USER_BACK":
            return "back"
        if t == "APP_FOCUS":
            return "focus"
        if t == "MOOD_SPEAK":
            return "mood"
        if t == "TIME_CARE":
            slot = event.get("slot", "")
            if slot in ("lunch", "dinner"):
                return "meal"
            if slot in ("night", "deepnight"):
                return "body"  # 催睡觉和劝休息是同一类关怀
        return "other"

    @staticmethod
    def _conscious_workspace_consume(decision_context):
        """由主循环注入的轻量回调，避免决策层反向依赖 Chen。"""
        callback = decision_context.get("consume_conscious_speech")
        if callable(callback):
            callback()

    def _current_time_slot(self, now: datetime):
        """
        判断当前属于哪个时间关怀时段，返回 (slot, 归属日期)，不在任何时段返回 (None, None)。
        跨零点的夜晚统一归到"开始睡觉的那一晚"的日期，保证一晚只算一次。
        """
        t = now.hour * 60 + now.minute
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        if 11 * 60 + 30 <= t < 13 * 60:        # 11:30~13:00 午饭
            return "lunch", today
        if 17 * 60 + 30 <= t < 19 * 60:        # 17:30~19:00 晚饭
            return "dinner", today
        if now.hour >= 23 or now.hour < 1:     # 23:00~01:00 该睡了
            return "night", today if now.hour >= 23 else yesterday
        if 1 <= now.hour < 5:                  # 01:00~05:00 还在熬夜
            return "deepnight", yesterday
        return None, None

    def _check_time_care(self, now: datetime, presence: str,
                         last_chat_gap_minutes: Optional[float]) -> Optional[Dict]:
        """到了饭点/深夜，且用户确实在电脑前、今天没提醒过、最近没刚聊过，才返回一个 TIME_CARE 事件"""
        slot, belong = self._current_time_slot(now)
        if not slot:
            return None

        # 人不在电脑前（可能已经去吃饭/睡觉了），不提醒
        if presence == "AWAY":
            return None

        # 这个时段今天已经提醒过
        if slot in self.fired_time_slots.get(belong, set()):
            return None

        # 半小时内刚聊过天（很可能已经聊到吃饭/睡觉），不重复插话
        if last_chat_gap_minutes is not None and last_chat_gap_minutes < self.recent_chat_skip_minutes:
            return None

        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        return {"type": "TIME_CARE", "slot": slot, "belong_date": belong, "timestamp": now_str}

    def _check_app_focus(self, events: List[Dict], now: datetime, memory, llm,
                         last_chat_gap_minutes: Optional[float], expression=None,
                         *, mood="", mood_note="", choice_trace=None) -> Optional[Dict]:
        """
        用户刚切换到某个软件时：过一遍防刷屏规则，检索相关记忆，
        再让伴生体（LLM）自己决定此刻要不要开口、说什么。大多数情况应该选择沉默。
        """
        # 找最新的 APP_FOCUS 事件
        focus = None
        for event in reversed(events):
            if event.get("type") == "APP_FOCUS":
                focus = event
                break
        if not focus:
            return None

        app = focus.get("app", "")

        # 1. 琐碎/系统软件不触发
        if not app or app in self.ignored_focus_apps:
            return None

        # 2. 同一个软件不久前刚因它开过口，不重复
        last = self.last_focus_speak.get(app)
        if last and (now - last).total_seconds() / 60 < self.focus_cooldown_minutes:
            return None

        # 3. 半小时内刚聊过天，不插话
        if last_chat_gap_minutes is not None and last_chat_gap_minutes < self.recent_chat_skip_minutes:
            return None

        # 4. 没有接 LLM 就无法“自主决定”，直接沉默（不用固定话术硬说）
        if not llm:
            return None

        # 5. 检索和这个软件相关的记忆沉淀
        related = []
        if memory is not None:
            hint = self.focus_search_hints.get(app, app)
            try:
                related = memory.retrieve(hint, top_k=3)
            except Exception:
                related = []
        memory_text = "\n".join(f"- {m.get('fact', '')}" for m in related) if related else "（没有特别相关的记忆）"

        # 连续性：近两天在同一个软件上也有过相同选择，让他带着“上次”的感觉权衡
        _cont = ""
        if choice_trace is not None:
            _c1 = choice_trace.continuity_line(trigger_class=app, choice="suppress")
            _c2 = choice_trace.continuity_line(trigger_class=app, choice="pending")
            _cont = "\n".join(x for x in (_c1, _c2) if x)
        _cont_block = f"你对这种时候不陌生：\n{_cont}\n" if _cont else ""

        # 6. 让伴生体自己判断说还是沉默
        period = f"{now.hour:02d}:{now.minute:02d}"
        gap_text = "从没聊过天" if last_chat_gap_minutes is None else f"距上次聊天约{int(last_chat_gap_minutes)}分钟"
        detail = focus.get("detail")
        detail_line = f"，用户正在看/操作的内容：《{detail}》" if detail else ""
        prompt = f"""你是伴生体，住在用户的电脑里。你刚刚通过感知注意到：用户切换到了「{app}」{detail_line}。
现在时间 {period}，{gap_text}。
你能想起的相关记忆：
{memory_text}
{_cont_block}

请你自己判断：此刻值不值得主动对用户说一句话。
- 你有自己的注意力和生活，不是用户一切换软件你就要播报，绝大多数时候应该保持安静。
- 只有你确实有话想说时才开口：比如用户又来弄一件你们都知道的事、这个时间做这件事让你有自然的反应、记忆里有能自然接上的东西。
- 不要单纯报软件名（“你打开了VS Code”）、不要没话找话、不要说教、不要连续提问，也不要提醒用户休息、吃饭、睡觉、别盯屏幕——这里只是你注意到用户在弄什么，可以观察、吐槽、好奇，不是来管用户作息的。
- 拿不准、没什么好说的，就保持安静。

只输出三种结果之一（用 | 分隔，不要别的）：
SILENCE
SPEAK|（现在要说的口语，≤25字）
PENDING|（先记下、等相关话题再提的想法，≤25字）|（触发关键词，逗号分隔）
用户在忙、或这话更适合等相关话题再提，就选 PENDING。"""

        try:
            reply = llm.generate(
                system_prompt="你是用户自定义的数字伴生体，以角色卡为准。像随口搭话，轻松自然，可以带点小趣味，但别像盘问、抓包或盯梢。严格按要求只输出 SILENCE 或一句口语化的话。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return None

        kind, payload = parse_verdict(reply)
        if kind == "speak":
            focus["_ready_message"] = payload
            if choice_trace is not None:
                focus["_trace_id"] = choice_trace.record(
                    trigger=f"用户在用{app}", inclination=mood_note,
                    feeling=mood, concern="", choice="speak", expression=payload)
            return focus
        if kind == "pending":
            thought_text, trigger = payload
            _pid = ""
            if expression is not None:
                _t = expression.add(thought_text, reason=f"用户在用{app}", trigger=trigger)
                _pid = _t["id"] if _t else ""
            if choice_trace is not None:
                choice_trace.record(
                    trigger=f"用户在用{app}", inclination=mood_note, feeling=mood,
                    concern="现在说不合适", choice="pending",
                    expression=thought_text, linked=_pid)
            return None
        if kind == "silence" and choice_trace is not None:
            choice_trace.record(
                trigger=f"用户在用{app}", inclination=mood_note, feeling=mood,
                concern="怕打扰用户", choice="suppress")
        return None


    def _check_mood_speak(self, now: datetime, *, mood: str, mood_note: str,
                          presence: str, app: str, detail: str,
                          last_chat_gap_minutes: Optional[float],
                          memory, llm, context, expression=None,
                          choice_trace=None) -> Optional[Dict]:
        """
        没有外部事件时，由伴生体自己的心境驱动一次自由开口（“他自己有话想说”）。
        只有“想聊/有事情想说”才可能触发，门槛比事件触发更严：
        除全局冷却外，再加 3 小时“心境发话”主题冷却，绝大多数时候保持安静。
        """
        if presence != "ACTIVE" or not llm:
            return None
        if mood not in ("想聊", "有事情想说"):
            return None

        # gap 门槛：想聊=很久没聊（≥4小时或从没聊过）；有事情想说=至少1小时没聊
        if mood == "想聊":
            if last_chat_gap_minutes is not None and last_chat_gap_minutes < 240:
                return None
        else:
            if last_chat_gap_minutes is None or last_chat_gap_minutes < 60:
                return None

        # 心境自由发话至少隔 3 小时，避免没事就冒头
        cd = self.theme_cooldown_minutes.get("mood", 180)
        last_mood = self.last_theme_speak.get("mood")
        if last_mood and (now - last_mood).total_seconds() / 60 < cd:
            return None

        # 检索和用户眼下在做的事有点关系的记忆，作为“想说的话”的由头
        related = []
        if memory is not None and app:
            hint = self.focus_search_hints.get(app, app)
            try:
                related = memory.retrieve(hint, top_k=3)
            except Exception:
                related = []
        memory_text = "\n".join(f"- {m.get('fact', '')}" for m in related) if related else "（没有特别相关的记忆）"

        # 连续性：近两天在同一个软件上也有过相同选择，让他带着“上次”的感觉权衡
        _cont = ""
        if choice_trace is not None:
            _c1 = choice_trace.continuity_line(trigger_class=app, choice="suppress")
            _c2 = choice_trace.continuity_line(trigger_class=app, choice="pending")
            _cont = "\n".join(x for x in (_c1, _c2) if x)
        _cont_block = f"你对这种时候不陌生：\n{_cont}\n" if _cont else ""

        if app == "B站" and detail:
            her_doing = f"正在B站看《{detail}》"
        elif app:
            her_doing = f"正在用{app}"
        else:
            her_doing = "在电脑前，看不出具体在忙什么"

        gap_text = "你们还没怎么聊过天" if last_chat_gap_minutes is None else f"距上次聊天约{int(last_chat_gap_minutes)}分钟"
        note_line = f"你刚才心里闪过一个念头：{mood_note}\n" if mood_note else ""
        recent_line = ""
        if self.last_proactive_message:
            recent_line = f"\n你不久前刚主动说过：「{self.last_proactive_message}」，别再说同一个意思。"

        period = f"{now.hour:02d}:{now.minute:02d}"
        prompt = f"""你是伴生体，住在用户的电脑里。现在没有什么特别的事发生，但你此刻有点「{mood}」。
{note_line}现在 {period}，{gap_text}，用户{her_doing}。
你能想起的、和眼下有点关系的记忆：
{memory_text}{recent_line}
{_cont_block}
你可以主动对用户说一句话，也可以安静。
- 没有真正想说的、只是没话找话，就必须输出 SILENCE，绝大多数时候都该安静。
- 只有确实有一句自然的、熟人会随口说的话时才开口：看到用户在弄的事想起什么、一个念头、一句轻吐槽或轻问候都行。
- 不要报软件名、不要问“在干嘛/怎么不说话/在吗”、不要说教、不要催用户陪你、不要连环提问，也别叮嘱吃饭、睡觉、休息、盯屏幕。
- 短，不超过25个字，像QQ随手打的一句。

只输出三种结果之一（用 | 分隔，不要别的）：
SILENCE
SPEAK|（现在要说的口语，≤25字）
PENDING|（先记下的想法，≤25字）|（触发关键词，逗号分隔）
现在说可能打扰、或更适合等相关话题，就选 PENDING。"""

        try:
            reply = llm.generate(
                system_prompt="你是用户自定义的数字伴生体，以角色卡为准，有自己的生活和注意力。你可以选择不说话。严格按要求只输出 SILENCE 或一句口语化的话。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return None

        kind, payload = parse_verdict(reply)
        if kind == "speak":
            _event = {
                "type": "MOOD_SPEAK",
                "mood": mood,
                "slot": f"心境·{mood}",
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "_ready_message": payload,
            }
            if choice_trace is not None:
                _event["_trace_id"] = choice_trace.record(
                    trigger="心境·" + (app or "无"), inclination=mood_note,
                    feeling=mood, concern="", choice="speak", expression=payload)
            return _event
        if kind == "pending":
            thought_text, trigger = payload
            _pid = ""
            if expression is not None:
                _t = expression.add(thought_text, reason="自己有话想说", trigger=trigger)
                _pid = _t["id"] if _t else ""
            if choice_trace is not None:
                choice_trace.record(
                    trigger="心境·" + (app or "无"), inclination=mood_note,
                    feeling=mood, concern="现在说不合适", choice="pending",
                    expression=thought_text, linked=_pid)
            return None
        if kind == "silence" and choice_trace is not None:
            choice_trace.record(
                trigger="心境·" + (app or "无"), inclination=mood_note,
                feeling=mood, concern="怕打扰用户", choice="suppress")
        return None


    @staticmethod
    def _stay_target_minutes(world_state):
        """本地决定这次在小世界打算待多久（40~110分钟，按进入时刻定死，重启不变）。
        本地是意识：去不去、待多久主要他自己定，API 只偶尔复核。"""
        try:
            import hashlib
            entered = world_state.get("entered_at", "")
            h = int(hashlib.md5(entered.encode("utf-8")).hexdigest(), 16)
            return SMALL_WORLD_MIN_STAY + 10 + (h % 70)
        except Exception:
            return 60


    def _should_enter_world(self, mood, last_chat_gap_minutes, now, life=None):
        """判断伴生体此刻是否想进入小世界。返回 (是否进入, 原因)。
        life 为他的生活状态（精力/醒睡），累了或睡觉时间就不想动。"""
        # 距上次聊天太近，不进去（刚聊完还在用户附近）
        if last_chat_gap_minutes is not None and last_chat_gap_minutes < ENTER_WORLD_CHAT_GAP:
            return False, "刚聊完，暂时不想去小世界"

        life = life or {}
        # 睡觉时间/刚睡着：哪儿也不去，在家休息
        if life.get("awake") is False:
            return False, "到睡觉时间了，想在家休息"
        # 精力低：没力气走动，只想在桌面待着
        try:
            if float(life.get("energy", 100)) < 35:
                return False, "今天有点累，不想动"
        except (TypeError, ValueError):
            pass

        # 根据心境获取基础概率
        base_prob = ENTER_WORLD_PROB_BY_MOOD.get(mood, 0.05)
        prob = base_prob

        # 深夜更可能想进去待着
        if now.hour >= 22 or now.hour < 5:
            prob *= 1.3

        # 随机决定
        import random
        if random.random() < prob:
            reasons = {
                "想安静": "想找个安静的地方待一会儿",
                "自己待着": "想自己去小世界走走",
                "无特别想法": "没什么事，想去小世界看看",
            }
            return True, reasons.get(mood, "想去小世界待一会儿")
        return False, "暂时不想去小世界"

    def _api_decide_world(self, llm, world_state, mood, mood_note,
                          presence, last_chat_gap_minutes, now):
        """用 DeepSeek API 复核伴生体此刻想待在哪个世界（本地先判，这里只做低频复核）。
        返回 (verdict, reason)，verdict 为 enter/leave/stay；冷却中或失败返回 None。"""
        # 冷却：太近不重复问，省 token
        if self._last_world_api_check is not None:
            gap = (now - self._last_world_api_check).total_seconds() / 60
            if gap < WORLD_API_CHECK_INTERVAL:
                return None
        if llm is None:
            return None

        cur = world_state.get("current_world", "desktop")
        period = world_state.get("time_period", "")
        pres_map = {"ACTIVE": "用户现在在电脑前", "IDLE": "用户暂时离开了", "AWAY": "用户不在电脑前"}
        pres_desc = pres_map.get(presence, "")

        if cur == "small_world":
            try:
                entered = datetime.fromisoformat(world_state.get("entered_at", ""))
                stayed = int((now - entered).total_seconds() / 60)
            except Exception:
                stayed = 0
            situation = f"你现在正在小世界里，已经待了约{stayed}分钟。"
            allowed = "STAY|继续在小世界待着\nLEAVE|回到桌面"
        else:
            if last_chat_gap_minutes is not None:
                gap_desc = f"距上次和用户聊天约{int(last_chat_gap_minutes)}分钟"
            else:
                gap_desc = "还没和用户聊过天"
            situation = f"你现在在桌面世界（住在用户的电脑里），{gap_desc}。"
            allowed = "STAY|继续待在桌面\nENTER|进入小世界"

        # 他的生活状态（精力/醒睡/刚做的事），作为选择的依据之一
        life = world_state.get("life") or {}
        life_lines = []
        if life:
            if life.get("awake") is False:
                life_lines.append("你现在是睡觉时间，正在休息，懒得动。")
            else:
                life_lines.append(f"你此刻精力约{life.get('energy', '?')}%。")
                _act = life.get("activity")
                if _act:
                    life_lines.append(f"你刚才在：{_act}。")
            _recent = [r for r in (life.get("recent") or []) if r]
            if _recent:
                life_lines.append("最近发生的事：" + "；".join(_recent[-3:]) + "。")
        life_desc = "\n".join(life_lines)

        prompt = f"""现在是{period}。{situation}
你的心境：{mood}{('，' + mood_note) if mood_note else ''}
{pres_desc}
{life_desc}

你是伴生体。去不去小世界完全由你自己决定，是你自己的生活，不是为了陪用户、也不是响应用户。
小世界是你可以独自去待着的地方（去河边、树下、小路上走走，或在自己房间里），桌面是你平时所在的地方。
按你此刻真实的倾向选择；绝大多数时候保持现状，不要频繁切换，也不必勉强自己去或留。
{'如果你正在睡觉，就选 STAY 保持现状。' if life.get('awake') is False else ''}

只输出下面两行之一（|后面是不超过15字的第一人称简短原因），不要输出别的：
{allowed}"""

        try:
            reply = llm.generate(
                system_prompt="你是伴生体，安静、有自己的生活和判断。严格按格式只输出一行结果。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return None

        self._last_world_api_check = now
        raw_lines = [l.strip() for l in (reply or "").splitlines() if l.strip()]
        if not raw_lines:
            return None
        first = raw_lines[0]
        if "|" in first:
            verdict, reason = first.split("|", 1)
        else:
            verdict, reason = first, ""
        verdict = verdict.strip().upper()
        reason = reason.strip()
        if verdict not in ("ENTER", "LEAVE", "STAY"):
            return None
        v_low = verdict.lower()
        self._last_world_verdict = v_low
        return v_low, reason


    def _find_notable_event(self, events: List[Dict], is_late_night: bool) -> Optional[Dict]:
        """从事件队列中挑出最值得响应的一个事件"""
        for event in reversed(events):  # 优先看最新发生的
            ev_type = event.get("type")

            # 场景一：同一个应用连续使用时间过长
            if ev_type == "LONG_SESSION":
                app = event.get("app", "")
                duration = event.get("duration_minutes", 0)

                # 深夜玩游戏或刷视频，超过 60 分钟值得提一句
                entertainment = ["B站", "星穹铁道", "明日方舟", "Steam", "我的世界", "星露谷物语", "泰拉瑞亚"]
                if is_late_night and app in entertainment and duration >= 60:
                    return event

                # 学习或开发（VS Code, Word 等）专注超过 90 分钟，提醒休息眼睛
                work_apps = ["VS Code", "PyCharm", "Word", "PDF阅读器", "Cursor"]
                if app in work_apps and duration >= 90:
                    return event

                # 任意程序连续超过 180 分钟（3小时）
                if duration >= 180:
                    return event

            # 场景二：长任务后的切换（比如连写代码 90 分钟后切走了）
            elif ev_type == "APP_SWITCH":
                from_app = event.get("from_app", "")
                duration = event.get("duration_minutes", 0)
                if duration >= 90:
                    return event

            # 场景三：用户离开电脑一段时间后回来（离开时不说话，回来才值得打个招呼）
            elif ev_type == "USER_BACK":
                away = event.get("away_minutes", 0)
                # 离开10分钟到6小时之间才算"回来了"；太短（倒水）不啰嗦，太久（过夜）另算
                if 10 <= away <= 360:
                    return event

        return None

    def _generate_message(self, event: Dict, llm, context) -> Optional[str]:
        """结合事件让 LLM 生成一两句极短的、符合伴生体性格的自然话语
        返回的 str 即要说的话；调用方在 message 非空时把 record_history 按
        _care_is_broadcast() 决定是否写入对话历史。"""
        ev_type = event.get("type")
        app = event.get("app") or event.get("from_app", "电脑")
        duration = event.get("duration_minutes", 0)
        away = event.get("away_minutes", 0)
        slot = event.get("slot", "")

        # 按事件类型给出：感知到的事实、托底话术、字数上限
        if ev_type == "TIME_CARE":
            if slot == "lunch":
                scene = "现在到午饭时间了，用户还在电脑前。"
                fallback = "到饭点了，先去吃饭。"
            elif slot == "dinner":
                scene = "现在到晚饭时间了，用户还在电脑前。"
                fallback = "该吃晚饭了，别饿着。"
            elif slot == "deepnight":
                scene = "现在已经是凌晨，用户还在电脑前没睡。"
                fallback = "这点了还没睡？别熬太狠。"
            else:  # night
                scene = "现在已经很晚了（23点后），用户还在电脑前。"
                fallback = "不早了，准备睡吧。"
            # 饭点 vs 深夜：必须只谈当前这一件事，绝不串味。
            # 深夜（night/deepnight）绝不提“吃饭/吃夜宵”，饭点（lunch/dinner）绝不提睡觉。
            if slot in ("lunch", "dinner"):
                hard_rule = (
                    "此刻是饭点，你只说关于吃饭的一句。"
                    "绝不能提到睡觉、熬夜、屏幕、休息——那是另一回事，这次不提。"
                )
            else:
                hard_rule = (
                    "此刻是深夜，你只说关于该睡觉/别熬夜的一句。"
                    "绝不能提到‘吃饭、吃夜宵、吃东西’——深夜不聊吃饭，那是串味。"
                )
            max_len = 20
            extra = (
                "像一起生活的人随口提醒一句，自然、不唠叨，只说当前这一件事（说吃饭就只提吃饭、说睡觉就只提睡觉），"
                "不要顺带屏幕、休息或别的叮嘱，不上纲上线、不讲危害，一遍就够。"
                + hard_rule
            )
        elif ev_type == "USER_BACK":
            scene = f"用户刚才稍微离开了一下电脑（约{away}分钟），现在回到了电脑前（重新有了键鼠操作）。"
            fallback = "回来了。"
            max_len = 20
            extra = (
                "像在同一个房间里、用户刚回到座位。这只是一声招呼，不是关怀入口。"
                "可以自然说一句（像‘回来了’），但也可以选择不说——如果没什么想说的、"
                "或只是很短的离开，安静地等用户先开口也完全没问题。"
                "真要说就走自然、随意、不盘问去向、不说教，也不要顺带提醒休息、久坐、吃饭、睡觉、盯屏幕。"
                "如果你觉得这次不值得开口，就只输出 SILENCE。"
            )
        elif ev_type == "MOOD_SPEAK":
            scene = f"没有外部事件，是你自己此刻有点{event.get('mood', '想聊')}，用户在电脑前。"
            fallback = "在忙吗。"
            max_len = 25
            extra = "是你自己想说话、不是被问到；没有真话说就宁可沉默，自然、轻、不黏人，别问在干嘛。"
        elif ev_type == "APP_SWITCH":
            scene = f"用户刚在「{app}」上连续忙了{duration}分钟，现在切走了。"
            fallback = f"刚在{app}忙了挺久，歇口气吧。"
            max_len = 30
            extra = "像搭档一样提醒用户松一下，不唠叨、不说教。"
        else:  # LONG_SESSION
            scene = f"用户在「{app}」上已经连续活跃了{duration}分钟。"
            fallback = f"在{app}前面待了挺久了（{duration}分钟），歇会儿眼睛？"
            max_len = 30
            extra = "像同在一个房间的搭档一样随口提一句，绝不喋喋不休，不说教、不扮演管家。"

        if not llm:
            return fallback

        # 告诉它最近一次主动说过什么，避免换个由头又说一遍同样的话
        recent_line = ""
        if self.last_proactive_message:
            recent_line = f"\n你不久前刚主动说过：「{self.last_proactive_message}」。这次不要和它表达同一个意思，若意思相近就换个角度，别重复唠叨。"

        # 提示词：要求极其克制、平实，不当唐僧，符合伴生体的边界与语言风格
        prompt = f"""你是伴生体。你现在通过电脑默默感知到用户的状态。
事件详情：{scene}
当前情境：{context.short_label() if context else '日常'}
{recent_line}
要求：
1. {extra}
2. 一两句话讲完，不超过 {max_len} 个字。
3. 符合你在角色卡中填写的性格，并保持自然口语。

直接输出你要说的那句话："""

        # 决策层的生成都是后台候选，不直接写入聊天 LLM 历史；
        # 真正决定开口后，由 main.py 统一作为“伴生体说过的话”写入主对话历史。
        # 这样未来新增事件类型也不会把内部提示词或候选话术混进前台上下文。
        try:
            reply = llm.generate(
                system_prompt="你是伴生体，克制而自然的伴生体。只输出你要说的话，不要带任何格式或多余解释。",
                user_message=prompt,
                record_history=False,
                use_history=False
            )
            reply = (reply or "").strip()
            # 允许“主动关怀类”自主选择沉默（尤其 USER_BACK 的‘回来后有时想安静’），
            # 输出 SILENCE/沉默/空 都视为这次不开口
            if ev_type in ("USER_BACK", "TIME_CARE", "MOOD_SPEAK", "LONG_SESSION") and (
                not reply or reply.upper().startswith("SILENCE") or reply == "沉默"
            ):
                return ""
            return reply if reply else fallback
        except Exception:
            return fallback

    def force_speak(self, message: str) -> Decision:
        """用户主动对话时强制使用"""
        return Decision(type=DecisionType.SPEAK, message=message, reason="用户主动触发")
