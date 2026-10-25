# -*- coding: utf-8 -*-
"""伴生体的统一工具层。

工具层把“能力是什么”和“现在能不能执行”分开：
- observe：读取或感知，不改变环境
- suggest：生成建议，等待下一步确认
- execute：可以改变环境，必须由权限配置允许

这里先提供轻量注册表，不主动扩大现有权限。具体模块可以逐步注册真实处理函数。
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


OBSERVE = "observe"
SUGGEST = "suggest"
EXECUTE = "execute"
_LEVELS = {OBSERVE, SUGGEST, EXECUTE}


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    category: str
    description: str
    level: str = OBSERVE
    enabled: bool = True
    requires_confirmation: bool = False
    handler: Optional[Callable[..., Any]] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.level not in _LEVELS:
            raise ValueError(f"未知工具权限等级：{self.level}")


class ToolRegistry:
    """统一的工具描述、查询和执行入口。"""

    def __init__(self, tools_backend=None, permission_getter=None,
                 confirmation_getter=None, audit_path=None, policy=None):
        self.tools_backend = tools_backend
        self.permission_getter = permission_getter or (lambda _tool_id: True)
        self.confirmation_getter = confirmation_getter or (lambda _tool_id, _kwargs: False)
        self.audit_path = Path(audit_path) if audit_path else None
        self.policy = policy
        self._tools: Dict[str, ToolSpec] = {}
        # 确认后的执行只能由 Runtime 的待确认请求恢复，不能由调用者在
        # 普通 kwargs 里伪造一个 `_confirmed=True`。
        self._confirmation_token = object()

    def register(self, spec: ToolSpec):
        if spec.id in self._tools:
            raise ValueError(f"工具已注册：{spec.id}")
        self._tools[spec.id] = spec
        return spec

    def add(self, tool_id, name, category, description, level=OBSERVE,
            handler=None, enabled=True, requires_confirmation=False):
        return self.register(ToolSpec(tool_id, name, category, description,
                                      level=level, enabled=enabled, handler=handler,
                                      requires_confirmation=requires_confirmation))

    def get(self, tool_id):
        return self._tools.get(tool_id)

    def all(self) -> Iterable[ToolSpec]:
        return tuple(self._tools.values())

    def by_category(self, category):
        return tuple(tool for tool in self._tools.values() if tool.category == category)

    def describe(self):
        return [
            {"id": tool.id, "name": tool.name, "category": tool.category,
             "description": tool.description, "level": tool.level,
             "enabled": tool.enabled,
             "requires_confirmation": tool.requires_confirmation}
            for tool in self._tools.values()
        ]

    def can_use(self, tool_id):
        tool = self.get(tool_id)
        return bool(tool and tool.enabled and self.permission_getter(tool_id))

    def execute(self, tool_id, *, source="user", **kwargs):
        """公开工具入口。

        `_confirmed` 等内部字段被明确拒绝，避免 UI、聊天或插件通过参数
        伪造已经获得的用户确认。
        """
        if any(key.startswith("_") for key in kwargs):
            return self._result(False, tool_id, error="工具请求包含内部保留参数",
                                status="invalid_request", source=source)
        return self._execute(tool_id, kwargs, source=source, confirmation_granted=False)

    def _execute_confirmed(self, tool_id, kwargs, *, token, source="approval"):
        """Runtime 专用的确认恢复入口。"""
        if token is not self._confirmation_token:
            return self._result(False, tool_id, error="无效的工具确认凭据",
                                status="invalid_request", source=source)
        if not isinstance(kwargs, dict) or any(key.startswith("_") for key in kwargs):
            return self._result(False, tool_id, error="工具请求包含内部保留参数",
                                status="invalid_request", source=source)
        return self._execute(tool_id, dict(kwargs), source=source, confirmation_granted=True)

    def _execute(self, tool_id, kwargs, *, source, confirmation_granted):
        tool = self.get(tool_id)
        if not tool:
            return self._result(False, tool_id, error=f"未知工具：{tool_id}", source=source)
        if not tool.enabled:
            return self._result(False, tool_id, error=f"工具未启用：{tool.name}", source=source)
        if not self.permission_getter(tool_id):
            return self._result(False, tool_id, error=f"权限不足：{tool.name}", source=source)
        if self.policy and not self.policy.allow_tool_call():
            return self._result(False, tool_id, error="工具调用过于频繁，请稍后再试",
                                status="rate_limited", source=source)
        if (tool.requires_confirmation and not confirmation_granted
                and not self.confirmation_getter(tool_id, kwargs)):
            return self._result(False, tool_id, error=f"需要确认后才能执行：{tool.name}",
                                status="confirmation_needed", source=source)
        handler = tool.handler
        if handler is None and self.tools_backend is not None:
            handler = getattr(self.tools_backend, tool_id, None)
        if handler is None:
            return self._result(False, tool_id, error=f"工具尚未接入：{tool.name}", source=source)
        try:
            return self._result(True, tool_id, result=handler(**kwargs), source=source)
        except Exception as exc:
            return self._result(False, tool_id, error=f"工具执行失败：{exc}", source=source)

    def _result(self, ok, tool_id, result=None, error=None, status=None, source="user"):
        self.record_event(tool_id, ok, status=status or ("success" if ok else "error"),
                          error=error, details={"source": source} if source else None)
        response = {"ok": bool(ok)}
        if result is not None:
            response["result"] = result
        if error:
            response["error"] = str(error)
        if status:
            response["status"] = status
        return response

    def record_event(self, tool_id, ok, status=None, error=None, details=None):
        entry = {"at": datetime.now().isoformat(timespec="seconds"),
                 "tool": tool_id, "ok": bool(ok)}
        if status:
            entry["status"] = status
        if error:
            entry["error"] = str(error)
        if details:
            entry["details"] = details
        self._audit(entry)

    def _audit(self, entry):
        if not self.audit_path:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def build_default_registry(tools_backend=None, audit_path=None, network_handler=None,
                           computer_handler=None, world_handler=None, world_touch_handler=None,
                           permission_getter=None, confirmation_getter=None, policy=None):
    """建立伴生体五类能力器官的第一版注册表。"""
    registry = ToolRegistry(tools_backend=tools_backend, audit_path=audit_path,
                            permission_getter=permission_getter,
                            confirmation_getter=confirmation_getter,
                            policy=policy)
    registry.add("network_observe", "网络观察", "网络",
                 "读取允许的天气、RSS、公开网页和 API 信息。", OBSERVE,
                 handler=network_handler)
    registry.add("file_read", "文件读取", "文件",
                 "在允许的目录内查找和读取文件。", OBSERVE,
                 handler=getattr(tools_backend, "read_file", None))
    registry.add("workspace_list", "工作区观察", "文件",
                 "列出允许 workspace 内的文件和目录，不读取文件内容。", OBSERVE,
                 handler=getattr(tools_backend, "list_workspace", None))
    registry.add("workspace_search", "工作区搜索", "文件",
                 "在 workspace 指定文本文件中搜索关键词。", OBSERVE,
                 handler=getattr(tools_backend, "search_workspace", None))
    registry.add("file_write", "文件整理", "文件",
                 "在 workspace 内创建或整理伴生体自己的文件。", EXECUTE,
                 handler=getattr(tools_backend, "write_file", None))
    registry.add("chen_note_write", "写随笔", "文件",
                 "在 workspace/chen_notes/ 专属目录内写下伴生体自己的随笔或笔记。", EXECUTE,
                 handler=getattr(tools_backend, "write_chen_note", None))
    registry.add("chen_note_organize", "整理随笔目录", "文件",
                 "整理 workspace/chen_notes/ 下的随笔并更新目录索引 index.md。", EXECUTE,
                 handler=getattr(tools_backend, "organize_chen_notes", None))
    registry.add("computer_observe", "电脑状态", "电脑",
                 "读取允许的应用、窗口和运行状态摘要。", OBSERVE,
                 handler=computer_handler)
    registry.add("world_interact", "小世界互动", "小世界",
                 "读取或改变小世界中的对象和地点状态。", SUGGEST,
                 handler=world_handler)
    registry.add("world_state_touch", "小世界轻互动", "小世界",
                 "在独处时整理房间、照看窗台植物或微调小世界物件状态。", EXECUTE,
                 handler=world_touch_handler)
    registry.add("phone_message", "手机消息", "手机",
                 "通过局域网向已授权的 iPhone / iPad 发送状态或消息。", EXECUTE,
                 requires_confirmation=True)
    return registry
