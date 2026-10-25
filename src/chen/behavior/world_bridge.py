# -*- coding: utf-8 -*-
"""伴生体小世界桥接层。

这个模块把现有的 ``WorldManager`` 状态转换成渲染客户端可以消费的稳定
快照，并处理来自客户端的语义命令。RPG Maker MV、Tkinter 或未来的 Godot
都只应该通过这里访问小世界，不直接读写 world_state.json。
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from datetime import datetime


class WorldBridge:
    """把伴生体的世界状态暴露为一个小而稳定的客户端协议。"""

    SCHEMA_VERSION = 1
    INDOOR_PLACES = {"家里", "卧室", "房间", "客厅", "厨房", "厕所", "卫生间", "浴室", "阳台", "小院"}
    OUTDOOR_PLACES = {"家外", "家外景", "屋外", "小路", "小镇", "主街", "学校", "学校门口", "公园", "社区公园", "树下", "河边", "田野", "山顶"}
    MAX_TEXT_CHARS = 256
    MAX_MESSAGE_CHARS = 1000
    ROOM_TARGETS = {
        "desk", "wall", "window", "bed", "drawer", "door", "sofa", "tv",
        "coffee_table", "bookshelf", "fireplace", "hall_door", "stove",
        "dining_table", "fridge", "sink", "coffee_bar", "kitchen_door",
        "washbasin", "bathtub", "toilet", "washer", "towel_shelf",
        "bath_door", "plant_rack", "balcony_rail", "lounge_chair",
        "tea_table", "balcony_door", "wall_lights",
        "courtyard_door", "watering", "bench", "sun_spot", "clothesline", "pond",
        "scene_center", "rest_spot", "lookout", "house_door",
    }
    ACTION_TARGETS = {
        "move_to": ROOM_TARGETS,
        "inspect": ROOM_TARGETS,
        "perform_activity": ROOM_TARGETS,
    }
    ACTION_POSES = {
        "walk", "book", "phone", "sit", "sleep", "drink", "music",
        "stretch", "bed_phone", "cook", "eat", "wash", "plant", "sofa",
    }

    def __init__(self, chen):
        self.chen = chen
        self._lock = threading.RLock()
        self._last_digest = ""
        self._revision = 0
        # 行动请求是短生命周期的运行时消息，不写入 world_state.json。
        # Python 决策层提出语义行动，MV 执行后用 report_action 回报结果。
        self._pending_action = None
        self._last_action = None
        self._runtime = {}
        self._last_intent_key = ""
        self._current_intent = None

    @staticmethod
    def _copy_item(item, index, location):
        """复制一个房间物品，并给旧数据补一个稳定的客户端 id。"""
        if isinstance(item, dict):
            value = copy.deepcopy(item)
        else:
            value = {"name": str(item)}
        value.setdefault("name", "")
        value.setdefault("desc", "")
        value.setdefault("rarity", "普通")
        value.setdefault("id", f"{location}_{index}")
        value["location"] = location
        return value

    @classmethod
    def _items(cls, house, key, location):
        raw = house.get(key, []) if isinstance(house, dict) else []
        if not isinstance(raw, list):
            return []
        return [cls._copy_item(item, index, location)
                for index, item in enumerate(raw)]

    @classmethod
    def _map_for(cls, current_world, place):
        if current_world != "small_world":
            return "desktop"
        text = str(place or "").strip().lower()
        if any(word in text for word in ("客厅", "living", "沙发", "电视")):
            return "living_room"
        if any(word in text for word in ("厨房", "餐厅", "kitchen", "做饭")):
            return "kitchen"
        if any(word in text for word in ("厕所", "卫生间", "浴室", "bathroom", "洗漱")):
            return "bathroom"
        if any(word in text for word in ("小院", "院子", "庭院", "courtyard", "浇水", "晾衣", "晒太阳", "长椅")):
            return "courtyard"
        if any(word in text for word in ("家外", "家门外", "屋外", "家外景")):
            return "outdoor_house"
        if any(word in text for word in ("主街", "小镇", "小路")):
            return "outdoor_street"
        if any(word in text for word in ("学校", "校门")):
            return "outdoor_school"
        if any(word in text for word in ("公园", "田野")):
            return "outdoor_park"
        if any(word in text for word in ("树下",)):
            return "outdoor_tree"
        if any(word in text for word in ("河边",)):
            return "outdoor_river"
        if any(word in text for word in ("山顶",)):
            return "outdoor_hill"
        if any(word in text for word in ("阳台", "露台", "balcony", "浇花")):
            return "balcony"
        return "room"

    def snapshot(self):
        """返回给渲染客户端的只读快照。"""
        with self._lock:
            world = getattr(self.chen, "world", None)
            state = world.get_state() if world is not None else {}
            house = state.get("house_state") or {}
            current_world = state.get("current_world", "desktop")
            place = state.get("activity_place", "")
            snap = {
                "schema_version": self.SCHEMA_VERSION,
                "world": current_world,
                "map": self._map_for(current_world, place),
                "chen": {
                    "present": current_world == "small_world",
                    "place": place,
                    "activity": state.get("activity", ""),
                    "pose": state.get("activity_pose", ""),
                    "event": state.get("current_event", ""),
                },
                "time": {
                    "period": state.get("time_period", ""),
                    "weather": state.get("weather", ""),
                },
                "house": {
                    "light_on": bool(house.get("light_on", False)),
                    "window_open": bool(house.get("window_open", False)),
                    "door_locked": bool(house.get("door_locked", False)),
                    "bed_made": bool(house.get("bed_made", True)),
                    "desk_items": self._items(house, "desk_items", "desk"),
                    "wall_items": self._items(house, "wall_items", "wall"),
                    "drawer_items": self._items(house, "drawer_items", "drawer"),
                },
                "environment": copy.deepcopy(state.get("environment") or {}),
                "runtime": {
                    "pending_action": copy.deepcopy(self._pending_action),
                    "last_action": copy.deepcopy(self._last_action),
                    "current_intent": copy.deepcopy(self._current_intent),
                    **copy.deepcopy(self._runtime),
                },
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            digest_payload = dict(snap)
            digest_payload.pop("updated_at", None)
            digest = hashlib.sha256(
                json.dumps(digest_payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if digest != self._last_digest:
                self._last_digest = digest
                self._revision += 1
            snap["revision"] = self._revision
            return snap

    def runtime(self):
        """返回 MV 执行动作所需的短生命周期运行时状态。"""
        with self._lock:
            return {
                "pending_action": copy.deepcopy(self._pending_action),
                "last_action": copy.deepcopy(self._last_action),
                "current_intent": copy.deepcopy(self._current_intent),
                **copy.deepcopy(self._runtime),
                "revision": self.snapshot()["revision"],
            }

    @staticmethod
    def _target_for_activity(place, activity):
        """把已有的本地活动标签映射成 MV 能执行的少量语义目标。"""
        text = f"{place} {activity}"
        if any(word in text for word in ("家外", "家门外", "屋外", "家外景")):
            return "house_door"
        if any(word in text for word in ("小路", "小镇", "主街", "学校", "校门", "公园", "树下", "河边", "田野", "山顶")):
            if any(word in text for word in ("风景", "眺望", "看远处", "看天空")):
                return "lookout"
            if any(word in text for word in ("坐", "休息", "发呆", "晒太阳", "停留")):
                return "rest_spot"
            return "scene_center"
        if any(word in text for word in ("客厅", "沙发")):
            if "电视" in text:
                return "tv"
            if "书" in text or "阅读" in text:
                return "bookshelf"
            return "sofa"
        if any(word in text for word in ("厨房", "做饭", "炒菜", "灶")):
            return "stove"
        if any(word in text for word in ("吃饭", "用餐", "餐桌")):
            return "dining_table"
        if any(word in text for word in ("冰箱",)):
            return "fridge"
        if any(word in text for word in ("厕所", "卫生间", "浴室", "洗漱", "刷牙")):
            if any(word in text for word in ("洗澡", "泡澡", "浴缸")):
                return "bathtub"
            if "厕所" in text or "如厕" in text:
                return "toilet"
            return "washbasin"
        if any(word in text for word in ("阳台", "浇花", "盆栽")):
            if "花" in text or "浇" in text:
                return "plant_rack"
            if "听歌" in text or "音乐" in text:
                return "lounge_chair"
            if "茶" in text or "喝" in text:
                return "tea_table"
            return "balcony_rail"
        if any(word in text for word in ("长椅", "坐着晒", "坐在院")):
            return "bench"
        if any(word in text for word in ("晾衣", "晾衣服", "衣服")):
            return "clothesline"
        if any(word in text for word in ("晒太阳", "院子发呆", "庭院发呆")):
            return "sun_spot"
        if any(word in text for word in ("水池", "池边")):
            return "pond"
        if any(word in text for word in ("小院", "院子", "庭院", "浇水", "浇花")):
            return "watering"
        if any(word in text for word in ("睡", "躺", "休息")):
            return "bed"
        if any(word in text for word in ("手机", "电话", "屏幕", "刷视频", "聊天")):
            return "desk"
        if any(word in text for word in ("看书", "阅读", "写", "学习", "电脑", "桌")):
            return "desk"
        if any(word in text for word in ("窗", "发呆", "风景", "晒太阳")):
            return "window"
        if any(word in text for word in ("喝水", "喝点水", "喝饮料", "喝茶", "听歌", "听音乐", "音乐")):
            return "desk"
        if any(word in text for word in ("拉伸", "伸懒腰", "活动筋骨")):
            return "window"
        return ""

    @classmethod
    def _pose_for_activity(cls, activity):
        text = str(activity or "").lower()
        if ("床" in text or "躺" in text) and "手机" in text:
            return "bed_phone"
        if any(word in text for word in ("喝水", "喝点水", "喝饮料", "喝茶", "热饮")):
            return "drink"
        if any(word in text for word in ("听歌", "听音乐", "音乐")):
            return "music"
        if any(word in text for word in ("做饭", "炒菜", "下厨", "灶")):
            return "cook"
        if any(word in text for word in ("吃饭", "用餐", "餐桌")):
            return "eat"
        if any(word in text for word in ("洗漱", "刷牙", "洗澡", "泡澡", "浴缸", "厕所")):
            return "wash"
        if any(word in text for word in ("浇花", "盆栽", "绿植")):
            return "plant"
        if any(word in text for word in ("浇水",)):
            return "plant"
        if any(word in text for word in ("长椅", "坐在院", "坐着晒")):
            return "sit"
        if any(word in text for word in ("晾衣", "晒太阳")):
            return "stretch"
        if any(word in text for word in ("沙发", "窝着", "躺椅")):
            return "sofa"
        if any(word in text for word in ("拉伸", "伸懒腰", "活动筋骨")):
            return "stretch"
        if any(word in text for word in ("睡觉", "睡", "眠")):
            return "sleep"
        if any(word in text for word in ("手机", "刷视频", "聊天")):
            return "phone"
        if any(word in text for word in ("看书", "阅读", "写", "学习")):
            return "book"
        if any(word in text for word in ("坐", "发呆", "窗边")):
            return "sit"
        return "walk"

    def sync_activity_intent(self, intent=None):
        """低频同步 Python 决定的语义意图到 MV；不调用模型，也不每帧发请求。"""
        with self._lock:
            world = getattr(self.chen, "world", None)
            if world is None:
                return None
            state = world.get_state()
            if state.get("current_world") != "small_world":
                self._last_intent_key = ""
                return None
            supplied = intent if isinstance(intent, dict) else {}
            place = str(supplied.get("place", state.get("activity_place", "")) or "")
            activity = str(supplied.get("activity", state.get("activity", "")) or "")
            target = str(supplied.get("target", "") or "") or self._target_for_activity(place, activity)
            if not target:
                return None
            if target not in self.ACTION_TARGETS["perform_activity"]:
                return None
            pose = str(supplied.get("pose", "") or "") or self._pose_for_activity(activity)
            if pose not in self.ACTION_POSES:
                pose = self._pose_for_activity(activity)
            intent_key = f"{place}|{activity}|{target}|{pose}"
            if intent_key == self._last_intent_key:
                return None
            if self._pending_action is not None:
                return None
            self._last_intent_key = intent_key
            duration = 240 if any(word in activity for word in ("睡", "躺", "休息")) else 180
            raw_duration = supplied.get("duration_frames", duration)
            if isinstance(raw_duration, bool):
                raw_duration = duration
            try:
                safe_duration = max(1, min(3600, int(raw_duration)))
            except (TypeError, ValueError):
                safe_duration = duration
            reason = str(supplied.get("reason", "") or "")[:self.MAX_TEXT_CHARS]
            request = {
                "id": uuid.uuid4().hex,
                "action": "perform_activity",
                "target": target,
                "params": {"place": place, "activity": activity, "pose": pose,
                            "duration_frames": safe_duration},
                "intent_id": str(supplied.get("intent_id", "") or ""),
                "reason": reason,
                "pose": pose,
                "priority": max(0, min(100, int(supplied.get("priority", 0) or 0))),
                "issued_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._current_intent = copy.deepcopy(supplied) if supplied else {
                "activity": activity, "place": place, "target": target,
                "reason": "沿用当前小世界活动",
            }
            self._pending_action = request
            return copy.deepcopy(request)

    def command(self, payload):
        """执行一个小世界语义命令，返回 ``(status_code, body)``。"""
        if not isinstance(payload, dict):
            return 400, {"ok": False, "error": "命令必须是 JSON 对象"}
        name = payload.get("command", "")
        if not isinstance(name, str):
            return 400, {"ok": False, "error": "command 必须是字符串"}
        name = name.strip()
        if not name:
            return 400, {"ok": False, "error": "缺少 command"}
        if len(name) > self.MAX_TEXT_CHARS:
            return 400, {"ok": False, "error": "command 过长"}

        with self._lock:
            world = getattr(self.chen, "world", None)
            if world is None:
                return 503, {"ok": False, "error": "世界管理器不可用"}

            if name == "refresh":
                return 200, {"ok": True, "snapshot": self.snapshot()}

            if name == "request_action":
                action = payload.get("action", "")
                target = payload.get("target", "")
                if not isinstance(action, str) or not isinstance(target, str):
                    return 400, {"ok": False, "error": "action 和 target 必须是字符串"}
                action = action.strip()
                target = target.strip()
                if action not in self.ACTION_TARGETS:
                    return 400, {"ok": False, "error": "不支持的 action"}
                if target not in self.ACTION_TARGETS[action]:
                    return 400, {"ok": False, "error": "不支持的 action target"}
                raw_params = payload.get("params")
                params = {} if raw_params is None else raw_params
                if not isinstance(params, dict):
                    return 400, {"ok": False, "error": "params 必须是 JSON 对象"}
                if self._pending_action is not None:
                    return 409, {"ok": False, "error": "已有行动正在执行"}
                allowed_params = {"map", "place", "activity", "pose", "duration_frames"}
                if any(key not in allowed_params for key in params):
                    return 400, {"ok": False, "error": "params 含有不支持的字段"}
                clean_params = {}
                for key, value in params.items():
                    if key == "duration_frames":
                        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
                            return 400, {"ok": False, "error": "duration_frames 超出范围"}
                        clean_params[key] = value
                    else:
                        if not isinstance(value, str) or len(value) > self.MAX_TEXT_CHARS:
                            return 400, {"ok": False, "error": f"{key} 必须是短文本"}
                        clean_params[key] = value
                if "pose" in clean_params and clean_params["pose"] not in self.ACTION_POSES:
                    return 400, {"ok": False, "error": "不支持的 pose"}
                request = {
                    "id": uuid.uuid4().hex,
                    "action": action,
                    "target": target,
                    "params": clean_params,
                    "issued_at": datetime.now().isoformat(timespec="seconds"),
                }
                self._pending_action = request
                return 200, {"ok": True, "action": request,
                             "snapshot": self.snapshot()}

            if name == "report_action":
                action_id = payload.get("action_id", "")
                status = payload.get("status", "")
                if not isinstance(action_id, str) or not isinstance(status, str):
                    return 400, {"ok": False, "error": "report_action 参数无效"}
                action_id = action_id.strip()
                status = status.strip()
                if not action_id or len(action_id) > self.MAX_TEXT_CHARS or status not in {"running", "succeeded", "failed"}:
                    return 400, {"ok": False, "error": "report_action 参数无效"}
                pending = self._pending_action
                if pending is None:
                    return 409, {"ok": False, "error": "当前没有待执行行动"}
                if pending.get("id") != action_id:
                    return 409, {"ok": False, "error": "action_id 不是当前行动"}
                supplied_action = payload.get("action")
                supplied_target = payload.get("target")
                if supplied_action is not None and supplied_action != pending.get("action"):
                    return 400, {"ok": False, "error": "action 与当前行动不一致"}
                if supplied_target is not None and supplied_target != pending.get("target"):
                    return 400, {"ok": False, "error": "target 与当前行动不一致"}
                message = payload.get("message", "") or ""
                if not isinstance(message, str) or len(message) > self.MAX_MESSAGE_CHARS:
                    return 400, {"ok": False, "error": "message 过长或类型无效"}
                raw_runtime = payload.get("runtime")
                raw_runtime = {} if raw_runtime is None else raw_runtime
                if not isinstance(raw_runtime, dict):
                    return 400, {"ok": False, "error": "runtime 必须是 JSON 对象"}
                runtime = {}
                for key in ("map", "x", "y", "direction"):
                    if key not in raw_runtime:
                        continue
                    value = raw_runtime[key]
                    if key == "map":
                        if not isinstance(value, str) or len(value) > self.MAX_TEXT_CHARS:
                            return 400, {"ok": False, "error": "runtime.map 无效"}
                    elif isinstance(value, bool) or not isinstance(value, int) or not -10000 <= value <= 10000:
                        return 400, {"ok": False, "error": f"runtime.{key} 无效"}
                    runtime[key] = value
                result = {
                    "id": action_id,
                    "action": pending.get("action", ""),
                    "target": pending.get("target", ""),
                    "status": status,
                    "message": message,
                    "reported_at": datetime.now().isoformat(timespec="seconds"),
                }
                current_intent = self._current_intent or {}
                result["intent_id"] = current_intent.get("intent_id", "")
                result["activity"] = current_intent.get("activity", pending.get("params", {}).get("activity", ""))
                result["place"] = current_intent.get("place", pending.get("params", {}).get("place", ""))
                result["pose"] = current_intent.get("pose", pending.get("params", {}).get("pose", ""))
                self._runtime = runtime
                self._last_action = result
                callback = getattr(self.chen, "on_world_action_result", None)
                if callable(callback) and status in {"succeeded", "failed"}:
                    try:
                        callback(copy.deepcopy(result))
                    except Exception:
                        pass
                if status in {"succeeded", "failed"} and pending and pending.get("id") == action_id:
                    self._pending_action = None
                    self._current_intent = None
                return 200, {"ok": True, "action": result,
                             "snapshot": self.snapshot()}

            if name == "runtime":
                return 200, {"ok": True, "runtime": self.runtime()}

            if name == "inspect":
                target = str(payload.get("target", "") or "").strip()
                targets = {
                    "desk": "desk_items",
                    "wall": "wall_items",
                    "drawer": "drawer_items",
                }
                # 家具目标没有独立持久化物品栏时，仍允许客户端调查，
                # 返回空列表而不是把 Godot 的语义目标拒绝掉。
                if target in self.ROOM_TARGETS and target not in targets:
                    return 200, {"ok": True, "target": target, "items": [],
                                 "revision": self.snapshot()["revision"]}
                key = targets.get(target)
                if not key:
                    return 400, {"ok": False, "error": "不支持的 inspect target"}
                house = world.get_house_state() or {}
                items = self._items(house, key, target)
                return 200, {"ok": True, "target": target, "items": items,
                              "revision": self.snapshot()["revision"]}

            if name == "enter_small_world":
                changed = bool(world.enter_small_world())
                return 200, {"ok": changed, "snapshot": self.snapshot()}

            if name == "leave_small_world":
                changed = bool(world.leave_small_world())
                return 200, {"ok": changed, "snapshot": self.snapshot()}

            return 400, {"ok": False, "error": f"不支持的 command: {name}"}

