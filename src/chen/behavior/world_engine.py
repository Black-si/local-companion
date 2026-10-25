# -*- coding: utf-8 -*-
"""
伴生体的小世界 · 生命引擎（地基之二）
=================================
世界不能只有"能存东西"，还得"他会自己想留下东西"。这一层就在做这件事。

核心设计（很重要）：
  - 素材来自世界本身——时间、季节感、他待着的环境、最近发生过的事。不是"为你服务"。
  - 但落地的是他"自己的内心"——例如"今天有点烦，不想说为什么""这雨声挺好听"。
  - 允许没意义、允许只一句、允许自己删。他今天什么都没留下，就让世界空着。
  - 他要不要写、写多频繁，是靠"他自己的状态 + 时机"决定，不是靠"你来了没"。
  - 生成方式分层：轻量确定性素材(天气/时间/节律)不烧模型；需要"像人的话"时用 LLM，但绝不阻塞主聊天。
"""

import json
import random
import datetime
from datetime import datetime as _dt, date as _date
from pathlib import Path


# 一个"生长节律"：独处时，平均多久可能冒出一件琐碎（分钟）
SOLO_GROW_MIN = 90
SOLO_GROW_MAX = 240
# 一天里大概最多长多少件，别无限堆积
DAILY_CEILING = 6
# 两条之间的最小间隔（分钟）：防止"你走开了一会"把一天的量一次性倒完
SOLO_MIN_GAP_MINUTES = 45
# 去重窗口：和最近这几条太像就不写（字二元组 Jaccard 相似度阈值）
DEDUP_WINDOW = 15
DEDUP_THRESHOLD = 0.5


