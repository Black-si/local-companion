# -*- coding: utf-8 -*-
"""伴生体的低频自主活动层。

这里借鉴了 Pippin 的“状态 → 活动 → 结果”边界，以及 MaiBot/AIRI 的
“事件和工具分层”思路，但不把模型直接暴露给无限工具列表。

第一阶段只做低风险、只读活动：在用户离开电脑并且冷却到期时，伴生体可以
观察自己的 workspace；如果下一轮仍然适合独处，再读取一个新文本文件。
每轮最多一个工具调用，结果只写入伴生体自己的经历日志和自主状态文件。
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path


AUTONOMY_SOURCE = "internal_autonomy"


@dataclass(frozen=True)
class ActivityContext:
    """一次自主活动选择所能看到的最小状态。"""

    now: datetime = field(default_factory=datetime.now)
    presence: str = "ACTIVE"
    gap_minutes: float = 0.0
    mood: str = "无特别想法"
    energy: float = 100.0
    awake: bool = True

    @property
    def alone(self):
        return self.presence == "AWAY" or self.gap_minutes >= 20


@dataclass(frozen=True)
class Activity:
    id: str
    tool_id: str
    label: str
    description: str


@dataclass(frozen=True)
class ActivityDecision:
    activity: Activity | None
    allowed: bool
    reason: str
    kwargs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExperienceRecord:
    at: str
    activity_id: str
    tool_id: str
    ok: bool
    summary: str
    reason: str


class AutonomyEngine:
    """一次心跳最多执行一个受限工具的自主行动选择器（第一阶段观察 + 第二阶段随笔/整理/小世界轻互动）。"""

    WORKSPACE_OBSERVE = Activity(
        "workspace_observe", "workspace_list", "看看工作区",
        "观察允许的 workspace 文件列表，不读取内容。",
    )
    WORKSPACE_READ = Activity(
        "workspace_read_new", "file_read", "读一份新文件",
        "读取 workspace 中一份新出现的文本文件。",
    )
    COMPUTER_OBSERVE = Activity(
        "computer_observe", "computer_observe", "看看电脑状态",
        "读取应用和在离开状态摘要，不读取屏幕内容。",
    )
    WRITE_NOTE = Activity(
        "write_note", "chen_note_write", "写一段随笔",
        "在伴生体专属的 workspace/chen_notes/ 目录下写一段简短随笔或记录。",
    )
    ORGANIZE_NOTES = Activity(
        "organize_notes", "chen_note_organize", "整理随笔目录",
        "整理 workspace/chen_notes/ 目录并更新索引 index.md。",
    )
    WORLD_TOUCH = Activity(
        "world_touch", "world_state_touch", "收拾房间或照看植物",
        "在独处时轻轻整理小世界房间、给植物浇水或留下一件小物件。",
    )
    WRITE_ACTIVITY_IDS = {"write_note", "organize_notes", "world_touch"}

    def __init__(self, runtime, state_path, *, day_log=None,
                 interval_seconds=1800, max_actions_per_day=4,
                 max_write_actions_per_day=2,
                 enabled=False, planner=None, note_generator=None):
        self.runtime = runtime
        self.state_path = Path(state_path)
        self.day_log = day_log
        self.interval_seconds = max(60, int(interval_seconds))
        self.max_actions_per_day = max(1, int(max_actions_per_day))
        self.max_write_actions_per_day = max(1, int(max_write_actions_per_day))
        self.enabled = bool(enabled)
        # planner 只能返回候选 Activity 的 id 或 None；它没有工具执行权限。
        self.planner = planner
        self.note_generator = note_generator
        self.state = self._load()

    def _load(self):
        default = {
            "date": datetime.now().date().isoformat(),
            "actions": 0,
            "write_actions": 0,
            "wrote_note_today": False,
            "organized_today": False,
            "world_touched_today": False,
            "last_run_at": "",
            "seen_workspace": [],
            "pending_read": "",
            "history": [],
            "initialized": False,
        }
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                default.update(loaded)
        except (OSError, ValueError, TypeError):
            pass
        if default.get("date") != datetime.now().date().isoformat():
            default["date"] = datetime.now().date().isoformat()
            default["actions"] = 0
            default["write_actions"] = 0
            default["wrote_note_today"] = False
            default["organized_today"] = False
            default["world_touched_today"] = False
        return default

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            os.replace(temporary, self.state_path)
        except OSError:
            pass

    def _reset_day_if_needed(self, now):
        today = now.date().isoformat()
        if self.state.get("date") != today:
            self.state.update({
                "date": today, "actions": 0, "write_actions": 0,
                "wrote_note_today": False, "organized_today": False,
                "world_touched_today": False, "history": [],
                "pending_read": "", "initialized": False,
            })

    def _cooldown_ready(self, now):
        value = self.state.get("last_run_at")
        if not value:
            return True
        try:
            previous = datetime.fromisoformat(value)
            return (now - previous).total_seconds() >= self.interval_seconds
        except (TypeError, ValueError):
            return True

    def decide(self, context: ActivityContext):
        self._reset_day_if_needed(context.now)
        if not self.enabled:
            return ActivityDecision(None, False, "自主工具活动默认关闭")
        if not context.awake:
            return ActivityDecision(None, False, "正在睡觉")
        if not context.alone:
            return ActivityDecision(None, False, "用户还在电脑前，暂不打扰")
        if context.energy < 15:
            return ActivityDecision(None, False, "精力太低，先休息")
        if not self._cooldown_ready(context.now):
            return ActivityDecision(None, False, "自主活动仍在冷却")
        if int(self.state.get("actions", 0)) >= self.max_actions_per_day:
            return ActivityDecision(None, False, "今天的自主活动额度已用完")

        pending = str(self.state.get("pending_read") or "").strip()
        write_used = int(self.state.get("write_actions", 0) or 0)
        can_write_more = write_used < self.max_write_actions_per_day
        already_observed = bool(self.state.get("initialized"))
        stage2_candidates = []
        if can_write_more and self.runtime.tools:
            if (not self.state.get("wrote_note_today")
                    and context.energy >= 30
                    and self.runtime.tools.can_use("chen_note_write")):
                stage2_candidates.append((
                    self.WRITE_NOTE, {},
                    "独处时在自己的随笔夹里写几句话",
                ))
            if (self.state.get("wrote_note_today")
                    and not self.state.get("organized_today")
                    and context.energy >= 20
                    and self.runtime.tools.can_use("chen_note_organize")):
                stage2_candidates.append((
                    self.ORGANIZE_NOTES, {},
                    "顺手把随笔目录整理一下",
                ))
            if (not self.state.get("world_touched_today")
                    and context.energy >= 20
                    and self.runtime.tools.can_use("world_state_touch")):
                stage2_candidates.append((
                    self.WORLD_TOUCH, {},
                    "独处时收拾一下房间或看看窗台植物",
                ))

        candidates = []
        if pending and self.runtime.tools and self.runtime.tools.can_use("file_read"):
            candidates.append((self.WORKSPACE_READ, {"filename": pending},
                               f"工作区出现新文件，想读一眼：{pending}"))
        else:
            # 第一轮先观察工作区；已经观察过且无新文件待读时，优先候选第二阶段自主小活动
            if already_observed and stage2_candidates:
                candidates.extend(stage2_candidates)
            if self.runtime.tools and self.runtime.tools.can_use("workspace_list"):
                candidates.append((self.WORKSPACE_OBSERVE, {},
                                   "独处时看看工作区有没有新东西"))
            if not already_observed and stage2_candidates:
                candidates.extend(stage2_candidates)
            if self.runtime.tools and self.runtime.tools.can_use("computer_observe"):
                candidates.append((self.COMPUTER_OBSERVE, {},
                                   "独处时确认一下电脑状态"))
        if candidates:
            selected = candidates[0]
            if self.planner:
                try:
                    choice = self.planner(context, tuple(item[0] for item in candidates))
                    if choice == "skip":
                        # skip 也要占用一次规划冷却，避免每分钟重复消耗云端额度。
                        self.state["last_run_at"] = context.now.isoformat(timespec="seconds")
                        self.state["last_decision"] = "skip"
                        self._save()
                        return ActivityDecision(None, False, "外部规划器判断这次先不行动")
                    for candidate in candidates:
                        if candidate[0].id == choice:
                            selected = candidate
                            break
                except Exception:
                    # 规划器是润色层，失败时仍使用确定性候选，不阻断生命循环。
                    pass
            activity, kwargs, reason = selected
            return ActivityDecision(activity, True, reason, kwargs)
        return ActivityDecision(None, False, "没有可用的低风险自主工具")

    @staticmethod
    def _workspace_files(value):
        """兼容旧 Tools.list_workspace 的文本结果，只提取文件名。"""
        if isinstance(value, dict):
            value = value.get("files", "")
        files = []
        for line in str(value or "").splitlines():
            line = line.strip()
            if line.startswith("[文件]"):
                name = line[len("[文件]"):].strip()
                if name:
                    files.append(name)
        return files

    def _summarize(self, decision, result):
        if not result.get("ok"):
            return str(result.get("error") or "工具没有返回结果")[:180]
        value = result.get("result")
        if decision.activity.tool_id == "workspace_list":
            files = self._workspace_files(value)
            known = set(self.state.get("seen_workspace") or [])
            if not self.state.get("initialized"):
                self.state["initialized"] = True
            else:
                new_files = [name for name in files if name not in known]
                if new_files:
                    self.state["pending_read"] = new_files[0]
            self.state["seen_workspace"] = sorted(set(files))[-200:]
            return f"看过工作区，共发现 {len(files)} 个文件"
        if decision.activity.tool_id == "file_read":
            self.state["pending_read"] = ""
            return f"读过 {decision.kwargs.get('filename', '')}，内容约 {len(str(value or ''))} 字节"
        if decision.activity.tool_id == "chen_note_write":
            self.state["wrote_note_today"] = True
            self.state["write_actions"] = int(self.state.get("write_actions", 0) or 0) + 1
            if isinstance(value, dict):
                title = value.get("title") or value.get("filename") or "随笔"
                preview = value.get("preview") or ""
                return f"在随笔夹写下了《{title}》：{preview[:36]}" if preview else f"在随笔夹写下了《{title}》"
            return "在随笔夹写下了一段随笔"
        if decision.activity.tool_id == "chen_note_organize":
            self.state["organized_today"] = True
            self.state["write_actions"] = int(self.state.get("write_actions", 0) or 0) + 1
            if isinstance(value, dict):
                return f"整理了随笔目录（共 {value.get('count', 0)} 篇）"
            return "整理了随笔目录"
        if decision.activity.tool_id == "world_state_touch":
            self.state["world_touched_today"] = True
            self.state["write_actions"] = int(self.state.get("write_actions", 0) or 0) + 1
            if isinstance(value, dict) and value.get("summary"):
                return str(value["summary"])[:160]
            return "收拾了一下小世界房间"
        return "看过一眼电脑状态"

    def run_once(self, context: ActivityContext):
        decision = self.decide(context)
        if not decision.allowed:
            return decision, None, None
        activity = decision.activity
        call_kwargs = dict(decision.kwargs or {})
        if activity.tool_id == "chen_note_write" and not call_kwargs.get("content"):
            generated = None
            if callable(self.note_generator):
                try:
                    generated = self.note_generator(context)
                except Exception:
                    generated = None
            if isinstance(generated, dict):
                call_kwargs.setdefault("title", generated.get("title", ""))
                call_kwargs.setdefault("content", generated.get("content", ""))
                if generated.get("filename"):
                    call_kwargs.setdefault("filename", generated["filename"])
            elif isinstance(generated, str) and generated.strip():
                call_kwargs["content"] = generated.strip()
            if not call_kwargs.get("content"):
                call_kwargs["title"] = f"独处随记 · {context.now:%m.%d}"
                call_kwargs["content"] = f"安静待了一会儿（心境：{context.mood}），顺手把桌上的东西理了理。"
        result = self.runtime.call_tool(
            activity.tool_id, source=AUTONOMY_SOURCE,
            activity_id=activity.id, **call_kwargs)
        summary = self._summarize(decision, result)
        self.state["actions"] = int(self.state.get("actions", 0)) + 1
        self.state["last_run_at"] = context.now.isoformat(timespec="seconds")
        record = ExperienceRecord(
            at=context.now.isoformat(timespec="seconds"),
            activity_id=activity.id, tool_id=activity.tool_id,
            ok=bool(result.get("ok")), summary=summary, reason=decision.reason,
        )
        history = list(self.state.get("history") or [])
        history.append(record.__dict__)
        self.state["history"] = history[-50:]
        self._save()
        if self.day_log:
            self.day_log.log("autonomy", summary, extra={
                "activity_id": activity.id, "tool": activity.tool_id,
                "ok": bool(result.get("ok")), "reason": decision.reason,
            })
        return decision, result, record

