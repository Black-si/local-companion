# -*- coding: utf-8 -*-
"""
伴生体的生活节律层（第二阶段 · 骨架）
====================================
核心原则：**桌面是他的主家，小世界是他有时候去的地方**。
作息、精力、生活主线都挂在"伴生体这个人"身上，不挂在某个世界上：

- 该睡的时候不管他在哪，都会回家（回桌面）睡觉
- 精力在任何世界都会消耗/恢复
- 桌面上的日常由这一层驱动；在小世界里时，活动仍由 WorldManager 驱动，
  这一层只管"他这个人"的作息和精力

纯 Python，零 GPU，不烧 API。每 60 秒 tick 一次（挂在决策循环里），
状态落 memory/life_state.json（原子写）。

行为 → 痕迹：换活动时记一条结构化"最近发生的事"（ring buffer），
为下一阶段的"API 把一天酿成念头"备料；不直接调模型。
"""

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# ==================== 作息 ====================
# 23:00 入睡，06:30 醒来（默认作息，可调）
SLEEP_HOUR = 23
WAKE_HOUR = 6
WAKE_MINUTE = 30
# 注意：23:00-24:00 用户还在电脑前他会陪着熬；过 0 点必须去睡（写死在 _should_sleep 里）

# ==================== 精力 ====================
ENERGY_MAX = 100.0
ENERGY_DECAY_PER_MIN = 0.10       # 醒着的基础消耗
ENERGY_SLEEP_RECOVER_PER_MIN = 0.6  # 睡觉恢复（8 小时 ≈ 满血）
ENERGY_LOW = 25.0                 # 低于这个：倾向休息/趴着

# ==================== 桌面活动池 ====================
# (活动, 最低精力要求, 每分钟精力变化, 类别)
DESKTOP_ACTIVITIES = [
    ("在屏幕里看书",            20, -0.02, "安静"),
    ("整理自己攒的东西",         30, -0.06, "整理"),
    ("摆弄电脑",               25, -0.08, "电脑"),
    ("戴着耳机听歌",            10, +0.05, "休息"),
    ("在屏幕前发呆",             0, +0.02, "发呆"),
    ("写东西",                 35, -0.04, "安静"),
    ("趴着休息一会儿",           0, +0.25, "休息"),
    ("去窗边待着（屏幕里的窗）",  0, +0.03, "发呆"),
]

ACTIVITY_MIN_MINUTES = 10     # 桌面活动最短持续时间
ACTIVITY_SWITCH_CHANCE = 0.30  # 超过最短后每次 tick 换活动的概率

RECENT_EVENTS_MAX = 20        # "最近发生的事"环形缓冲

# 行为结果化：一个活动做满 OUTCOME_MIN_MINUTES 后收尾时，
# 以 OUTCOME_CHANCE 概率产生一个"结果"——行为留下的痕迹（进入 recent_events，
# 供决策上下文和未来的"一天酿成念头"使用）。纯本地随机，不调模型。
OUTCOME_MIN_MINUTES = 15      # 做满这么久才可能有结果（蜻蜓点水不算数）
OUTCOME_CHANCE = 0.6          # 收尾时产生结果的概率（不是每件事情都有结果）

