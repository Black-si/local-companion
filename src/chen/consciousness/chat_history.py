"""聊天记录的本地持久化。

这里只负责聊天记录文件，不负责 LLM 上下文、关系更新或日誌副作用。
"""

import json
import os
from pathlib import Path


class ChatHistory:
    """读取和原子保存有限长度的聊天记录。"""

    def __init__(self, path, max_entries=500):
        self.path = Path(path)
        self.max_entries = max(1, int(max_entries))

    def load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-self.max_entries:]
        except Exception:
            pass
        return []

    def save(self, entries):
        """原子保存记录；写盘失败不影响聊天。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(list(entries)[-self.max_entries:], ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass
