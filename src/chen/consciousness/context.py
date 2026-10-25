"""
情境模块 - 伴生体的“现在”
V0.4：只保留现实世界中的当前情境、话题和目标。
它不是长期记忆；它描述的是“我们现在正在聊什么、为什么聊”。
"""

import json
import os
from datetime import datetime
from pathlib import Path


class Context:
    """管理伴生体当前所处的情境和话题"""

    CONTEXTS = {
        "日常": "现实中的日常交流，不要求解决什么问题。",
        "思考": "围绕一个问题共同思考，可以保留未解决的问题和双方观点。",
        "学习": "一起理解知识、解决学习问题，以弄懂为目标。",
        "项目": "一起建设伴生体或其他项目，以推进事情为目标。"
    }

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "memory" / "current_context.json"
        self.data = self._load()

    def _default(self):
        return {
            "context": "日常",
            "topic": "",
            "goal": "",
            "started_at": datetime.now().isoformat(timespec="minutes"),
            "updated_at": datetime.now().isoformat(timespec="minutes"),
            "topic_history": []
        }

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._default()
                base.update(data)
                # 兼容 V0.3：旧文件可能有 world，但 V0.4 不再使用它。
                base.pop("world", None)
                return base
        except Exception:
            pass
        return self._default()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def set(self, context=None, topic=None, goal=None):
        old_topic = self.data.get("topic", "")
        if topic is not None and topic != old_topic and old_topic:
            self.data.setdefault("topic_history", []).append({
                "topic": old_topic,
                "ended_at": datetime.now().isoformat(timespec="minutes")
            })
            self.data["topic_history"] = self.data["topic_history"][-20:]

        if context in self.CONTEXTS:
            self.data["context"] = context
        if topic is not None:
            self.data["topic"] = topic.strip()
        if goal is not None:
            self.data["goal"] = goal.strip()
        # V0.4 起不再保存/切换“世界”；旧版 current_context.json 中若残留 world 字段，
        # 会被忽略，不影响现有记忆。
        self.data.pop("world", None)
        self.data["updated_at"] = datetime.now().isoformat(timespec="minutes")
        self.save()

    def prompt_text(self):
        context = self.data.get("context", "日常")
        desc = self.CONTEXTS.get(context, "")
        topic = self.data.get("topic") or "无固定话题"
        goal = self.data.get("goal") or "没有必须完成的目标"
        return (
            "====当前情境====\n"
            f"当前情境：{context}（{desc}）\n"
            f"当前话题：{topic}\n"
            f"当前目标：{goal}\n"
            "注意：当前情境只是帮助你理解“我们现在在做什么”，"
            "不是要求你每次回复都提及它。"
        )

    def short_label(self):
        topic = self.data.get("topic") or "无固定话题"
        return f"现实 · {self.data.get('context','日常')} · {topic}"
