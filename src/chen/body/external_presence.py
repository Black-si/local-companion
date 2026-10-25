"""受限的手机/平板存在感状态存储。

只保存白名单摘要字段，不接收聊天、照片或网页正文等原始内容。
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path


class ExternalPresence:
    """维护多设备的低敏状态，并兼容旧版单设备文件格式。"""

    _TEXT_LIMITS = {
        "app": 80,
        "focus": 60,
        "wifi": 80,
        "music_title": 160,
        "music_artist": 100,
    }

    def __init__(self, memory_dir):
        self._lock = threading.RLock()
        self.path = Path(memory_dir) / "external_presence.json"
        self.data = self._load()

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return {}
                if not isinstance(data.get("devices"), dict):
                    legacy_device = str(data.get("device", "ios") or "ios")
                    data["devices"] = {legacy_device: dict(data)}
                return data
        except Exception:
            pass
        return {}

    def update(self, device="ios", activity="", place="", **details):
        """更新一个设备的低敏摘要，并限制常驻设备数量。"""
        with self._lock:
            now = datetime.now().isoformat(timespec="seconds")
            device = str(device or "ios")[:30]
            state = {
                "device": device,
                "activity": str(activity or "")[:80],
                "place": str(place or "")[:80],
                "updated_at": now,
            }
            for key, limit in self._TEXT_LIMITS.items():
                value = details.get(key)
                if value not in (None, ""):
                    state[key] = str(value)[:limit]
            battery = details.get("battery")
            if isinstance(battery, (int, float)) and not isinstance(battery, bool):
                state["battery"] = max(0, min(100, round(float(battery), 1)))
            if isinstance(details.get("charging"), bool):
                state["charging"] = details["charging"]

            devices = self.data.get("devices", {}) if isinstance(self.data, dict) else {}
            devices = dict(devices) if isinstance(devices, dict) else {}
            devices[device] = state
            while len(devices) > 16:
                oldest = min(devices, key=lambda name: devices[name].get("updated_at", ""))
                del devices[oldest]

            # 顶层保留最近更新的设备，兼容现有 UI 和提示词。
            self.data.clear()
            self.data.update(state)
            self.data["devices"] = devices
            self._save()
            return dict(self.data)

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def prompt_block(self):
        """把当前白名单状态格式化为可注入 system prompt 的文本。"""
        try:
            with self._lock:
                ext = dict(self.data or {})
            if not ext.get("activity"):
                return ""
            age = ext.get("updated_at", "")
            mobile_lines = [
                f"设备：{ext.get('device', 'iOS')}",
                f"活动：{ext.get('activity')}",
                f"地点：{ext.get('place') or '未知'}",
            ]
            labels = (
                ("app", "当前应用"),
                ("focus", "专注模式"),
                ("wifi", "Wi‑Fi"),
                ("battery", "电量"),
                ("music_title", "音乐"),
            )
            for key, label in labels:
                if ext.get(key) not in (None, ""):
                    value = ext[key]
                    if key == "battery":
                        value = f"{value}%" + ("（充电中）" if ext.get("charging") else "")
                    elif key == "music_title" and ext.get("music_artist"):
                        value = f"{value} / {ext['music_artist']}"
                    mobile_lines.append(f"{label}：{value}")
            mobile_lines.append(f"更新时间：{age}")
            return "====用户的手机/平板状态====\n" + "；".join(mobile_lines)
        except Exception:
            return ""
