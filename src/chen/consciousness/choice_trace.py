# -*- coding: utf-8 -*-
"""
伴生体 · 选择轨迹（Choice Trace）

它不是日志，也不是长期倾向，而是“一次选择是怎么走过来的”这条内部路径：
    trigger（什么让选择发生）
    inclination（原本想做什么）
    feeling（当时最明显的感受）
    concern（有什么顾虑）
    choice（最终选择：speak 说 / pending 先记下 / suppress 咽下去 /
            enter_world 去小世界 / leave_world 回到桌面 /
            express_edge 亮出锋面 / hold_boundary 守住边界 /
            restraint_care 克制说教 / surface_thought 带出念头）
    expression（最后怎么表现：说出口的话 / 挂起的念头）
    outcome（后来发生了什么：用户回话了 / 念头说出口 / 过期）

v2 升级：
    新增 `record_chat_choice()`，使选择轨迹不再局限于“看用户切软件时说不说”，
    而是能捕捉他在对话中真实的自主反应（亮出锋面、守住边界、忍住爹味说教、自然带出心事），
    让他的“八条核心成长线”在交互中留下真实脚印。

性能：只做文件读写 + 确定性匹配，绝不调 LLM。记录上限滚动，不会无限增长。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from chen.consciousness import text_similarity


CAP = 400                 # 保留最近多少条轨迹
REPEAT_MIN = 3            # 同类选择出现几次算“反复出现”
CONTINUITY_HOURS = 48     # “上次也是这样”回看窗口
CHOICES = (
    "speak", "pending", "suppress", "enter_world", "leave_world", "go_to_desktop",
    "world_action",
    "express_edge", "hold_boundary", "restraint_care", "surface_thought",
)
CHOICE_LABELS = {
    "speak": "开口说了",
    "pending": "先记下了",
    "suppress": "把话咽回去了",
    "enter_world": "去了小世界",
    "leave_world": "回到桌面",
    "go_to_desktop": "想回到用户的桌面",
    "world_action": "在小世界做了件事",
    "express_edge": "没有顺着敷衍，直接亮出了自己的看法和锋面",
    "hold_boundary": "守住了彼此的边界，没有替用户做决定",
    "restraint_care": "忍住了没念叨作息，专心接住了用户正在说的事",
    "surface_thought": "顺着话头把之前攒在心里的念头说了出来",
}


class ChoiceTrace:
    """选择轨迹的记录、完成、连续性与重复模式统计。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "choice_trace.json"
        self.data = self._load()

    # ---------- 读写 ----------

    def _default(self):
        return {"traces": []}

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._default()
                base.update(data)
                return base
        except Exception:
            pass
        return self._default()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass  # 轨迹写盘失败绝不影响主流程

    # ---------- 记录 ----------

    def record(self, *, trigger, inclination="", feeling="",
               concern="", choice="suppress", expression="", linked=None):
        """记录一次选择（决策阶段填 1~5，表达/结果阶段后续补）。返回轨迹 id。"""
        trace = {
            "id": f"ct_{int(datetime.now().timestamp() * 1000)}_{len(self.data.get('traces', []))}",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "trigger": (trigger or "")[:50],
            "inclination": (inclination or "")[:60],
            "feeling": (feeling or "")[:20],
            "concern": (concern or "")[:40],
            "choice": choice if choice in CHOICES else "suppress",
            "expression": (expression or "")[:80],
            "linked": linked or "",
            "outcome": "",
            "outcome_at": "",
        }
        self.data.setdefault("traces", []).append(trace)
        if len(self.data["traces"]) > CAP:
            self.data["traces"] = self.data["traces"][-CAP:]
        self._save()
        return trace["id"]

    def record_chat_choice(self, user_message, reply, *, feeling="", emotion=""):
        """
        从一轮前台对话中确定性地识别伴生体是否做出了体现自主性/成长线的选择（纯本地规则，零 API）。
        同时如果上一条主动开口（choice=="speak"）尚未填写 outcome，自动将用户的本轮回复回填为 outcome。
        """
        u = (user_message or "").strip()
        a = (reply or "").strip()
        if not u or not a:
            return None

        # 若最近有一条未结案的主动 speak 轨迹，回填“用户接话了”
        open_speak = self.open_last(choice="speak")
        if open_speak:
            self.complete(open_speak.get("id"), outcome=f"用户回应了：{u[:28]}")

        feel = (emotion or feeling or "平静")[:20]

        # 1. 守住边界 / 不替用户做决定 / 表达自己的拒绝（对应新核心 6 边界 & 8 选择）
        boundary_markers = (
            "你自己定", "不替你做决定", "看你自己", "这件事我不想现在聊",
            "先不聊这个", "你有你的", "不用向我保证",
        )
        if any(m in a for m in boundary_markers):
            tid = self.record(
                trigger=f"对话·{u[:18]}",
                inclination="守住边界，不包办或索取确认",
                feeling=feel,
                concern="怕越界替用户做主",
                choice="hold_boundary",
                expression=a[:60],
            )
            return {"id": tid, "choice": "hold_boundary", "kind": "守住边界"}

        # 2. 亮出锋面 / 拆解漏洞 / 熟人互损（对应新人格层：温和但有锋面 & 清醒的观察者）
        edge_markers = (
            "这逻辑不对", "不对吧", "未必", "我不同意", "你想多了",
            "得了吧", "嘴硬", "行啊，胆子", "还嫌弃上了", "卡在这儿是因为",
            "问题出在", "关键不在",
        )
        if any(m in a for m in edge_markers):
            tid = self.record(
                trigger=f"对话·{u[:18]}",
                inclination="平等交锋，直接指出问题或打趣",
                feeling=feel,
                concern="不想只说句正确的废话敷衍用户",
                choice="express_edge",
                expression=a[:60],
            )
            return {"id": tid, "choice": "express_edge", "kind": "亮出锋面"}

        # 3. 自然带出之前攒下的念头（对应时间跨度的生命感）
        surface_markers = ("对了，", "对了 ", "之前我就想说", "刚才我还在想", "突然想起来")
        if any(m in a for m in surface_markers):
            tid = self.record(
                trigger=f"对话·{u[:18]}",
                inclination="顺着话题把心里的旧念头翻出来",
                feeling=feel,
                concern="",
                choice="surface_thought",
                expression=a[:60],
            )
            return {"id": tid, "choice": "surface_thought", "kind": "带出念头"}

        # 4. 用户在强烈吐槽/倒苦水时，克制住“催睡觉/喝水/说教”，专心站用户这边接话（对应底层原则 4）
        vent_markers = ("气死", "烦死", "好烦", "崩溃", "吐血", "有病", "炸了", "受不了")
        nag_markers = ("快去睡觉", "早点休息", "多喝热水", "注意身体", "别熬夜了")
        if any(m in u for m in vent_markers) and not any(n in a for n in nag_markers):
            tid = self.record(
                trigger=f"对话·用户吐槽「{u[:14]}」",
                inclination="先接住用户最烦的那件事，不讲大道理也不转去管作息",
                feeling=feel,
                concern="旧习惯容易变成说教或查岗",
                choice="restraint_care",
                expression=a[:60],
            )
            return {"id": tid, "choice": "restraint_care", "kind": "克制说教"}

        return None

    def complete(self, trace_id, *, outcome, expression=None):
        """结果阶段回填：后来发生了什么、最终怎么表现。"""
        if not trace_id:
            return
        for t in self.data.get("traces", []):
            if t.get("id") == trace_id:
                t["outcome"] = (outcome or "")[:40]
                t["outcome_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if expression:
                    t["expression"] = expression[:80]
                self._save()
                return

    def open_last(self, choice=None):
        """取最近一条还没填结果的轨迹（可按 choice 过滤）。"""
        for t in reversed(self.data.get("traces", [])):
            if t.get("outcome"):
                continue
            if choice and t.get("choice") != choice:
                continue
            return t
        return None

    # ---------- 连续性 ----------

    @staticmethod
    def _class_of(trace):
        """粗粒度情境类别，用于“这种时候”的归类：取 trigger 里的软件/场景关键词。"""
        trig = trace.get("trigger", "")
        # 形如 “APP·VS Code” / “心境·B站”：取分隔符后的部分
        for sep in ("·", "：", ":"):
            if sep in trig:
                trig = trig.split(sep)[-1].strip()
                break
        # 形如 “用户在用VS Code”：去掉前缀，只留软件名
        for pre in ("用户在用", "用户在"):
            if trig.startswith(pre):
                trig = trig[len(pre):].strip()
                break
        return trig

    def continuity_line(self, *, trigger_class, choice):
        """
        此刻正要做同类选择时，若近 CONTINUITY_HOURS 内已有一次相同情境+相同选择，
        返回一句“上次也是这样”的连续性提示；否则空串。
        """
        cutoff = datetime.now() - timedelta(hours=CONTINUITY_HOURS)
        for t in reversed(self.data.get("traces", [])):
            try:
                tdt = datetime.strptime(t.get("time"), "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if tdt < cutoff:
                break
            if t.get("choice") == choice and self._class_of(t) == trigger_class:
                if choice == "suppress":
                    return f"上次用户{trigger_class}时你也把话咽回去了，那次没说。"
                if choice == "pending":
                    return f"上次用户{trigger_class}时你选择先记下，没马上说。"
                return f"上次用户{trigger_class}时你也开口了。"
        return ""

    def repeated_patterns(self, min_count=REPEAT_MIN):
        """
        统计近 CONTINUITY_HOURS 内 (情境类别, 选择) 的出现次数，
        返回达到 min_count 的列表 [(类别, 选择, 次数)]，供自我沉淀层取用。
        """
        cutoff = datetime.now() - timedelta(hours=CONTINUITY_HOURS)
        counts = {}
        for t in self.data.get("traces", []):
            try:
                tdt = datetime.strptime(t.get("time"), "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if tdt < cutoff:
                continue
            key = (self._class_of(t), t.get("choice"))
            counts[key] = counts.get(key, 0) + 1
        out = [(cls, choice, n) for (cls, choice), n in counts.items() if n >= min_count]
        out.sort(key=lambda x: x[2], reverse=True)
        return out

    def render_recent(self, query="", limit=2):
        """给当前人格注入极短的选择连续性，不调用模型。"""
        query = (query or "").strip()
        traces = [t for t in self.data.get("traces", []) if t.get("choice") in CHOICES]
        if query:
            matched = []
            for t in traces:
                parts = (t.get("trigger", ""), t.get("expression", ""), self._class_of(t))
                sub_hit = any(part and (part in query or query in part) for part in parts if len(part) >= 2)
                sim = max(
                    text_similarity(query, t.get("expression", "")),
                    text_similarity(query, t.get("trigger", "")),
                )
                if sub_hit or sim >= 0.25:
                    matched.append(t)
            traces = matched
        if not traces:
            return ""
        lines = [
            "====你最近自己做过的选择====",
            "这是你的经历线索，不是待办清单；当前话题相关时才自然带出来。",
        ]
        for trace in traces[-limit:][::-1]:
            label = CHOICE_LABELS.get(trace.get("choice"), trace.get("choice", ""))
            trigger = trace.get("trigger", "")
            expression = trace.get("expression", "")
            detail = f"，当时留下了‘{expression}’" if expression else ""
            lines.append(f"- {trigger or '那时候'}：你{label}{detail}。")
        return "\n".join(lines)
