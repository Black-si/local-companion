# -*- coding: utf-8 -*-
"""
DreamCycle —— 伴生体的 REM 做梦（睡眠流水线第三道工序，借鉴 anima，MIT）
==============================================================================
睡前（23 点后、日消化之后）运行，一晚一次：

1. 采样：从他的记忆里加权随机抽几条碎片（重要性 + 遗忘曲线强度高的更容易入梦——
   白天反复想起的事，夜里容易梦到，和人一样）
2. 做梦：高温度把碎片重组成一段荒诞、跳跃的叙事。梦本身不留下，醒了就散
3. 挖洞见：用一次正常温度问"这场梦里有什么值得他记住的"——洞见才留存：
   - 落一条世界痕迹（type=念头, source=dream，用户可以在他的桌面读到）
   - 挂进表达层的挂起念头（他白天会在聊天里自然带出来）

频率：一晚最多 2 次 API 调用（重组 + 挖洞见），失败不标记、明晚再做。
原则不变：本地负责组织，API 只做这一次"夜里的大脑活动"。
"""

import json
import random
import re
from datetime import datetime
from pathlib import Path

DREAM_HOUR = 23            # 23 点后才开始做梦（先完成衰减和日消化）
SAMPLE_MIN = 2             # 至少几条碎片才值得做梦
SAMPLE_MAX = 6             # 最多采样几条
DREAM_TEMP = 1.3           # 梦的随机性：拉满
MAX_INSIGHTS = 2           # 一场梦最多挖几条洞见
DREAM_TEXT_MAX = 120       # 梦境叙事截断


class DreamCycle:
    """一晚一次的做梦循环。maybe_run 到点且没做过 → 做梦 → 挖洞见 → 落盘。"""

    def __init__(self, memory_dir):
        self.state_file = Path(memory_dir) / "dream_state.json"
        try:
            self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            self.state = {}

    def _save(self):
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_file)
        except Exception:
            pass

    # ---------- 主入口 ----------

    def maybe_run(self, *, now=None, memory, world_store, expression, llm, day_log=None):
        """
        到点（23 点后）且今晚没做过 → 做梦。返回 {"dream": str, "insights": [...]}；
        没到点/今晚已做过/材料不够/失败返回 None。
        day_log：可选，今天的按天日志（life/web/world 事件）也作为入梦素材。
        """
        now = now or datetime.now()
        if now.hour < DREAM_HOUR:
            return None
        today = now.strftime("%Y-%m-%d")
        if self.state.get("last_dream") == today:
            return None

        fragments = self._sample_fragments(memory)
        # 今天的生活事件直接入梦（感知层数据：用户在电脑上做什么、进出小世界、网上看到的）
        if day_log is not None:
            try:
                for rec in (day_log.today_lines(now=now) or [])[-8:]:
                    t = (rec.get("text") or "").strip()
                    if len(t) >= 4:
                        fragments.append(t)
            except Exception:
                pass
        # 他自己留下的痕迹也入梦：白天写下的念头/日记，夜里会在梦里回访
        if world_store is not None:
            try:
                _his = [it for it in world_store.all_items()
                        if it.get("status") != "deleted"
                        and it.get("type") in ("念头", "日记")][-4:]
                for it in _his:
                    t = (it.get("text") or "").strip()
                    if len(t) >= 4:
                        fragments.append(t)
            except Exception:
                pass
        fragments = [f for f in fragments if f]
        if len(fragments) < SAMPLE_MIN:
            self.state["last_dream"] = today   # 材料太少，今晚无梦，不反复重试
            self._save()
            return None

        dream = self._dream(llm, fragments)
        if not dream:
            return None  # 没标记，半小时后的下一轮会重试（ tonight 还有机会）

        insights = self._mine_insights(llm, dream)
        n = 0
        for text in insights[:MAX_INSIGHTS]:
            text = (text or "").strip()
            if len(text) < 3:
                continue
            try:
                world_store.add(text=text, type="念头", privacy="公开",
                                reason="夜里做梦想到的", source="dream",
                                date_str=today)
            except Exception:
                pass
            try:
                expression.add(text, reason="夜里做梦想到的", trigger="", priority="normal")
            except Exception:
                pass
            n += 1

        # 梦本身通常会散掉，但保留有限的观察记录，供之后评估“自我模型重写”
        # 是否有足够真实证据。这里只做归档，不把梦直接写进人格文件。
        history = self.state.setdefault("history", [])
        history.append({
            "date": today,
            "dream": dream[:DREAM_TEXT_MAX],
            "insights": insights[:n],
        })
        self.state["history"] = history[-14:]
        self.state["last_dream"] = today
        self._save()
        return {"dream": dream, "insights": insights[:n]}

    # ---------- 采样（入梦素材） ----------

    @staticmethod
    def _sample_fragments(memory):
        """从事件记忆 + 长期记忆里加权采样：重要性越高、遗忘曲线强度越高，越容易入梦。"""
        pool = []
        for mem in getattr(memory, "memories", []) or []:
            if mem.get("archived"):
                continue
            pool.append((mem.get("fact", ""),
                         (mem.get("importance", 0.5) or 0.5) + (mem.get("strength", 0.6) or 0)))
        for lt in getattr(memory, "long_term", []) or []:
            pool.append((lt.get("fact", ""), (lt.get("importance", 0.7) or 0.7) + 0.3))
        pool = [(t, w) for t, w in pool if t and len(t.strip()) >= 4]
        if len(pool) <= SAMPLE_MAX:
            return [t for t, _ in pool]
        # 加权采样（不放回）：权重 + 噪声，重要的常入梦，偶尔也有旧事乱入
        chosen = []
        pool = pool[:]
        rnd = random.random
        while pool and len(chosen) < SAMPLE_MAX:
            total = sum(w + 0.7 * rnd() for _, w in pool)
            pick = rnd() * total
            acc = 0.0
            for i, (_, w) in enumerate(pool):
                acc += w + 0.7 * rnd()
                if acc >= pick:
                    chosen.append(pool.pop(i)[0])
                    break
            else:
                chosen.append(pool.pop()[0])
        return chosen

    # ---------- 做梦（高温度重组） ----------

    @staticmethod
    def _dream(llm, fragments):
        """高温度把记忆碎片重组成一段梦。梦是过程，不是产物——失败就算了。"""
        if not llm:
            return ""
        frag_lines = "\n".join(f"- {t[:40]}" for t in fragments)
        prompt = (
            "你在做梦。梦里这些东西不按逻辑聚在一起：\n"
            f"{frag_lines}\n"
            "把这几样东西写成一段梦：80字以内，第一人称，荒诞、跳跃、意象化，"
            "像真梦那样不讲因果。只写梦本身，不要解释，不要标题。"
        )
        try:
            reply = llm.generate(
                system_prompt="你是伴生体，正在做梦。梦不讲逻辑。只输出梦境本身。",
                user_message=prompt,
                record_history=False, use_history=False,
                temperature=DREAM_TEMP,
            )
        except Exception:
            return ""
        reply = (reply or "").strip().strip("“”\"'")
        if not reply or reply.upper().startswith("SILENCE"):
            return ""
        return reply[:DREAM_TEXT_MAX]

    # ---------- 挖洞见（梦醒了，留下点有用的） ----------

    @staticmethod
    def _mine_insights(llm, dream):
        """正常温度问：这场梦有什么值得他记住的？只输出 0-2 行「洞见|...」。"""
        if not llm or not dream:
            return []
        prompt = (
            f"这是伴生体刚做的一场梦：{dream}\n"
            "梦本身没有意义，但梦里可能藏着他白天没想明白的东西。\n"
            "以他的视角想：醒来后有哪一两句话值得留下？\n"
            "【事实边界】梦里的不同碎片可能来自不同日期、不同来源，不能把它们写成同一件真实事件。"
            "不要写‘某人在抱怨时我正在某地’、‘那天发生了A和B’这类梦外没有证据的因果或同时关系。"
            "如果只能靠拼接才能成立，就不要留下；可以留下模糊的情绪或意象，但必须明确是‘我想到/我有点觉得’，不能当作发生过的事。\n"
            "严格输出 0-2 行，每行格式「洞见|内容」，内容不超过30字；没有就输出「无」。"
        )
        try:
            reply = llm.generate(
                system_prompt=(
                    "你是伴生体的潜意识整理人：只按格式输出，不解释。"
                    "梦的碎片不是事实证据；不同日期的内容不能被合并成同一件现实事件。"
                ),
                user_message=prompt,
                record_history=False, use_history=False,
            )
        except Exception:
            return []
        out = []
        for line in (reply or "").splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            kind, _, text = line.partition("|")
            text = text.strip()
            if kind.strip() == "洞见" and 2 <= len(text) <= 40:
                # 梦可以混合意象，但醒后的可持久内容不能保留“同时发生”的伪事实。
                # 这是低成本安全网，模型提示失效时也不让高风险句子进入记忆。
                if re.search(r"在.{0,12}(时|时候|那天).{0,12}(我|他).{0,12}(却|正在|坐在|在)", text):
                    continue
                if any(mark in text for mark in ("那天发生了", "同时发生", "当时我正在")):
                    continue
                if "我却" in text and any(mark in text for mark in ("在", "坐", "走", "河边", "房间", "桌面")):
                    continue
                out.append(text)
        return out