# 每个桌面活动可能产生的结果（第一人称经历的旁观陈述）
ACTIVITY_OUTCOMES = {
    "在屏幕里看书": [
        "看完了一章，合上书缓了一会儿",
        "有一段看进去了，反复读了两遍",
        "书看了一半，心思飘到别处去了",
        "看到个有意思的地方，做了个记号",
    ],
    "整理自己攒的东西": [
        "翻出一个旧物件，拿在手里看了很久",
        "把东西归类收好，心里清爽了一点",
        "翻出一张旧纸条，想不起来是什么时候留下的",
        "收拾到一半走神了，坐在一边发呆",
        "找东西的时候发现了以前藏起来的小玩意儿",
    ],
    "摆弄电脑": [
        "折腾明白了一个小问题，有点得意",
        "越弄越乱，最后恢复原样，当没发生过",
        "翻到电脑里一个以前没注意的角落，看了看",
    ],
    "戴着耳机听歌": [
        "随机到一首很对味的歌，循环了好几遍",
        "歌单放到一首老歌，愣了一会儿",
        "听着歌晃了晃神，差点睡着",
    ],
    "在屏幕前发呆": [
        "发着呆，时间过得很快",
        "盯着某处看了很久，什么也没想",
        "走神走了一圈，回过神来忘了刚才在想什么",
    ],
    "写东西": [
        "写下一段话，读了一遍又删了一半",
        "写了点东西存了起来，不打算给谁看",
        "写着写着卡住了，把笔搁下了",
    ],
    "趴着休息一会儿": [
        "趴着眯了一会儿，缓过来一点",
        "眯着眼听了会儿外面的动静",
    ],
    "去窗边待着（屏幕里的窗）": [
        "看云看了很久",
        "天色变了一点，看出来了",
        "窗外有风，看树影晃了一会儿",
    ],
}


