# -*- coding: utf-8 -*-
"""按需工具和模型调用策略。默认低负载，不做常驻扫描。"""

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class RuntimeLimits:
    tool_timeout_seconds: int = 20
    max_tool_calls_per_minute: int = 12
    max_external_calls_per_day: int = 30
    max_external_tokens_per_call: int = 300
    background_interval_seconds: int = 1800
    mcp_autostart: bool = False


class RuntimePolicy:
    """轻量限流器；只在调用发生时占用少量内存和 CPU。"""

    def __init__(self, limits=None, state_path=None):
        self.limits = limits or RuntimeLimits()
        self._lock = Lock()
        self._tool_calls = []
        self._external_calls = 0
        self._external_day = date.today()
        self._state_path = Path(state_path) if state_path else None
        if self._state_path and self._state_path.exists():
            try:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                if state.get("date") == self._external_day.isoformat():
                    self._external_calls = max(0, int(state["calls"]))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # 无法确认当天已用额度时保守处理，避免损坏文件导致额外付费。
                self._external_calls = self.limits.max_external_calls_per_day

    def allow_tool_call(self, now=None):
        now = monotonic() if now is None else float(now)
        with self._lock:
            self._tool_calls = [t for t in self._tool_calls if now - t < 60]
            if len(self._tool_calls) >= self.limits.max_tool_calls_per_minute:
                return False
            self._tool_calls.append(now)
            return True

    def allow_external_call(self):
        with self._lock:
            today = date.today()
            if today != self._external_day:
                self._external_day = today
                self._external_calls = 0
            if self._external_calls >= self.limits.max_external_calls_per_day:
                return False
            self._external_calls += 1
            if self._state_path:
                try:
                    self._state_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self._state_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"date": today.isoformat(),
                                               "calls": self._external_calls}), encoding="utf-8")
                    os.replace(tmp, self._state_path)
                except OSError:
                    self._external_calls -= 1
                    return False
            return True

    @staticmethod
    def clamp_tokens(requested, maximum):
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            requested = maximum
        return max(1, min(requested, int(maximum)))
