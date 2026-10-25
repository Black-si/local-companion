"""伴生体在小世界里的低风险行动决策。

WorldMind 只决定语义行动，不操作坐标，也不直接调用 Godot。它把精力、
心境、天气和房间生活痕迹压成一个可解释的意图，交给 WorldBridge 执行。
规则是确定性的，后续可以让模型润色理由，但不能绕过这里的行动白名单。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


class WorldMind:
    """小世界行动的意图层。"""

    def __init__(self, chen=None):
        self.chen = chen
        self.current_intent: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self._failed_activities: set[str] = set()

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _number(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _elapsed_minutes(value: Any) -> float:
        if not value:
            return 999.0
        try:
            started = datetime.fromisoformat(str(value))
            return max(0.0, (datetime.now() - started).total_seconds() / 60.0)
        except (TypeError, ValueError):
            return 999.0

    @staticmethod
    def _intent_id(activity: str, place: str, reason: str) -> str:
        raw = f"{activity}|{place}|{reason}"
        return "world-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _make(self, *, activity: str, place: str, target: str,
              reason: str, priority: int, duration_frames: int) -> dict[str, Any]:
        pose = self._pose_for_activity(activity)
        intent = {
            "intent_id": self._intent_id(activity, place, reason),
            "activity": activity,
            "place": place,
            "target": target,
            "pose": pose,
            "reason": reason,
            "priority": max(0, min(100, int(priority))),
            "duration_frames": max(30, min(3600, int(duration_frames))),
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.current_intent = intent
        return dict(intent)

    @staticmethod
    def _pose_for_activity(activity: str) -> str:
        """把自然语言活动收敛到 Godot/桌宠共用的动作白名单。"""
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
        if any(word in text for word in ("洗漱", "刷牙", "洗澡", "泡澡")):
            return "wash"
        if any(word in text for word in ("浇花", "浇水", "盆栽", "绿植")):
            return "plant"
        if any(word in text for word in ("沙发", "窝着", "躺椅")):
            return "sofa"
        if any(word in text for word in ("拉伸", "伸懒腰", "活动筋骨", "晒太阳", "晾衣")):
            return "stretch"
        if any(word in text for word in ("睡觉", "睡", "眠")):
            return "sleep"
        if any(word in text for word in ("手机", "刷视频", "聊天")):
            return "phone"
        if any(word in text for word in ("看书", "阅读", "写", "学习")):
            return "book"
        if any(word in text for word in ("坐", "发呆", "窗边", "长椅", "看云", "听水")):
            return "sit"
        return "walk"

    def decide(self, *, world_state: dict[str, Any] | None = None,
               life_state: dict[str, Any] | None = None,
               mood: str = "无特别想法", emotion: str = "",
               emotion_value: float = 0.0) -> dict[str, Any] | None:
        """根据当前状态选择一个可执行意图。

        选择顺序体现需要优先级：睡意/低精力 > 房间维护 > 明确心境 > 天气 > 默认活动。
        相同输入会得到相同选择；失败过的行动会暂时避开。
        """
        state = world_state or {}
        life = life_state or {}
        if state.get("current_world") != "small_world":
            self.current_intent = None
            return None

        activity_now = self._text(state.get("activity"))
        place_now = self._text(state.get("activity_place"))
        activity_elapsed = self._elapsed_minutes(state.get("activity_started_at"))
        weather = self._text(state.get("weather"))
        period = self._text(state.get("time_period"))
        house = state.get("house_state") if isinstance(state.get("house_state"), dict) else {}
        environment = state.get("environment") if isinstance(state.get("environment"), dict) else {}
        energy = self._number(life.get("energy"), 80.0)
        awake = bool(life.get("awake", True))
        mood = self._text(mood) or "无特别想法"
        emotion = self._text(emotion)
        emotion_value = self._number(emotion_value, 0.0)

        if not awake:
            return self._make(activity="睡觉", place="家里", target="bed",
                              reason="已经睡着了，先把休息完成", priority=100,
                              duration_frames=360)

        candidates: list[tuple[int, str, str, str, str, int]] = []
        if energy <= 25:
            candidates.append((100, "在家里躺着", "家里", "bed",
                               "精力已经很低了，先休息一会儿", 360))
        if energy <= 42:
            candidates.append((88, "在家里窗边待着", "家里", "window",
                               "不想继续消耗精力，去窗边安静待着", 240))

        if energy <= 65:
            candidates.append((57, "在家里喝点水", "家里", "desk",
                               "先喝点水，身体会舒服一点", 150))

        if emotion in {"开心", "安心"} and emotion_value >= 0.35:
            candidates.append((56, "在家里听音乐", "家里", "desk",
                               "心情还可以，放点音乐陪着自己", 240))

        if period in {"早晨", "上午"} and energy >= 65:
            candidates.append((56, "在家里拉伸一下", "家里", "window",
                               "刚开始一天，先活动一下身体", 150))

        cleanliness = self._number(environment.get("cleanliness"), 72)
        bed_made = bool(house.get("bed_made", True))
        if cleanliness < 38 or not bed_made:
            candidates.append((82, "在家里整理东西", "家里", "desk",
                               "房间有些乱了，先把眼前的生活痕迹收一收", 240))

        quiet = mood in {"想安静", "自己待着"} or (
            emotion in {"烦闷", "委屈"} and emotion_value >= 0.45
        )
        if quiet:
            if weather in {"小雨", "中雨", "大雨", "阵雨", "雷暴", "雾"}:
                candidates.append((78, "在家里窗边听雨", "家里", "window",
                                   "现在只想安静一点，窗边的声音刚好", 300))
            else:
                candidates.append((78, "在家里窗边待着", "家里", "window",
                                   "现在只想安静一点，去窗边看一会儿", 300))

        if weather in {"小雨", "中雨", "大雨", "阵雨", "雷暴", "冻雨", "小雪", "中雪", "大雪"}:
            candidates.append((70, "在家里窗边听雨", "家里", "window",
                               "外面天气不适合久待，留在窗边更舒服", 300))
        elif period in {"傍晚", "晚上", "深夜"}:
            candidates.append((68, "在家里窗边待着", "家里", "window",
                               "天色慢下来，去窗边看一会儿", 240))

        candidates.extend([
            (55, "在家里看书", "家里", "desk", "想做一点安静而具体的事", 300),
            (48, "在家里用电脑", "家里", "desk", "还有精神，先处理一点手边的事", 240),
        ])

        # 天气晴好且精力充足时，把小院、其他房间与室外散步纳入候选，
        # 让伴生体能走出卧室（触发室外发现、拍照与 Godot 跨图寻路）。
        bad_outdoor_weather = {"小雨", "中雨", "大雨", "阵雨", "雷暴", "冻雨", "小雪", "中雪", "大雪"}
        if weather not in bad_outdoor_weather and energy >= 50:
            candidates.extend([
                (54, "在小院里给植物浇水", "小院", "watering", "去院子里透透气，顺手看看植物", 240),
                (53, "在树下坐着乘凉", "树下", "rest_spot", "外面天气不错，去树下安静待一会儿", 260),
                (52, "在河边看水慢慢流", "河边", "lookout", "想去河边走走，听听水声", 260),
                (51, "在小路上散步", "小路", "scene_center", "顺着小路随便走走，换换脑子", 240),
                (50, "在田野边慢慢走", "田野", "scene_center", "去开阔的地方走一走", 240),
                (49, "在山顶看远处的风景", "山顶", "lookout", "想去高处吹吹风、看看远处", 260),
                (47, "在客厅沙发上靠一会儿", "客厅", "sofa", "去客厅沙发上松一口气", 220),
                (46, "在阳台吹会儿风", "阳台", "balcony_rail", "去阳台上看看外面的天色", 200),
            ])

        has_started_ts = bool(state.get("activity_started_at"))
        # 刚进入某项活动未满 12 分钟且无高优生理/整理需求时，保持当前活动不频繁打断
        if has_started_ts and activity_now and place_now and activity_elapsed < 12.0:
            top_priority = max((c[0] for c in candidates), default=0)
            if top_priority < 75 and activity_now not in self._failed_activities:
                for candidate in candidates:
                    if candidate[1] == activity_now:
                        return self._make(activity=candidate[1], place=candidate[2],
                                          target=candidate[3], reason=candidate[4],
                                          priority=candidate[0], duration_frames=candidate[5])
                from chen.behavior.world_bridge import WorldBridge
                inferred_target = WorldBridge._target_for_activity(place_now, activity_now) or "scene_center"
                return self._make(activity=activity_now, place=place_now,
                                  target=inferred_target, reason="继续眼下正在做的事",
                                  priority=60, duration_frames=240)

        # 上一个行动完成后保持一段时间；只有状态确实改变时才换意图。
        if (self.last_result and self.last_result.get("status") == "succeeded"
                and activity_elapsed < 8.0):
            last_activity = self._text(self.last_result.get("activity"))
            if last_activity and last_activity == activity_now and activity_now not in self._failed_activities:
                for candidate in candidates:
                    if candidate[1] == activity_now:
                        return self._make(activity=candidate[1], place=candidate[2],
                                          target=candidate[3], reason=candidate[4],
                                          priority=candidate[0], duration_frames=candidate[5])

        # 当存在真实计时且当前日常活动已持续 >= 15 分钟时，按时间片轮转到下一个候选（含室外），
        # 避免永远停在同一个最高分日常动作上。
        rotate_offset = 0
        if has_started_ts and activity_elapsed >= 15.0:
            seed_str = f"{state.get('activity_started_at', '')}|{activity_now}|{place_now}"
            rotate_offset = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:6], 16)

        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        valid = [c for c in candidates if c[1] not in self._failed_activities]
        if valid:
            top_p = valid[0][0]
            if rotate_offset and top_p < 75:
                routinePool = [c for c in valid if c[0] < 75 and c[1] != activity_now] or valid
                chosen = routinePool[rotate_offset % len(routinePool)]
            else:
                chosen = valid[0]
            priority, activity, place, target, reason, duration = chosen
            return self._make(activity=activity, place=place, target=target,
                              reason=reason, priority=priority,
                              duration_frames=duration)
        return self._make(activity="在家里窗边待着", place="家里", target="window",
                          reason="暂时没有更合适的安排，先安静待着", priority=20,
                          duration_frames=180)

    def handle_action_result(self, result: dict[str, Any] | None) -> None:
        """接收 Godot 回报，让下一次选择知道行动是否完成。"""
        if not isinstance(result, dict):
            return
        self.last_result = dict(result)
        activity = self._text(result.get("activity"))
        status = self._text(result.get("status"))
        if status == "failed" and activity:
            self._failed_activities.add(activity)
        elif status == "succeeded" and activity:
            self._failed_activities.discard(activity)

