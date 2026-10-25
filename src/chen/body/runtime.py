# -*- coding: utf-8 -*-
"""伴生体 Runtime：连接核心、工具和各个客户端的轻量协调层。

第一版只负责统一生命周期、事件和工具入口，不接管现有模块内部逻辑。
这样可以逐步把 UI、手机、桌宠和小世界接入，而不用一次性重写 main.py。
"""

from collections import defaultdict
from datetime import datetime
from threading import RLock


class ChenRuntime:
    MAX_PENDING_REQUESTS = 64

    def __init__(self, chen, tools=None):
        self.chen = chen
        self.tools = tools
        self.started_at = None
        self._running = False
        self._clients = {}
        self._listeners = defaultdict(list)
        self._pending = {}
        self._next_request_id = 1
        self._lock = RLock()

    @property
    def running(self):
        return self._running

    def start(self):
        with self._lock:
            self._running = True
            self.started_at = datetime.now().isoformat(timespec="seconds")
        self.emit("runtime_started", {"at": self.started_at})

    def stop(self):
        with self._lock:
            self._running = False
        self.emit("runtime_stopped", {})

    def register_client(self, client_id, client):
        if not client_id:
            raise ValueError("client_id 不能为空")
        with self._lock:
            self._clients[str(client_id)] = client

    def unregister_client(self, client_id):
        with self._lock:
            self._clients.pop(str(client_id), None)

    def clients(self):
        with self._lock:
            return dict(self._clients)

    def client_status(self, client_id):
        client = self._clients.get(str(client_id))
        if client is None:
            return "未连接"
        try:
            if hasattr(client, "window"):
                return "运行中" if client.window and client.window.winfo_exists() else "未显示"
            if hasattr(client, "_server"):
                return "运行中" if client._server else "未启动"
        except Exception:
            return "异常"
        return "已注册"

    def on(self, event_name, callback):
        if not callable(callback):
            raise TypeError("事件监听器必须是可调用对象")
        with self._lock:
            self._listeners[event_name].append(callback)
        return callback

    def emit(self, event_name, payload=None):
        with self._lock:
            callbacks = tuple(self._listeners.get(event_name, ()))
        for callback in callbacks:
            try:
                callback(payload or {})
            except Exception:
                # 一个客户端失效不能拖垮生命循环。
                continue

    def call_tool(self, tool_id, *, source="user", activity_id="", **kwargs):
        if not self.tools:
            return {"ok": False, "error": "工具层尚未初始化"}
        result = self.tools.execute(tool_id, source=source, **kwargs)
        event = {"tool": tool_id, "result": result, "source": source}
        if activity_id:
            event["activity_id"] = activity_id
        self.emit("tool_result", event)
        if result.get("status") == "confirmation_needed":
            with self._lock:
                if len(self._pending) >= self.MAX_PENDING_REQUESTS:
                    return {"ok": False, "status": "rate_limited",
                            "error": "待确认工具请求过多，请先处理已有请求"}
                request_id = str(self._next_request_id)
                self._next_request_id += 1
                self._pending[request_id] = {
                    "tool": tool_id, "kwargs": kwargs, "source": source,
                    "activity_id": activity_id,
                }
            event["request_id"] = request_id
            self.emit("tool_confirmation_needed", event)
        return result

    def approve_tool(self, request_id, approved):
        with self._lock:
            request = self._pending.pop(str(request_id), None)
        if not request:
            return {"ok": False, "error": "工具请求不存在或已经处理"}
        if not approved:
            result = {"ok": False, "status": "denied", "error": "用户拒绝了工具请求"}
            self.tools.record_event(request["tool"], False, status="denied",
                                     details={"source": "approval",
                                              **self._audit_details(request["kwargs"])})
            self.emit("tool_result", {"tool": request["tool"], "result": result,
                                       "source": "approval"})
            return result
        result = self.tools._execute_confirmed(
            request["tool"], request["kwargs"],
            token=self.tools._confirmation_token, source="approval")
        event = {"tool": request["tool"], "result": result, "source": "approval"}
        if request.get("activity_id"):
            event["activity_id"] = request["activity_id"]
        self.emit("tool_result", event)
        return result

    @staticmethod
    def _audit_details(kwargs):
        """只记录安全摘要，不把查询内容或文件正文写进审计日志。"""
        details = {}
        if "filename" in kwargs:
            details["filename"] = str(kwargs["filename"])[:160]
        if "max_results" in kwargs:
            details["max_results"] = kwargs["max_results"]
        if "query" in kwargs:
            details["query_length"] = len(str(kwargs["query"]))
        return details

    def observe_workspace(self, query=None, max_results=30):
        """低风险文件观察入口，供 UI / 手机端后续调用。"""
        if query:
            return self.call_tool("workspace_search", query=query, max_results=max_results)
        return self.call_tool("workspace_list")

    def snapshot(self):
        presence = {}
        try:
            presence = self.chen.get_presence_snapshot(persist=False) or {}
        except Exception:
            pass
        relation = getattr(getattr(self.chen, "relationship", None), "data", {}) or {}
        autonomy = getattr(self.chen, "autonomy", None)
        autonomy_state = getattr(autonomy, "state", {}) or {}
        return {
            "running": self._running,
            "started_at": self.started_at,
            "clients": list(self._clients),
            "tools": self.tools.describe() if self.tools else [],
            "presence": presence,
            "relationship": {"stage": relation.get("stage"),
                             "interaction_count": relation.get("interaction_count", 0)},
            "autonomy": {
                "enabled": bool(getattr(autonomy, "enabled", False)),
                "actions_today": int(autonomy_state.get("actions", 0) or 0),
                "last_run_at": autonomy_state.get("last_run_at", ""),
                "pending_read": autonomy_state.get("pending_read", ""),
            },
            "pending_requests": len(self._pending),
        }
