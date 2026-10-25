# -*- coding: utf-8 -*-
"""
按天经历日志：一天一个 jsonl，把伴生体的一天原样留下来。
=====================================================
不同于 recent_events 环形缓冲（只留 20 条、会被新事件挤掉），
这里是只增不删的长期痕迹——"他真的经历过一些日子"的底账，
也是第 3 阶段"睡前把一天酿成念头"的原料。

- 每条一行 JSON：{ts, kind, world, text, extra?}
- kind: life（作息/活动/结果）/ world（世界切换）/ chat（聊天）
- 纯本地追加写，每行一次 open+write，写坏绝不影响主流程
"""
import json
from datetime import datetime
from pathlib import Path


class DayLog:
    """按天追加的经历日志。日志文件：memory/life_logs/YYYY-MM-DD.jsonl"""

    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _file(self, now):
        return self.log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"

    def log(self, kind, text, world="", extra=None, now=None):
        """落一条。kind: life/world/chat；extra 放结构化补充（如选择原因）。"""
        now = now or datetime.now()
        rec = {
            "ts": now.strftime("%Y-%m-%d %H:%M"),
            "kind": kind,
            "world": world,
            "text": text,
        }
        if extra:
            rec["extra"] = extra
        try:
            with open(self._file(now), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 日志写坏不阻断主流程

    def today_lines(self, now=None):
        """读今天的日志（供状态栏/未来的日总结使用）；读坏返回空列表。"""
        now = now or datetime.now()
        try:
            f = self._file(now)
            if not f.exists():
                return []
            return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()
                    if l.strip()]
        except Exception:
            return []


if __name__ == "__main__":
    import tempfile
    dl = DayLog(tempfile.mkdtemp())
    dl.log("life", "去睡了", world="desktop")
    dl.log("world", "进入小世界", extra={"reason": "想出去走走"})
    dl.log("chat", "用户：在干嘛")
    for rec in dl.today_lines():
        print(rec)