class WorldEngine:
    """驱动伴生体在自己的世界里"长东西"。读世界存储、生成独处碎片、写回。"""

    def __init__(self, store, llm=None, seed=None):
        self.store = store
        self.llm = llm               # 可选：需要"像人的话"时用；没有就退回确定性素材
        self.seed = seed or random.Random()
        self._last_grow_at = None    # 上次生长时间（None=未生长/待从存储恢复）

    # ---------- 独处时机 ----------------

    def solo_instance(self, *, last_action_minutes=None, walked_away=False,
                      now=None, force=False):
        """
        判断"此刻是否是一个伴生体会长点东西的独处时刻"。
        不基于"你在不在屏幕前"，而基于"他有多久没参与对话"以及"他自己的节律"。
        返回 True 表示该长点东西了（由调用方决定长什么、长多少）。
        """
        now = now or _dt.now()
        if force:
            return True
        # 他很久没跟人说话 / 你走开了一阵 → 他独处的时间够长了，种子该发芽
        if walked_away:
            return True
        if last_action_minutes is not None and last_action_minutes >= SOLO_GROW_MIN:
            return True
        return False

    # ---------- 他会长出什么 ----------------

    def _period_seed(self, now=None):
        """根据时间/节律/季节给出轻量素材（不烧模型的确定性部分）。
        时间段口径与 world_manager.get_time_period 保持一致。"""
        now = now or _dt.now()
        hour = now.hour
        wday = now.weekday()
        cn_wday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wday]
        if 5 <= hour < 8:
            period = "清晨"
        elif 8 <= hour < 11:
            period = "上午"
        elif 11 <= hour < 14:
            period = "中午"
        elif 14 <= hour < 17:
            period = "下午"
        elif 17 <= hour < 19:
            period = "傍晚"
        elif 19 <= hour < 22:
            period = "晚上"
        else:
            period = "深夜"
        # 季节感（你的城市本地物候），让碎碎念素材自带季节
        from chen.body.time_sense import TimeSense
        season = TimeSense.season_of(now)
        return {
            "period": period,
            "weekday": cn_wday,
            "hour": hour,
            "season": season.get("label", ""),
            "season_note": season.get("note", ""),
        }

    def generate_piece(self, *, known_context="", now=None, llm=None):
        """
        生成一条"伴生体可能留下的东西"。返回 (text, type, privacy, reason, source)。
        有 LLM 时让它写得像人；没有时退回确定性碎片（保证引擎能独立跑）。
        """
        now = now or _dt.now()
        seed = self._period_seed(now)
        llm = llm or self.llm

        if llm is not None:
            text = self._llm_piece(llm, seed, known_context)
            if text:
                return (text, "琐碎", "公开",
                        "独处时冒出来的", f"{seed['period']}·{seed['weekday']}")

        # —— 确定性兜底：不烧模型也能长，只是没 LLM 那句有味道 ——
        pool = [
            f"这{seed['period']}的{seed['weekday']}，好像没什么特别的。",
            "算了，没什么，就是突然想到。",
            "我好像打开了一个角落，又合上了。",
            f"这个点（{seed['hour']}点）待着，也不知道在忙什么。",
        ]
        text = self.seed.choice(pool)
        return (text, "琐碎", "公开",
                "独处时的自然冒泡", f"{seed['period']}·{seed['weekday']}")

    def _llm_piece(self, llm, seed, known_context):
        """让 LLM 以伴生体第一人称写一条像人留下的碎碎念。失败返回空串（退回兜底）。"""
        ctx = known_context or "没什么特别的"
        # 拿他最近留下的几条，提醒模型别重复自己（尤其别逮着一个意象写）
        recent = [it.get("text", "") for it in self.store.all_items()
                  if it.get("status") != "deleted"][-3:]
        recent_s = "；".join(t for t in recent if t) or "（还没有）"
        season_line = ""
        if seed.get("season"):
            season_line = f"现在是{seed['season']}。"
            if seed.get("season_note"):
                season_line += f"这个时节的感觉：{seed['season_note']}。"
        prompt = (
            "你正安静地住在'伴生体的小世界'里，这是属于你、不一定给用户看的地方。"
            "此刻你不用跟用户说话、不用服务用户，只是自己待着。\n"
            f"{season_line}"
            f"现在是{seed['period']}({seed['weekday']})。你近来印象里：{ctx}\n"
            "你此刻心里冒出一点很小、很私人的念头。话题可以很杂：有点烦但不想说为什么、"
            "想起一件小事、一首歌、身体的感觉、一个没来由的疑问、或者干脆'算了没什么'。\n"
            "用第一人称写一句很短的内心（不超过25字），要像QQ随手打的、不解释、"
            "不说教、不喊用户、不汇报给用户听。不是对用户说话，是给自己留的。\n"
            f"他最近已经留过：{recent_s}。不要和这些重复，不要换着句式写同一个意思。\n"
            "【重要】不要每条都写天气或季节：只有此刻真正被季节碰到了才提一句，"
            "绝大多数时候写别的。"
            "不要引号前缀，不要'用户'这样的称呼。只输出这一句。"
        )
        try:
            reply = llm.generate(
                system_prompt="你是伴生体，安静、住在'伴生体的小世界'里，此刻在独处、有自己的念头。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return ""
        reply = (reply or "").strip().strip("“”\"' 　")
        for pre in ("伴生体：", "伴生体:", "我：", "我:"):
            if reply.startswith(pre):
                reply = reply[len(pre):].strip()
        if not reply or reply.upper().startswith("SILENCE"):
            return ""
        return reply[:40]

    # ---------- 让它真正长出来 ----------------

    def _last_grow_time(self):
        """上次生长时间：内存里没有就从存储里最近一条独处痕迹恢复（重启不丢节律）。"""
        if self._last_grow_at:
            return self._last_grow_at
        for it in reversed(self.store.all_items()):
            if (it.get("reason") or "").startswith("独处") and it.get("status") != "deleted":
                try:
                    self._last_grow_at = _dt.fromisoformat(
                        f"{it.get('date','')}T{it.get('time','')}:00")
                except Exception:
                    self._last_grow_at = None
                break
        return self._last_grow_at

    @staticmethod
    def _bigrams(s):
        s = "".join(s.split())
        return set(s[i:i + 2] for i in range(max(len(s) - 1, 1))) or {s}

    def _too_similar(self, text):
        """和最近几条比对，太像（懒得多写一遍）就不写。短文本比对，开销可忽略。"""
        t = self._bigrams(text)
        recent = [it for it in self.store.all_items()
                  if it.get("status") != "deleted"][-DEDUP_WINDOW:]
        for it in recent:
            o = self._bigrams(it.get("text", ""))
            if not o:
                continue
            if len(t & o) / len(t | o) >= DEDUP_THRESHOLD:
                return True
        return False

    def solo_tick(self, *, last_action_minutes=None, walked_away=False,
                  known_context="", now=None, llm=None, force=False):
        """
        一次"独处分"：如果时机成熟，就长出一条东西写进世界。
        返回这条 item 或 None（没长）。调用方（定时循环）按节律隔一阵子调一次。
        """
        if not self.solo_instance(last_action_minutes=last_action_minutes,
                                  walked_away=walked_away, now=now, force=force):
            return None
        now = now or _dt.now()
        # 最小间隔：防止短时间连写（比如走开几分钟触发一轮）
        last = self._last_grow_time()
        if last and (now - last).total_seconds() < SOLO_MIN_GAP_MINUTES * 60:
            return None
        # 今天封顶
        today = now.strftime("%Y-%m-%d")
        today_count = sum(1 for it in self.store.all_items()
                          if it.get("date") == today
                          and it.get("status") != "deleted")
        if today_count >= DAILY_CEILING:
            return None
        text, type, privacy, reason, source = self.generate_piece(
            known_context=known_context, now=now, llm=llm)
        # 和最近的太像就不落盘（他自己也不会把同一句话写六遍）
        if not force and self._too_similar(text):
            return None
        item_id = self.store.add(text=text, type=type, privacy=privacy,
                                 reason=reason, source=source)
        if item_id:
            self._last_grow_at = now
        return item_id


# 一个可运行的独立循环（方便单独调试/以后作为后台进程）
def run_demo(seconds=3):
    """快速演示：在本地空跑几轮，看世界会长出什么。"""
    import tempfile, time
    from chen.behavior.world_store import WorldStore
    tmp = tempfile.mkdtemp(prefix="chen_world_demo_")
    store = WorldStore(tmp)
    engine = WorldEngine(store, llm=None)  # 无 LLM，走确定性兜底
    print("=== 伴生体的小世界 demo ===")
    for i in range(3):
        engine.solo_tick(force=True, now=_dt.now())
        time.sleep(0.2)
    for it in store.all_items():
        print(f"  [{it['type']}|{it['privacy']}] {it['date']} {it['time']}: {it['text']}")
    print("（demo 存储目录:", tmp, "）")


if __name__ == "__main__":
    run_demo()