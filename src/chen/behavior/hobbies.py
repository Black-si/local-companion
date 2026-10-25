"""伴生体的爱好与偏好统计。

这一层只记录行为形成的轻量偏好，不参与聊天决策，也不主动调用模型。
"""

import json
import os
from datetime import datetime
from pathlib import Path


class Hobbies:
    """从重复活动中维护偏好摘要。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "hobbies.json"
        self.data = self._load()

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("activity_count", {})
                    data.setdefault("collection_types", {})
                    data.setdefault("preferences", [])
                    data.setdefault("updated_at", "")
                    return data
        except Exception:
            pass
        return {
            "activity_count": {},
            "collection_types": {},
            "preferences": [],
            "updated_at": "",
        }

    def save(self):
        """原子保存当前偏好。保存失败不影响聊天。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception as exc:
            print(f"保存爱好失败：{exc}")

    def record_activity(self, activity):
        """记录一次活动，每累计十次重新计算偏好。"""
        try:
            if not activity:
                return
            key = activity[:10]
            counts = self.data["activity_count"]
            counts[key] = counts.get(key, 0) + 1
            if sum(counts.values()) % 10 == 0:
                self.update_preferences()
            self.save()
        except Exception as exc:
            print(f"记录活动失败：{exc}")

    def update_preferences(self):
        """根据活动次数更新前三项偏好。"""
        try:
            counts = self.data["activity_count"]
            if not counts:
                return
            sorted_activities = sorted(
                counts.items(), key=lambda item: item[1], reverse=True
            )
            self.data["preferences"] = [
                activity for activity, count in sorted_activities[:3]
                if count >= 3
            ]
            print(f"[爱好] 当前偏好：{self.data['preferences']}")
        except Exception as exc:
            print(f"更新偏好失败：{exc}")

    def summary(self):
        """把偏好转换成可注入聊天上下文的短摘要。"""
        try:
            preferences = self.data.get("preferences", [])
            if not preferences:
                return ""
            descriptions = []
            for preference in preferences:
                if "河边" in preference or "水" in preference:
                    descriptions.append("喜欢去河边待着")
                elif "音乐" in preference or "耳机" in preference or "听歌" in preference:
                    descriptions.append("喜欢听音乐")
                elif "游戏" in preference:
                    descriptions.append("喜欢打游戏")
                elif "书" in preference or "写" in preference:
                    descriptions.append("喜欢看书/写东西")
                elif "山顶" in preference or "看远处" in preference:
                    descriptions.append("喜欢去山顶看风景")
                elif "田野" in preference:
                    descriptions.append("喜欢在田野里走走")
                else:
                    descriptions.append(f"经常{preference}")
            return "你的爱好：" + "、".join(descriptions[:3]) if descriptions else ""
        except Exception:
            return ""