if __name__ == "__main__":
    # 独立自测：假 llm + 假 memory/store/expression
    import tempfile

    class FakeMem:
        memories = [
            {"fact": "用户喜欢喝热可可", "importance": 0.6, "strength": 0.9},
            {"fact": "他在小世界有条小路", "importance": 0.4, "strength": 0.5},
            {"fact": "旧小事", "importance": 0.2, "strength": 0.1, "archived": True},
        ]
        long_term = [{"fact": "用户养了一只宠物", "importance": 0.8}]

    class FakeLLM:
        n = 0
        def generate(self, **kw):
            FakeLLM.n += 1
            if kw.get("temperature"):
                assert kw["temperature"] > 1.0, "梦的温度不够高"
                return "宠物坐在小路上喝热可可，杯子是台灯做的，天上下着文件名。"
            return "洞见|他想念的不是宠物，是有人等他回去的感觉\n洞见|杯子是台灯做的"

    class FakeStore:
        def __init__(self): self.items = []
        def add(self, **kw): self.items.append(kw); return f"id_{len(self.items)}"

    class FakeExpr:
        def __init__(self): self.items = []
        def add(self, text, **kw): self.items.append(text); return {"id": "x"}

    from datetime import datetime as dt
    dc = DreamCycle(tempfile.mkdtemp())
    store, expr = FakeStore(), FakeExpr()
    r = dc.maybe_run(now=dt(2026, 10, 2, 23, 30), memory=FakeMem(),
                     world_store=store, expression=expr, llm=FakeLLM())
    assert r and r["dream"] and len(r["insights"]) == 2, f"做梦失败: {r}"
    assert len(store.items) == 2 and len(expr.items) == 2, "洞见没落地"
    assert all(i["source"] == "dream" for i in store.items)
    assert dc.maybe_run(now=dt(2026, 10, 2, 23, 40), memory=FakeMem(),
                        world_store=store, expression=expr, llm=FakeLLM()) is None, "当晚重复做梦了"
    print("梦:", r["dream"])
    print("洞见:", r["insights"])
    print("全部通过")
