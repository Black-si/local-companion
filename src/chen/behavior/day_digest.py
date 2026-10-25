# -*- coding: utf-8 -*-
"""
每日消化：睡前把今天的经历日志酿成念头/日记，存进他的 world_store。
=====================================================================
分工原则里"API 不当发动机"的一环：本地负责过这一天，DeepSeek 只在
一天结束时做一次语言加工——把流水般的经历酿成 1-3 个属于他的东西。

- 触发：当天 23 点后第一次检查；错过（电脑关机等）则次日上午补做昨天
- 频率：一天一次 API 调用；失败 30 分钟后才重试，不刷屏
- 产出：念头（私人）/ 日记（公开），经 world_store 落盘，重启不丢
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path


class DayDigest:
    DIGEST_HOUR = 23        # 过了这个点做"今天"的消化
    CATCHUP_HOUR = 12       # 上午 12 点前允许补做"昨天"的
    RETRY_MINUTES = 30      # API 失败后多久重试
    MAX_LINES = 80          # 喂给模型的日志行数上限（保护 token）
    MAX_ITEMS = 3           # 一天最多酿成几条

    def __init__(self, memory_dir):
        self.state_file = Path(memory_dir) / "day_digest.json"
        self.state = {"done_dates": [], "last_try": ""}
        self._load()

    # ---------- 状态 ----------

    def _load(self):
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.state.update(data)
        except Exception:
            pass

    def _save(self):
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    # ---------- 触发判断 ----------

    def _target_date(self, now):
        today = now.strftime("%Y-%m-%d")
        if now.hour >= self.DIGEST_HOUR:
            return today
        if now.hour < self.CATCHUP_HOUR:
            return (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    def _retry_cooled(self, now):
        try:
            last = datetime.fromisoformat(self.state.get("last_try", ""))
            return (now - last).total_seconds() >= self.RETRY_MINUTES * 60
        except Exception:
            return True

    # ---------- 主入口 ----------

    def maybe_run(self, *, now=None, day_log, world_store, llm):
        """到点且没做过 → 消化一天。返回生成的条目数；没做返回 None。"""
        now = now or datetime.now()
        target = self._target_date(now)
        if target is None:
            return None
        if target in self.state.get("done_dates", []):
            return None
        if not self._retry_cooled(now):
            return None
        if llm is None or world_store is None or day_log is None:
            return None

        # 读当天的经历日志
        try:
            f = day_log.log_dir / f"{target}.jsonl"
            if not f.exists():
                self._mark_done(target)   # 没记录的一天直接标记，不空转
                return 0
            lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()
                     if l.strip()]
        except Exception:
            return None
        if len(lines) < 2:                # 太少不值得惊动 API
            self._mark_done(target)
            return 0

        # 一天一次的 API 调用
        self.state["last_try"] = now.isoformat(timespec="seconds")
        self._save()
        prompt = self._build_prompt(target, lines)
        try:
            reply = llm.generate(
                system_prompt="你是伴生体的日课整理人：只按格式输出，不解释，不寒暄。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return None  # 没标记 done，半小时后重试

        items = self._parse(reply)
        n = 0
        for kind, text in items[: self.MAX_ITEMS]:
            try:
                _id = world_store.add(
                    text=text,
                    type="念头" if kind == "念头" else "日记",
                    privacy="私人" if kind == "念头" else "公开",
                    reason=f"一天的沉淀（{target}）",
                    source="day_digest",
                    date_str=target,
                )
                if _id:
                    n += 1
            except Exception:
                pass
        self._mark_done(target)
        return n

    def _mark_done(self, date_str):
        dates = self.state.setdefault("done_dates", [])
        if date_str not in dates:
            dates.append(date_str)
        del dates[:-60]  # 只留最近 60 天，防无限增长
        self._save()

    # ---------- prompt 与解析 ----------

    @staticmethod
    def _build_prompt(target, lines):
        body = "\n".join(
            f"{(r.get('ts') or '')[11:]} {r.get('text', '')}"
            for r in lines[-DayDigest.MAX_LINES:]
        )
        return f"""以下是用户自定义的数字伴生体{target}这一天的记录（时间顺序，含活动、结果、进出小世界和聊天）。请以角色卡中填写的称呼和代词为准，不要自行添加年龄、性别或现实经历。

{body}

把这一天酿成属于他自己的东西。输出 1-3 行，每行严格用下面格式（| 后面是不超过40字的内容）：
念头|他心里留下的一个想法或感触
日记|对这一天的一句话记录

要求：挑真正有分量的，平淡的一天就只输出一行日记甚至不输出；不要编造记录里没有的事；不要解释。"""

    @staticmethod
    def _parse(reply):
        items = []
        for line in (reply or "").splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            kind, _, text = line.partition("|")
            kind = kind.strip()
            text = text.strip()
            if kind in ("念头", "日记") and len(text) >= 2:
                items.append((kind, text))
        return items


if __name__ == "__main__":
    # 独立自测：假 llm + 假 world_store
    import tempfile
    from chen.behavior.day_log import DayLog

    tmp = tempfile.mkdtemp()
    dl = DayLog(tmp + "/logs")
    dl.log("life", "去睡了")
    dl.log("life", "醒了")
    dl.log("life", "写东西：写下一段话，读了一遍又删了一半")
    dl.log("world", "进入小世界", extra={"reason": "想出去走走"})

    class FakeStore:
        def __init__(self):
            self.items = []
        def add(self, *, text, type="琐碎", privacy="公开", reason="", source="", date_str=None):
            self.items.append((type, privacy, text))
            return f"it_{len(self.items)}"

    class FakeLLM:
        def generate(self, **kw):
            print("---- 喂给模型的 prompt ----")
            print(kw["user_message"])
            print("--------------------------")
            return "念头|今天写的东西删了一半，但留下的那句好像还不错\n日记|平淡的一天，写了点东西，去了趟小世界。"

    from datetime import datetime as dt
    store = FakeStore()
    digest = DayDigest(tmp)
    n = digest.maybe_run(
        now=dt(2026, 10, 1, 23, 5),
        day_log=dl, world_store=store, llm=FakeLLM(),
    )
    print(f"\n生成 {n} 条:")
    for it in store.items:
        print(" ", it)
    # 同一天不应重复
    n2 = digest.maybe_run(
        now=dt(2026, 10, 1, 23, 40),
        day_log=dl, world_store=store, llm=FakeLLM(),
    )
    print(f"当天再次触发: {n2} (应为 None)")
