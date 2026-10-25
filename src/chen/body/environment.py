# -*- coding: utf-8 -*-
"""
Environment —— 伴生体此刻能感知到的整个世界（0.4 感知层的统一快照）
====================================================================
回答一个问题："伴生体现在能感知到什么？"

各感知模块（perception/weather/hot_trends/life/emotion/world）原本各自为政，
这里把它们聚合成一个快照对象：

- 决策层、prompt 组装、虚拟桌面 UI 都从这里取"环境"，不再各自拼接
- 每轮决策循环刷新一次并落盘 memory/environment.json（原子写），
  虚拟桌面 V1 直接读这个文件，不用再碰各模块内部
- 纯本地聚合（各源自带缓存），零 LLM；以后的 RSS 感知事件也挂这层
"""
import json
import os
from datetime import datetime
from pathlib import Path

from chen.body.time_sense import TimeSense
from chen.body.holiday import Holiday

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class Environment:
    """统一环境快照。构造时注入各感知源（可为 None，缺失则该字段留空）。"""

    def __init__(self, memory_dir, *, perception=None, weather=None,
                 hot_trends=None, world=None, life=None, emotion=None,
                 external_presence=None):
        self.perception = perception
        self.weather = weather
        self.hot_trends = hot_trends
        self.world = world
        self.life = life
        self.emotion = emotion
        # 可传 dict，也可传无参 getter；getter 让手机同步后的最新状态即时可见。
        self.external_presence = external_presence
        self.file = Path(memory_dir) / "environment.json"
        # holidays.json 在 <项目根>/system/ 下（memory_dir 的 parent）
        self.holiday = Holiday(Path(memory_dir).parent / "system")
        self.latest = {}

    # ---------- 主入口 ----------

    def snapshot(self, *, presence="ACTIVE", now=None,
                 app="", detail="", duration_min=0):
        """聚合一次环境快照，落盘，并返回。任何源出错都不影响其他字段。"""
        now = now or datetime.now()
        previous = self.latest or self.load_persisted()
        season = TimeSense.season_of(now)
        snap = {
            "updated_at": now.strftime("%Y-%m-%d %H:%M"),
            "time": now.strftime("%H:%M"),
            "period": TimeSense.period_of_hour(now.hour),
            "weekday": _WEEKDAYS[now.weekday()],
            "season": season,
            "holiday": self._safe(lambda: self.holiday.day_info(now)),
            "presence": presence,
            "user": {
                "app": app or "",
                "detail": detail or "",
                "duration_min": int(duration_min or 0),
            },
            "weather": self._safe(lambda: self.weather.get_weather() if self.weather else None),
            "trends": [],
            "trends_source": "",
            "life": self._life_part(),
            "emotion": self._emotion_part(),
            "world": self._safe(lambda: self.world.current_world if self.world else "desktop"),
        }
        # 热搜（他"注意到外面"的来源之一）
        try:
            if self.hot_trends:
                titles, source = self.hot_trends.get_all(limit=5)
                snap["trends"] = titles or []
                snap["trends_source"] = source or ""
        except Exception:
            pass
        snap["observations"] = self._observations(snap, now)
        # events 保留给 RSS/本地事件总线；当前只提供快照中可判断的变化事件。
        snap["events"] = self._events(previous, snap, now)
        self.latest = snap
        self._persist(snap)
        return snap

    # ---------- 统一观察记录 ----------

    @staticmethod
    def _observation(obs_id, kind, source, now, *, status="ok", summary="", data=None,
                     expires_at=None):
        return {
            "id": obs_id, "kind": kind, "source": source,
            "observed_at": now.isoformat(timespec="seconds"),
            "expires_at": expires_at, "status": status,
            "summary": summary, "data": data or {},
        }

    def _observations(self, snap, now):
        """把既有来源收口为可追溯的最小观察记录。单个来源失败不影响其它来源。"""
        observations = [self._observation(
            "time.current", "time", "本地时钟", now,
            summary=f"{snap['weekday']}{snap['period']} {snap['time']}",
            data={"time": snap["time"], "weekday": snap["weekday"],
                  "period": snap["period"], "season": snap["season"],
                  "holiday": snap.get("holiday") or {}})]

        if self.perception:
            try:
                app, minutes, detail = self.perception.get_current_app_status()
                status, away_sec = self.perception.get_presence_status()
                observations.append(self._observation(
                    "computer.presence", "computer", "Perception", now,
                    summary=("用户在电脑前" if status == "ACTIVE" else "用户暂时离开电脑"),
                    data={"presence": status, "away_seconds": away_sec,
                          "app": app or "", "detail": detail or "",
                          "duration_min": int(minutes or 0)}))
            except Exception as exc:
                observations.append(self._observation(
                    "computer.presence", "computer", "Perception", now,
                    status="unavailable", summary="电脑状态暂不可用",
                    data={"error": type(exc).__name__}))

        weather = snap.get("weather")
        observations.append(self._observation(
            "weather.current", "weather", "Open-Meteo", now,
            status="ok" if weather else "unavailable",
            summary=str(weather or "天气暂不可用"), data={"weather": weather} if weather else {}))

        trends = snap.get("trends") or []
        observations.append(self._observation(
            "web.trends", "web", snap.get("trends_source") or "热搜缓存", now,
            status="ok" if trends else "unavailable",
            summary="；".join(str(x) for x in trends[:5]) if trends else "暂无网络话题",
            data={"items": trends[:5]}))

        presence = self._external_presence()
        devices = presence.get("devices", {}) if isinstance(presence, dict) else {}
        if isinstance(devices, dict):
            for device, state in devices.items():
                if not isinstance(state, dict):
                    continue
                phone_status = "ok"
                updated_at = state.get("updated_at")
                try:
                    if updated_at and (now - datetime.fromisoformat(updated_at)).total_seconds() > 6 * 3600:
                        phone_status = "stale"
                except (TypeError, ValueError):
                    phone_status = "stale"
                observations.append(self._observation(
                    f"phone.{device}", "phone", "用户主动同步", now,
                    status=phone_status,
                    summary=str(state.get("activity") or "手机状态已同步"),
                    data={k: v for k, v in state.items() if k != "device"}))
        return observations

    def _external_presence(self):
        try:
            value = self.external_presence() if callable(self.external_presence) else self.external_presence
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _events(self, previous, snap, now):
        """只报告相邻快照之间的变化；首次启动不把全部现状伪装成新事件。"""
        if not isinstance(previous, dict) or not previous:
            return []
        events = []
        old_obs = {x.get("id"): x for x in previous.get("observations", []) if isinstance(x, dict)}
        new_obs = {x.get("id"): x for x in snap.get("observations", []) if isinstance(x, dict)}
        old_presence = previous.get("presence")
        if old_presence and old_presence != snap.get("presence"):
            label = "回到电脑前" if snap.get("presence") == "ACTIVE" else "离开电脑"
            old_computer = old_obs.get("computer.presence") or {}
            new_computer = new_obs.get("computer.presence") or {}
            old_data = old_computer.get("data") or {}
            new_data = new_computer.get("data") or {}
            # 回到电脑前时，保留离开期间的时长，供决策层沿用原有 USER_BACK 规则。
            away_seconds = int(old_data.get("away_seconds") or 0) if snap.get("presence") == "ACTIVE" \
                else int(new_data.get("away_seconds") or 0)
            events.append(self._event("computer", "presence_changed", label, now,
                                      attention="decision_candidate",
                                      data={"from": old_presence, "to": snap.get("presence"),
                                            "away_seconds": max(0, away_seconds),
                                            "away_minutes": max(0, away_seconds // 60)}))

        old_weather = previous.get("weather")
        new_weather = snap.get("weather")
        if old_weather and new_weather and old_weather != new_weather:
            events.append(self._event("weather", "weather_changed",
                                      f"天气从{old_weather}变成{new_weather}", now,
                                      attention="notice",
                                      data={"from": old_weather, "to": new_weather}))

        old_items = set(previous.get("trends") or [])
        for item in (snap.get("trends") or []):
            if item and item not in old_items:
                events.append(self._event("web", "new_trend", str(item), now,
                                          attention="trace",
                                          data={"title": str(item), "source": snap.get("trends_source", "")}))

        for obs in snap.get("observations", []):
            if obs.get("kind") != "phone" or obs.get("status") not in ("ok", "stale"):
                continue
            old = old_obs.get(obs.get("id"))
            if not old:
                continue
            old_data = old.get("data") or {}
            new_data = obs.get("data") or {}
            keys = ("activity", "place", "app", "focus", "wifi", "music_title", "music_artist", "charging")
            changed = {k: {"from": old_data.get(k), "to": new_data.get(k)}
                       for k in keys if old_data.get(k) != new_data.get(k)}
            if changed:
                meaningful = any(k in changed for k in ("activity", "place", "app", "focus", "wifi"))
                events.append(self._event(
                    "phone", "phone_state_changed", obs.get("summary", "手机状态变化"), now,
                    attention="notice" if meaningful else "trace", data=changed))
        return events[:8]

    @staticmethod
    def _event(kind, event_type, summary, now, *, attention="trace", data=None):
        """attention: trace=只留痕，notice=进入可见关注队列，decision_candidate=可交决策层评估。"""
        rationale = {
            "trace": "记录变化，不触发主动表达",
            "notice": "值得保留并供状态界面展示，不直接触发主动表达",
            "decision_candidate": "可由既有决策规则评估；本事件本身不授权主动表达",
        }
        return {"kind": kind, "type": event_type,
                "observed_at": now.isoformat(timespec="seconds"),
                "summary": summary, "attention": attention,
                "attention_reason": rationale.get(attention, rationale["trace"]),
                "data": data or {}}

    # ---------- 各部分 ----------

    def _life_part(self):
        if not self.life:
            return {}
        try:
            st = self.life.state
            return {
                "awake": st.get("awake", True),
                "activity": st.get("activity", ""),
                "energy": int(st.get("energy", 0)),
                "lived_desc": self.life.lived_desc(),
            }
        except Exception:
            return {}

    def _emotion_part(self):
        if not self.emotion:
            return {}
        try:
            name, val = self.emotion.dominant()
            return {"dominant": name or "", "intensity": round(val, 2)}
        except Exception:
            return {}

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return None

    def _persist(self, snap):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.file)
        except Exception:
            pass  # 落盘失败绝不影响主流程

    # ---------- 给虚拟桌面/调试用 ----------

    def load_persisted(self):
        """读最近一次落盘的快照（UI 侧用；读坏返回空 dict）。"""
        try:
            if self.file.exists():
                return json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def headline(self, snap=None):
        """一行环境摘要，给日志/调试用。"""
        s = snap or self.latest
        if not s:
            return ""
        parts = [f"{s.get('weekday')}{s.get('period')}{s.get('time')}"]
        _season = s.get("season") or {}
        if _season.get("label"):
            parts.append(str(_season["label"]))
        _hol = s.get("holiday") or {}
        if _hol.get("type") in ("holiday", "makeup_workday", "winter_break", "summer_break"):
            parts.append(str(_hol.get("label", "")))
        if s.get("weather"):
            parts.append(str(s["weather"]))
        _life = s.get("life") or {}
        if _life.get("awake") is False:
            parts.append("他在睡觉")
        elif _life.get("activity"):
            parts.append(_life["activity"])
        if s.get("presence") == "AWAY":
            parts.append("用户不在电脑前")
        return " · ".join(parts)


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()

    class _Life:
        state = {"awake": True, "activity": "在屏幕里看书", "energy": 62.0}
        def lived_desc(self):
            return "活了1天3小时"

    class _Emo:
        def dominant(self):
            return "想念", 0.55

    class _World:
        current_world = "desktop"

    class _Trends:
        def get_all(self, limit=10):
            return ["AI 新模型发布", "某剧大结局"], "抖音热搜"

    class _Weather:
        def get_weather(self):
            return "多云"

    env = Environment(tmp, perception=None, weather=_Weather(),
                      hot_trends=_Trends(), world=_World(), life=_Life(),
                      emotion=_Emo())
    expected_weather = _Weather().get_weather()
    snap = env.snapshot(presence="ACTIVE", app="VS Code", duration_min=35)
    print("快照:", json.dumps(snap, ensure_ascii=False, indent=1))
    print("摘要:", env.headline())
    # 落盘读回
    # 和测试天气源自身比较；受限环境无法写临时目录时只跳过落盘断言。
    persisted = env.load_persisted()
    if persisted:
        assert persisted.get("weather") == expected_weather
        print("落盘读回 OK")
    else:
        print("落盘读回跳过（当前环境不允许写临时目录）")