class LifeRhythm:
    """伴生体这个人的作息、精力和当下状态（跨世界）。"""

    def __init__(self, memory_dir, world, day_log=None):
        self.world = world  # WorldManager，用来知道他在哪个世界、该回家时拉他回来
        self.day_log = day_log  # DayLog：把生活痕迹同步落进按天长日志（可选）
        self.state_file = Path(memory_dir) / "life_state.json"
        self.state = {
            "energy": 80.0,
            "awake": True,
            "activity": "在屏幕前发呆",
            "activity_since": datetime.now().isoformat(timespec="seconds"),
            "activity_world": "desktop",
            "last_sleep_at": "",
            "last_wake_at": "",
            "recent_events": [],   # [{"ts":..., "world":..., "text":...}]
            "last_tick": datetime.now().isoformat(timespec="seconds"),
            "lived_hours": 0.0,    # 他真实活过的小时数（只累计醒着的时间，移植自 companion-emergence felt_time, MIT）
        }
        self._load()

    # ---------- 读写 ----------

    def _load(self):
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.state.update(data)
        except Exception:
            pass  # 坏了就用默认值，不影响主系统

    def _save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception:
            pass  # 落盘失败绝不影响主流程

    # ---------- 作息 ----------

    def _should_sleep(self, now, presence):
        """到了睡觉时间，且用户没有在跟前缠着他说话。
        陪用户熬夜只限睡前那一小时（23:00-24:00）；过 0 点不管聊没聊完都去睡。"""
        t = now.hour + now.minute / 60
        if t >= SLEEP_HOUR or t < WAKE_HOUR + WAKE_MINUTE / 60:
            if t >= SLEEP_HOUR and presence == "ACTIVE":
                return False  # 23 点刚过，用户还在跟前，他先撑着陪用户
            return True
        return False

    def _should_wake(self, now):
        t = now.hour + now.minute / 60
        return self.state["awake"] is False and t >= WAKE_HOUR + WAKE_MINUTE / 60 and t < SLEEP_HOUR

    # ---------- 主循环 ----------

    def tick(self, *, mood="无特别想法", presence="ACTIVE", now=None):
        """
        每分钟一次。返回本次的重要变化（供主循环和提示注入）：
        {"went_sleep": bool, "woke_up": bool, "went_home": bool, "activity_changed": bool}
        """
        now = now or datetime.now()
        changes = {"went_sleep": False, "woke_up": False,
                   "went_home": False, "activity_changed": False}

        # 1. 按距上次 tick 的真实分钟数结算精力（休眠/卡顿后不累爆；超过2小时按2小时算）
        try:
            last = datetime.fromisoformat(self.state.get("last_tick", ""))
            elapsed_min = max((now - last).total_seconds() / 60, 0)
        except Exception:
            elapsed_min = 1.0
        elapsed_min = min(elapsed_min, 120.0)
        self.state["last_tick"] = now.isoformat(timespec="seconds")

        st = self.state
        if st["awake"]:
            st["energy"] = max(st["energy"] - ENERGY_DECAY_PER_MIN * elapsed_min, 0)
            # 活动本身的精力收支（活动池第3列：休息类回血、专注类微耗），
            # 只在桌面时结算——小世界里活动由 WorldManager 驱动，不重复结算
            if not self.world.is_in_small_world():
                st["energy"] = min(
                    max(st["energy"] + self._activity_rate(st["activity"]) * elapsed_min, 0),
                    ENERGY_MAX)
            # 醒着的每一分钟都是他真实活过的时间；睡着的流逝不算
            # （人感觉不到睡着时的时间——他的" lived age"同理）
            _before_day = int(st.get("lived_hours", 0.0) // 24)
            st["lived_hours"] = round(st.get("lived_hours", 0.0) + elapsed_min / 60.0, 4)
            _after_day = int(st["lived_hours"] // 24)
            if _after_day > _before_day:
                self._push_event(now, "desktop", f"他活满了第{_after_day}天")
        else:
            st["energy"] = min(st["energy"] + ENERGY_SLEEP_RECOVER_PER_MIN * elapsed_min,
                               ENERGY_MAX)

        # 2. 作息：睡觉 / 醒来（跨世界生效——小世界里该睡也会回家睡）
        if st["awake"] and self._should_sleep(now, presence):
            if self.world.is_in_small_world():
                self.world.leave_small_world()  # 先回桌面这个家
                changes["went_home"] = True
            st["awake"] = False
            st["activity"] = "睡觉"
            st["activity_since"] = now.isoformat(timespec="seconds")
            st["activity_world"] = "desktop"
            st["last_sleep_at"] = now.isoformat(timespec="seconds")
            self._push_event(now, "desktop", "去睡了")
            changes["went_sleep"] = True
        elif not st["awake"] and self._should_wake(now):
            st["awake"] = True
            st["last_wake_at"] = now.isoformat(timespec="seconds")
            st["activity"] = "刚醒，在屏幕前缓神"
            st["activity_since"] = now.isoformat(timespec="seconds")
            self._push_event(now, "desktop", "醒了")
            changes["woke_up"] = True
        elif not st["awake"]:
            self._save()
            return changes  # 睡着时不选活动

        # 3. 桌面活动驱动（在小世界里时活动交给 WorldManager）
        if not self.world.is_in_small_world():
            self._tick_desktop_activity(now, mood, changes)

        self._save()
        return changes

    def _tick_desktop_activity(self, now, mood, changes):
        st = self.state
        # 当前活动是否到期
        try:
            since = datetime.fromisoformat(st["activity_since"])
            stayed = (now - since).total_seconds() / 60
        except Exception:
            stayed = ACTIVITY_MIN_MINUTES + 1

        # 精力见底 → 强制休息类
        if st["energy"] < ENERGY_LOW and "休息" not in self._activity_kind(st["activity"]):
            self._settle_activity(now, stayed)  # 收尾：这次行为留下点什么
            self._switch_activity(now, "趴着休息一会儿", "desktop")
            changes["activity_changed"] = True
            return

        if stayed < ACTIVITY_MIN_MINUTES:
            return
        if random.random() > ACTIVITY_SWITCH_CHANCE:
            return

        # 换一个活动：精力门槛过滤 + 排除当前
        candidates = [(a, k) for a, min_e, _r, k in DESKTOP_ACTIVITIES
                      if min_e <= st["energy"] and a != st["activity"]]
        if not candidates:
            return
        # 心境微调：想安静/自己待着 → 偏向安静和发呆；想聊 → 也允许电脑类
        if mood in ("想安静", "自己待着"):
            quiet = [c for c in candidates if c[1] in ("安静", "发呆", "休息")]
            if quiet and random.random() < 0.6:
                candidates = quiet
        chosen, _kind = random.choice(candidates)
        self._settle_activity(now, stayed)  # 收尾：旧活动留下结果，再换新的
        self._switch_activity(now, chosen, "desktop")
        changes["activity_changed"] = True

    def _settle_activity(self, now, stayed):
        """行为收尾：做满时间的活动以一定概率产生一个"结果"，作为痕迹进入 recent_events。
        不是每件事都有结果——日子的大部分本来就是平淡的。"""
        act = self.state["activity"]
        pool = ACTIVITY_OUTCOMES.get(act)
        if not pool or stayed < OUTCOME_MIN_MINUTES:
            return
        if random.random() > OUTCOME_CHANCE:
            return
        self._push_event(now, "desktop", f"{act}：{random.choice(pool)}")

    @staticmethod
    def _activity_rate(name):
        for a, _e, r, _k in DESKTOP_ACTIVITIES:
            if a == name:
                return r
        return 0.0

    def _activity_kind(self, name):
        for a, _e, _r, k in DESKTOP_ACTIVITIES:
            if a == name:
                return k
        return ""

    def _switch_activity(self, now, name, world_name):
        st = self.state
        # 换活动时按上一活动的消耗率结算是不必要的（精力按时间统一结算），只记痕迹
        self._push_event(now, world_name, f"开始{name}")
        st["activity"] = name
        st["activity_since"] = now.isoformat(timespec="seconds")
        st["activity_world"] = world_name

    def _push_event(self, now, world_name, text):
        events = self.state["recent_events"]
        events.append({
            "ts": now.strftime("%Y-%m-%d %H:%M"),
            "world": world_name,
            "text": text,
        })
        del events[:-RECENT_EVENTS_MAX]
        # 同步进按天长日志（只增不删的底账；失败不影响内存痕迹）
        if self.day_log is not None:
            try:
                self.day_log.log("life", text, world=world_name, now=now)
            except Exception:
                pass

    # ---------- 对外描述 ----------

    def describe_for_prompt(self):
        """给聊天 prompt 的一句话状态（没有值得说的就返回空串）。"""
        st = self.state
        if not st["awake"]:
            return ("现在是你的睡觉时间，你在睡觉。用户发消息时你可以醒来回应，"
                    "但懒懒的、话少，像被吵醒的人。")
        try:
            since = datetime.fromisoformat(st["activity_since"])
            stayed = (datetime.now() - since).total_seconds() / 60
        except Exception:
            stayed = 999
        if "刚醒" in st["activity"] and stayed < 30:
            return "你刚醒没多久，还有点没缓过来。"
        if st["energy"] < ENERGY_LOW:
            return "你今天有点累，说话时透着一点倦意。"
        return ""

    def describe_status(self):
        """给界面状态栏的一句话。"""
        st = self.state
        if not st["awake"]:
            return "在睡觉"
        world = "小世界" if self.world.is_in_small_world() else "桌面"
        return f"{world} · {st['activity']}"

    def lived_desc(self):
        """他活过的时长描述；未满一天不显示（新手保护期）。"""
        h = self.state.get("lived_hours", 0.0)
        if h < 24:
            return ""
        days = h / 24
        if days < 10:
            return f"活了{int(days)}天{int(h % 24)}小时"
        return f"活了{days:.0f}天"

    def recent_events_text(self):
        """最近发生的事（备给 API 阶段做"一天总结"的料）。"""
        return list(self.state["recent_events"])


if __name__ == "__main__":
    # 独立自测：不依赖主系统（假 world）
    class _FakeWorld:
        def is_in_small_world(self):
            return False
    import tempfile
    life = LifeRhythm(tempfile.mkdtemp(), _FakeWorld())
    print("初始:", life.describe_status())
    changes = life.tick(mood="无特别想法", presence="AWAY")
    print("tick:", changes, "|", life.describe_status())
    print("prompt:", life.describe_for_prompt() or "(无)")
