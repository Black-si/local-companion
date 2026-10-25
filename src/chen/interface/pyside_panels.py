"""Static PySide windows used by the non-home parts of Chen's shell.

The living home owns the only video surface.  These panels deliberately paint
an image background and expose a small, shared context menu for choosing a
custom background.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QPainter, QFont
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
        QPlainTextEdit, QProgressBar, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
    )
    PYSIDE6_ERROR = None
except ImportError as exc:  # pragma: no cover
    PYSIDE6_ERROR = exc
    QRectF = Qt = QTimer = QColor = QImage = QPainter = QFont = object
    QApplication = QCheckBox = QDialog = QFileDialog = QFrame = QGridLayout = QHBoxLayout = QLabel = object
    QInputDialog = QLineEdit = QListWidget = QListWidgetItem = QMenu = QMessageBox = QPushButton = object
    QPlainTextEdit = QProgressBar = QScrollArea = QStackedWidget = QVBoxLayout = QWidget = object

try:
    from .ui_tokens import get_period_theme
    from .companion_profile import (
        COMPANION_PRESETS,
        PET_UPLOAD_SLOTS,
        SCENE_UPLOAD_SLOTS,
        companion_name,
        companion_pronoun,
        find_companion_label,
        get_uploaded_scene_map,
        install_custom_pet_sprite,
        install_custom_scene_asset,
        load_companion_profile,
        save_and_apply_companion_profile,
    )
except ImportError:  # pragma: no cover
    from chen.interface.ui_tokens import get_period_theme
    from chen.interface.companion_profile import (
        COMPANION_PRESETS,
        PET_UPLOAD_SLOTS,
        SCENE_UPLOAD_SLOTS,
        companion_name,
        companion_pronoun,
        find_companion_label,
        get_uploaded_scene_map,
        install_custom_pet_sprite,
        install_custom_scene_asset,
        load_companion_profile,
        save_and_apply_companion_profile,
    )


ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "workspace" / "living_home_settings.json"
DEFAULT_BG = ROOT / "assets" / "room_night.png"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
GODOT_RUNTIME = ROOT / "worlds" / "chen_world" / "godot_room_v1"
GODOT_EXE = ROOT / "tools" / "godot" / "Godot_v4.7.2-stable_win64.exe"


def _read_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        pass


def refresh_open_backgrounds() -> None:
    """Apply the shared static background setting to already open windows."""
    for window in QApplication.topLevelWidgets():
        reload = getattr(window, "reload_background", None)
        if callable(reload):
            reload()


def _rgba(color: tuple[int, int, int], alpha: int) -> str:
    return f"rgba({color[0]},{color[1]},{color[2]},{alpha})"


class StaticPanelWindow(QWidget):
    """A readable static-background panel shared by all non-home views."""

    _GLOBAL_NAV: dict[str, Callable[[], None]] = {}

    @classmethod
    def set_global_navigator(cls, nav_map: dict[str, Callable[[], None]]) -> None:
        cls._GLOBAL_NAV = dict(nav_map or {})

    def __init__(self, chen=None, *, panel_key: str, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.chen = chen
        self.panel_key = panel_key
        self.setWindowTitle(f"{title} · {companion_name()}")
        self.setMinimumSize(560, 440)
        self.resize(760, 600)
        self._background = QImage()
        self._blurred_background = QImage()
        pt = get_period_theme()
        self._theme = {
            "tint": pt["tint"],
            "text": pt["text"],
            "muted": pt["muted"],
            "accent": pt["accent"],
            "border": pt["border"],
            "period_label": pt["label"],
        }
        self._build_shell(title, subtitle)
        self.reload_background()

    def _build_shell(self, title: str, subtitle: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)
        head = QFrame()
        head.setObjectName("glass")
        header = QVBoxLayout(head)
        header.setContentsMargins(18, 14, 18, 14)
        header.setSpacing(6)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.heading = QLabel(title)
        self.heading.setObjectName("heading")
        top_bar.addWidget(self.heading)
        top_bar.addStretch()

        self._nav_buttons: dict[str, QPushButton] = {}
        nav_items = (
            ("relationship", "我们"),
            ("life", "今天"),
            ("space", "空间"),
            ("pet", "桌宠"),
            ("studio", "伴生体"),
            ("workshop", "修理台"),
            ("chat", f"{find_companion_label()}聊天"),
        )
        for nav_key, nav_label in nav_items:
            btn = QPushButton(nav_label)
            btn.setObjectName("panelNavTab")
            btn.setCheckable(nav_key != "chat")
            btn.setChecked(nav_key == self.panel_key)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=nav_key: self._switch_panel(k))
            top_bar.addWidget(btn)
            self._nav_buttons[nav_key] = btn

        header.addLayout(top_bar)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("subtitle")
            label.setWordWrap(True)
            header.addWidget(label)
        root.addWidget(head)
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        root.addLayout(self.content, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        self.background_button = QPushButton("背景")
        self.background_button.setMenu(QMenu(self.background_button))
        self.background_button.menu().addAction(
            "换所有非首页窗口背景…", lambda: self.choose_background("default")
        )
        self.background_button.menu().addAction(
            "只换当前窗口背景…", lambda: self.choose_background(self.panel_key)
        )
        self.background_button.menu().addAction("恢复当前窗口背景", self.reset_background)
        footer.insertWidget(0, self.background_button)
        root.addLayout(footer)

    def add_text(self, text: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.content.addWidget(view, 1)
        return view

    def _switch_panel(self, target_key: str) -> None:
        if target_key == self.panel_key:
            btn = self._nav_buttons.get(target_key)
            if btn is not None:
                btn.setChecked(True)
            return
        cb = self._GLOBAL_NAV.get(target_key)
        if callable(cb):
            if target_key != "chat":
                self.hide()
            cb()

    def add_button(self, label: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(callback)
        self.content.addWidget(button, 0)
        return button

    def _background_path(self) -> Path:
        settings = _read_settings()
        background_settings = settings.get("window_bg") or {}
        selected = background_settings.get(self.panel_key)
        # “空间”替代了旧的“小世界”窗口名，保留旧设置里的背景选择。
        if not selected and self.panel_key == "space":
            selected = background_settings.get("world")
        selected = selected or background_settings.get("default")
        path = Path(str(selected)) if selected else DEFAULT_BG
        return path if path.exists() else DEFAULT_BG

    def reload_background(self) -> None:
        pt = get_period_theme()
        self._theme = {
            "tint": pt["tint"],
            "text": pt["text"],
            "muted": pt["muted"],
            "accent": pt["accent"],
            "border": pt["border"],
            "period_label": pt["label"],
        }
        path = self._background_path()
        self._background = QImage(str(path))
        if not self._background.isNull():
            sw = max(16, self._background.width() // 7)
            sh = max(16, self._background.height() // 7)
            small = self._background.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._blurred_background = small.scaled(
                self._background.width(),
                self._background.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self._blurred_background = QImage()
        self.setStyleSheet(self._stylesheet())
        self.update()

    def _stylesheet(self) -> str:
        t = self._theme
        border_c = t.get("border", t["muted"])
        return f"""
        QLabel {{ background: transparent; color: rgb{t['text']}; }}
        QLabel#heading {{ font-size: 16pt; font-weight: 600; }}
        QLabel#subtitle {{ color: rgb{t['muted']}; font-size: 9.8pt; }}
        QFrame#glass {{
            background: {_rgba(t['tint'], 206)};
            border: 1px solid {_rgba(border_c, 130)};
            border-radius: 18px;
        }}
        QFrame#momentCard {{
            background: {_rgba(t['accent'], 34)};
            border: 1px solid {_rgba(t['accent'], 115)};
            border-left: 3px solid rgb{t['accent']};
            border-radius: 12px;
        }}
        QFrame#factCard {{
            background: {_rgba(t['tint'], 165)};
            border: 1px solid {_rgba(border_c, 100)};
            border-radius: 11px;
        }}
        QPlainTextEdit {{
            background: {_rgba(t['tint'], 192)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(border_c, 115)};
            border-left: 3px solid {_rgba(t['accent'], 170)};
            border-radius: 14px;
            padding: 12px;
            font-size: 10pt;
        }}
        QPushButton {{
            background: {_rgba(t['tint'], 210)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(border_c, 145)};
            border-radius: 11px;
            padding: 7px 14px;
        }}
        QPushButton:hover {{
            border-color: rgb{t['accent']};
            background: {_rgba(t['accent'], 48)};
        }}
        QPushButton#panelNavTab {{
            background: {_rgba(t['tint'], 145)};
            color: rgb{t['muted']};
            border: 1px solid {_rgba(border_c, 120)};
            border-radius: 10px;
            padding: 5px 11px;
            font-size: 9pt;
        }}
        QPushButton#panelNavTab:hover {{
            color: rgb{t['text']};
            border-color: rgb{t['accent']};
        }}
        QPushButton#panelNavTab:checked {{
            background: rgb{t['accent']};
            color: rgb(32, 28, 22);
            border-color: rgb{t['accent']};
            font-weight: 600;
        }}
        QProgressBar#energyBar {{
            background: {_rgba(t['tint'], 160)};
            border: 1px solid {_rgba(border_c, 110)};
            border-radius: 6px;
            height: 10px;
            text-align: center;
        }}
        QProgressBar#energyBar::chunk {{
            background: rgb{t['accent']};
            border-radius: 5px;
        }}
        """

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        img = self._blurred_background if not self._blurred_background.isNull() else self._background
        if img.isNull():
            painter.fillRect(self.rect(), QColor("#171916"))
        else:
            scale = max(self.width() / img.width(), self.height() / img.height())
            sw, sh = self.width() / scale, self.height() / scale
            source = QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh)
            painter.drawImage(QRectF(self.rect()), img, source)
            r, g, b = self._theme["tint"]
            painter.fillRect(self.rect(), QColor(r, g, b, 112))
        painter.end()

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        menu.addAction("换所有非首页窗口背景…", lambda: self.choose_background("default"))
        menu.addAction("只换当前窗口背景…", lambda: self.choose_background(self.panel_key))
        menu.addAction("恢复当前窗口背景", self.reset_background)
        menu.exec(event.globalPos())

    def choose_background(self, name: str) -> None:
        patterns = " ".join("*" + suffix for suffix in IMAGE_EXT)
        path, _ = QFileDialog.getOpenFileName(self, "选择静态背景", "", f"图片 ({patterns})")
        if not path:
            return
        settings = _read_settings()
        settings.setdefault("window_bg", {})[name] = path
        _write_settings(settings)
        refresh_open_backgrounds()

    def reset_background(self) -> None:
        settings = _read_settings()
        background_settings = settings.setdefault("window_bg", {})
        background_settings.pop(self.panel_key, None)
        if self.panel_key == "space":
            background_settings.pop("world", None)
        _write_settings(settings)
        refresh_open_backgrounds()


def _line(item: dict) -> str:
    stamp = str(item.get("at") or item.get("ts") or item.get("date") or "")[-16:]
    return f"{stamp}  {item.get('text') or item.get('event') or ''}".strip()


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _parse_relationship_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("T", " "))
    except (TypeError, ValueError):
        return None


def _relationship_warmth(data):
    """把关系内部状态翻译成界面上的一句话，避免把关系做成仪表盘。"""
    try:
        tension = float(data.get("tension", 0) or 0)
        comfort = float(data.get("comfort", 0) or 0)
        closeness = float(data.get("closeness", 0) or 0)
    except (TypeError, ValueError):
        tension, comfort, closeness = 0.0, 0.0, 0.0
    if tension >= 60:
        return "最近有点别扭，他还在想怎么说。"
    if tension >= 35:
        return "有一点没说开的东西。"
    if comfort >= 65:
        return "最近待在一起，挺放松的。"
    if closeness >= 60:
        return "最近话多了一些。"
    return "平平常常的日子。"


def _relationship_trend(relation):
    """把近期变化翻译成低压力的提示，不把趋势做成评分。"""
    try:
        trends = relation.recent_trends(limit=8) if relation and hasattr(relation, "recent_trends") else {}
    except Exception:
        trends = {}
    if trends.get("紧张感") == "上升":
        return "最近需要慢一点，先把没说开的地方放在心上。"
    if trends.get("信任") == "下降" or trends.get("安心感") == "下降":
        return "最近有些事，给彼此留一点修正的余地。"
    if trends.get("亲近感") == "上升" or trends.get("安心感") == "上升":
        return "最近在慢慢靠近。"
    return "最近整体稳定。"


def _relationship_fact_store(chen):
    """读取关系事实文件，同时保留旧版本中的历史字段。"""
    memory = getattr(chen, "memory", None)
    path = getattr(memory, "relationship_file", None)
    data = {}
    if path:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError, TypeError):
            data = {}
    facts = data.get("relationships")
    if not isinstance(facts, list):
        facts = list(getattr(memory, "relationships", []) or [])
        data["relationships"] = facts
    return memory, path, data, facts


def _save_relationship_facts(memory, path, data, facts):
    data["relationships"] = facts
    if memory is not None:
        memory.relationships = facts
    if path is None:
        saver = getattr(memory, "_save_relationships", None)
        if callable(saver):
            saver()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _fact_similarity(left, right):
    a, b = set(str(left or "")), set(str(right or ""))
    return len(a & b) / max(1, len(a | b))


class RelationshipFactsDialog(QDialog):
    """关系事实抽屉：保留修改、删除、候选确认和历史记录。"""

    def __init__(self, chen, parent=None):
        super().__init__(parent)
        self.chen = chen
        self.memory, self.path, self.store, self.facts = _relationship_fact_store(chen)
        self.setWindowTitle("他记得的事 · 可修正")
        self.setMinimumSize(620, 430)
        self.resize(700, 500)
        self._build()
        self._render()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        intro = QLabel("这些是伴生体保留下来的关系事实。你可以修正，也可以把共同经历确认成事实。")
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        root.addWidget(intro)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        root.addWidget(self.list, 1)
        actions = QHBoxLayout()
        for text, callback in (("修改", self.edit_fact), ("删除", self.remove_fact),
                               ("生成候选", self.add_candidate), ("查看历史", self.show_history)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        close = QPushButton("完成")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)

    def _render(self):
        self.list.clear()
        for fact in self.facts:
            topic = fact.get("topic", "关系")
            detail = fact.get("detail", "")
            status = {"confirmed": "已确认", "pending": "待确认", "rejected": "已否定"}.get(
                fact.get("status", "confirmed"), fact.get("status", "confirmed"))
            basis = f" · 依据：{fact['basis']}" if fact.get("basis") else ""
            item = QListWidgetItem(f"[{status}] {topic}：{detail}{basis}")
            item.setData(Qt.ItemDataRole.UserRole, fact)
            self.list.addItem(item)

    def _selected(self):
        row = self.list.currentRow()
        return (row, self.facts[row]) if 0 <= row < len(self.facts) else (None, None)

    def _persist(self, action, before=None, after=None, extra=None):
        history = self.store.setdefault("relationship_history", [])
        entry = {"at": datetime.now().isoformat(timespec="minutes"), "action": action, "actor": "user"}
        if before is not None:
            entry["before"] = before
        if after is not None:
            entry["after"] = after
        if extra:
            entry.update(extra)
        history.append(entry)
        history[:] = history[-200:]
        _save_relationship_facts(self.memory, self.path, self.store, self.facts)

    def edit_fact(self):
        row, fact = self._selected()
        if fact is None:
            return
        value, ok = QInputDialog.getText(self, "修改关系事实", "事实内容：", text=str(fact.get("detail", "")))
        if not ok or not value.strip():
            return
        before = dict(fact)
        fact["detail"] = value.strip()
        fact["source"] = "user_corrected"
        fact["updated"] = datetime.now().strftime("%Y-%m-%d")
        self._persist("edit", before, dict(fact))
        self._render()
        self.list.setCurrentRow(row)

    def remove_fact(self):
        row, fact = self._selected()
        if fact is None:
            return
        answer = QMessageBox.question(self, "删除关系事实", "确定删除这条关系事实吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = dict(fact)
        self.facts.pop(row)
        self._persist("delete", before=before)
        self._render()

    def add_candidate(self):
        relation = getattr(self.chen, "relationship", None)
        candidates = relation.relationship_candidates(limit=5) if relation else []
        if not candidates:
            QMessageBox.information(self, "候选事实", "目前还没有足够的共同经历。")
            return
        options = [str(item.get("detail", "")) for item in candidates]
        choice, ok = QInputDialog.getItem(self, "候选事实", "选择要确认的共同经历：", options, 0, False)
        if not ok:
            return
        candidate = dict(candidates[options.index(choice)])
        duplicate = next((old for old in self.facts
                          if _fact_similarity(old.get("detail"), candidate.get("detail")) >= 0.45), None)
        if duplicate:
            answer = QMessageBox.question(
                self, "相似关系事实",
                f"已有相似事实：\n{duplicate.get('detail', '')}\n\n是否合并这次依据？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                before = dict(duplicate)
                bases = [x.strip() for x in (
                    str(duplicate.get("basis", "")) + "," + str(candidate.get("basis", ""))
                ).split(",") if x.strip()]
                duplicate["basis"] = "、".join(dict.fromkeys(bases))
                duplicate["updated"] = datetime.now().strftime("%Y-%m-%d")
                self._persist("merge", before, dict(duplicate), {"merged": candidate})
                self._render()
                return
        same_topic = [old for old in self.facts
                      if old.get("topic") == candidate.get("topic") and old.get("status", "confirmed") != "rejected"]
        conflict = next((old for old in same_topic if
                         ("不" in str(old.get("detail", ""))) != ("不" in str(candidate.get("detail", "")))), None)
        if conflict:
            answer = QMessageBox.question(
                self, "可能的关系冲突",
                f"已有事实：\n{conflict.get('detail', '')}\n\n新候选：\n{candidate.get('detail', '')}\n\n否定旧事实并加入吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                conflict_before = dict(conflict)
                conflict["status"] = "rejected"
            else:
                conflict_before = None
        else:
            conflict_before = None
        candidate["confirmed_at"] = datetime.now().isoformat(timespec="minutes")
        candidate["status"] = "confirmed"
        self.facts.append(candidate)
        self._persist("confirm_candidate", extra={"conflict_before": conflict_before} if conflict_before else None)
        self._render()

    def show_history(self):
        history = self.store.get("relationship_history", []) or []
        if not history:
            QMessageBox.information(self, "关系事实历史", "还没有修改记录。")
            return
        lines = []
        for item in reversed(history[-20:]):
            before = (item.get("before") or {}).get("detail", "")
            after = (item.get("after") or {}).get("detail", "")
            lines.append(f"{item.get('at', '')} · {item.get('action', '')}\n  {after or before}")
        QMessageBox.information(self, "关系事实历史", "\n\n".join(lines))


class _TimelineRow(QWidget):
    """一条共同经历；有依据时点击事件文字展开摘录。"""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        time_label = QLabel(event["at"].strftime("%H:%M") if event.get("at") else "")
        time_label.setObjectName("timelineTime")
        time_label.setFixedWidth(44)
        dot = QLabel("●")
        dot.setObjectName("timelineDot")
        dot.setProperty("importance", "high" if event.get("importance", 0) >= 0.75 else "normal")
        dot.setFixedWidth(14)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(time_label)
        top.addWidget(dot)
        self.event_button = QPushButton(event.get("text", ""))
        self.event_button.setObjectName("timelineEvent")
        self.event_button.setFlat(True)
        self.event_button.setCursor(Qt.CursorShape.PointingHandCursor)
        top.addWidget(self.event_button, 1)
        if event.get("source") in {"手机", "phone", "mobile"}:
            source = QLabel("手机")
            source.setObjectName("timelineTag")
            top.addWidget(source)
        layout.addLayout(top)
        evidence = str(event.get("evidence", "") or "").replace("\n", " ").strip()
        self.evidence = QLabel(evidence if len(evidence) <= 100 else evidence[:100] + "…")
        self.evidence.setObjectName("timelineEvidence")
        self.evidence.setWordWrap(True)
        self.evidence.setContentsMargins(68, 0, 0, 0)
        self.evidence.setVisible(False)
        layout.addWidget(self.evidence)
        if evidence:
            self.event_button.clicked.connect(lambda: self.evidence.setVisible(not self.evidence.isVisible()))
        else:
            self.event_button.setCursor(Qt.CursorShape.ArrowCursor)


class RelationshipPanel(StaticPanelWindow):
    """“我们”关系页：一句温度、可修正事实和可展开的共同经历。"""

    def __init__(self, chen=None, parent=None, on_chat=None):
        self.only_important = False
        self.on_chat = on_chat
        super().__init__(chen, panel_key="relationship", title="我们",
                         subtitle="把共同经历留在这里；关系会从相处中慢慢变化。", parent=parent)
        self.setMinimumSize(700, 520)
        self.resize(860, 620)
        self._build_relationship_view()
        self.refresh()

    def _build_relationship_view(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.content.setSpacing(16)
        split = QHBoxLayout()
        split.setSpacing(16)
        split.addWidget(self._build_left(), 36)
        split.addWidget(self._build_right(), 64)
        self.content.addLayout(split)

    def _build_left(self):
        box = QFrame()
        box.setObjectName("relationSection")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(6)
        self.stage = QLabel()
        self.stage.setObjectName("relationStage")
        self.count = QLabel()
        self.count.setObjectName("relationMuted")
        self.warmth = QLabel()
        self.warmth.setObjectName("relationWarmth")
        self.warmth.setWordWrap(True)
        self.moment = QLabel()
        self.moment.setObjectName("relationMoment")
        self.moment.setWordWrap(True)
        self.moment.setContentsMargins(0, 2, 0, 4)
        self.trend = QLabel()
        self.trend.setObjectName("relationTrend")
        self.trend.setWordWrap(True)
        layout.addWidget(self.stage)
        layout.addWidget(self.count)
        layout.addSpacing(6)
        layout.addWidget(self.warmth)
        layout.addWidget(self.trend)
        layout.addSpacing(6)

        # 使用无边框滚动区包裹中间三块内容，彻底消除窗口高度不足导致的文字压扁与细条框问题
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_holder = QWidget()
        body = QVBoxLayout(scroll_holder)
        body.setContentsMargins(0, 2, 6, 4)
        body.setSpacing(6)

        body.addWidget(self._section_title("最近记得的一件事"))
        body.addWidget(self.moment)
        body.addSpacing(8)

        body.addWidget(self._section_title("我们的小习惯"))
        self.habits = QVBoxLayout()
        self.habits.setSpacing(6)
        body.addLayout(self.habits)
        body.addSpacing(8)

        facts_head = QHBoxLayout()
        facts_head.addWidget(self._section_title(f"{companion_pronoun()}记得的几件事"))
        facts_head.addStretch()
        all_button = QPushButton("全部")
        all_button.setObjectName("linkButton")
        all_button.clicked.connect(self.open_facts)
        facts_head.addWidget(all_button)
        body.addLayout(facts_head)
        self.facts = QVBoxLayout()
        self.facts.setSpacing(6)
        body.addLayout(self.facts)
        body.addStretch()

        left_scroll.setWidget(scroll_holder)
        layout.addWidget(left_scroll, 1)

        self.chat_button = QPushButton(f"去{find_companion_label()}说话")
        self.chat_button.setObjectName("primaryRelationButton")
        self.chat_button.clicked.connect(self.open_chat)
        self.chat_button.setVisible(callable(self.on_chat))
        layout.addWidget(self.chat_button)
        return box

    @staticmethod
    def _section_title(text):
        label = QLabel(text)
        label.setObjectName("relationSectionTitle")
        return label

    def _build_right(self):
        box = QFrame()
        box.setObjectName("relationSection")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(24, 20, 20, 16)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(self._section_title("一起走过的"))
        head.addStretch()
        self.important_button = QPushButton("重要经历")
        self.all_events_button = QPushButton("全部经历")
        for button in (self.important_button, self.all_events_button):
            button.setObjectName("filterButton")
            button.setCheckable(True)
            head.addWidget(button)
        self.important_button.clicked.connect(lambda: self.set_filter(True))
        self.all_events_button.clicked.connect(lambda: self.set_filter(False))
        self.all_events_button.setChecked(True)
        layout.addLayout(head)
        line = QFrame()
        line.setObjectName("relationLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.timeline = QVBoxLayout(holder)
        self.timeline.setContentsMargins(0, 4, 8, 4)
        self.timeline.setSpacing(0)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        return box

    def _stylesheet(self):
        t = self._theme
        return super()._stylesheet() + f"""
        QFrame#relationSection {{ background: {_rgba(t['tint'], 210)}; border: 1px solid {_rgba(t['muted'], 125)}; border-radius: 16px; }}
        QLabel#relationStage {{ color: rgb{t['text']}; font-size: 20pt; font-weight: 600; }}
        QLabel#relationMuted, QLabel#timelineTime, QLabel#timelineTag, QLabel#timelineEvidence {{ color: rgb{t['muted']}; font-size: 9pt; }}
        QLabel#relationWarmth {{ color: rgb{t['text']}; font-size: 11pt; }}
        QLabel#relationTrend {{ color: rgb{t['muted']}; font-size: 9pt; }}
        QLabel#relationMoment {{ color: rgb{t['text']}; font-size: 10pt; }}
        QLabel#relationSectionTitle {{ color: rgb{t['text']}; font-size: 11pt; font-weight: 600; }}
        QLabel#timelineDot {{ color: rgb{t['muted']}; font-size: 8pt; }}
        QLabel#timelineDot[importance="high"] {{ color: rgb{t['accent']}; font-size: 12pt; }}
        QFrame#relationLine {{ background: {_rgba(t['muted'], 100)}; border: none; }}
        QPushButton#linkButton {{ background: transparent; border: none; color: rgb{t['muted']}; padding: 3px 0; }}
        QPushButton#linkButton:hover {{ color: rgb{t['accent']}; }}
        QPushButton#filterButton {{ background: transparent; border: none; border-bottom: 2px solid transparent; color: rgb{t['muted']}; padding: 4px 6px; }}
        QPushButton#filterButton:checked {{ color: rgb{t['text']}; border-bottom-color: rgb{t['accent']}; }}
        QPushButton#timelineEvent {{ background: transparent; border: none; color: rgb{t['text']}; text-align: left; padding: 3px 0; }}
        QPushButton#timelineEvent:hover {{ color: rgb{t['accent']}; }}
        QPushButton#primaryRelationButton {{ background: rgb{t['accent']}; color: rgb{t['tint']}; border: none; border-radius: 10px; padding: 9px 14px; font-weight: 600; }}
        QPushButton#primaryRelationButton:hover {{ background: rgb{t['text']}; color: rgb{t['tint']}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {_rgba(t['muted'], 100)}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

    def _snapshot(self):
        relation = getattr(self.chen, "relationship", None)
        data = getattr(relation, "data", {}) or {}
        events = relation.shared_timeline(limit=40) if relation and hasattr(relation, "shared_timeline") else []
        normalized = []
        for item in events:
            normalized.append({
                "at": _parse_relationship_time(item.get("at")),
                "text": item.get("event", ""),
                "evidence": item.get("evidence", ""),
                "importance": float(item.get("importance", 0.5) or 0.5),
                "source": item.get("source", ""),
            })
        return {
            "stage": data.get("stage", "熟悉的陪伴者"),
            "count": int(data.get("interaction_count", 0) or 0),
            "warmth": _relationship_warmth(data),
            "trend": _relationship_trend(relation),
            "moment": data.get("last_meaningful_moment") or "暂时没有特别标记的时刻。",
            "rituals": list(data.get("shared_rituals", []) or [])[-3:],
            "events": normalized,
        }

    def refresh(self):
        snap = self._snapshot()
        self.stage.setText(str(snap["stage"]))
        self.count.setText(f"一起留下了 {snap['count']} 次记录")
        self.warmth.setText(snap["warmth"])
        self.trend.setText(snap["trend"])
        self.moment.setText(str(snap["moment"]))
        clear(self.habits)
        for ritual in snap["rituals"] or ["还没有固定的习惯，不急。"]:
            label = QLabel("· " + str(ritual))
            label.setObjectName("relationBodyItem")
            label.setWordWrap(True)
            label.setMinimumHeight(22)
            self.habits.addWidget(label)
        self.render_facts()
        self.render_timeline(snap["events"])

    def render_facts(self):
        _, _, _, facts = _relationship_fact_store(self.chen)
        self.current_facts = facts
        clear(self.facts)
        for index, fact in enumerate(facts[:3]):
            holder = QWidget()
            holder.setMinimumHeight(24)
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(8)
            detail = str(fact.get("detail", ""))
            preview = detail if len(detail) <= 38 else detail[:38] + "…"
            topic = str(fact.get("topic", "关系"))
            text = QLabel(f"· {topic}：{preview}")
            text.setObjectName("relationBodyItem")
            text.setToolTip(detail)
            text.setWordWrap(True)
            row.addWidget(text, 1)
            button = QPushButton("不太对")
            button.setObjectName("linkButton")
            button.clicked.connect(lambda _checked=False, i=index: self.fix_fact(i))
            row.addWidget(button)
            self.facts.addWidget(holder)
        if not facts:
            empty = QLabel("他还没记下什么。")
            empty.setObjectName("relationMuted")
            self.facts.addWidget(empty)

    def render_timeline(self, events=None):
        clear(self.timeline)
        events = events or []
        if self.only_important:
            events = [event for event in events if event.get("importance", 0) >= 0.75]
        last_group = None
        for event in events:
            at = event.get("at")
            group = "今天" if at and at.date() == datetime.now().date() else (
                "昨天" if at and (datetime.now().date() - at.date()).days == 1 else "更早")
            if group != last_group:
                heading = QLabel(group)
                heading.setObjectName("relationMuted")
                heading.setContentsMargins(0, 10 if last_group else 2, 0, 2)
                self.timeline.addWidget(heading)
                last_group = group
            self.timeline.addWidget(_TimelineRow(event))
        if not events:
            empty = QLabel("还没留下什么。")
            empty.setObjectName("relationMuted")
            self.timeline.addWidget(empty)
        self.timeline.addStretch()

    def set_filter(self, only_important):
        self.only_important = bool(only_important)
        self.important_button.setChecked(self.only_important)
        self.all_events_button.setChecked(not self.only_important)
        self.render_timeline(self._snapshot()["events"])

    def fix_fact(self, index):
        memory, path, store, facts = _relationship_fact_store(self.chen)
        if not 0 <= index < len(facts):
            return
        fact = facts[index]
        value, ok = QInputDialog.getText(self, "不太对？", "他记得的是这样，你想改成：",
                                         text=str(fact.get("detail", "")))
        if not ok or not value.strip():
            return
        before = dict(fact)
        fact["detail"] = value.strip()
        fact["source"] = "user_corrected"
        fact["updated"] = datetime.now().strftime("%Y-%m-%d")
        store.setdefault("relationship_history", []).append({
            "at": datetime.now().isoformat(timespec="minutes"), "action": "edit",
            "before": before, "after": dict(fact), "actor": "user",
        })
        _save_relationship_facts(memory, path, store, facts)
        self.refresh()

    def open_facts(self):
        dialog = RelationshipFactsDialog(self.chen, self)
        dialog.exec()
        self.refresh()

    def open_chat(self):
        if callable(self.on_chat):
            self.on_chat()


class LifePanel(StaticPanelWindow):
    """“他的生活”页：实时展示他的节律、精力、今日自主活动与生活痕迹。"""

    def __init__(self, chen=None, parent=None):
        super().__init__(
            chen, panel_key="life", title="他的生活",
            subtitle="作息、自主小活动、随笔与日常痕迹都会随着他的时间自然累积。",
            parent=parent,
        )
        self.setMinimumSize(720, 520)
        self.resize(860, 620)
        self._build_life_view()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self.refresh()

    def showEvent(self, event):  # noqa: N802
        self.refresh()
        super().showEvent(event)

    def _build_life_view(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        split = QHBoxLayout()
        split.setSpacing(14)

        left = QFrame()
        left.setObjectName("glass")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(20, 18, 20, 18)
        left_layout.setSpacing(8)
        self.life_headline = QLabel()
        self.life_headline.setObjectName("heading")
        self.life_headline.setWordWrap(True)
        self.life_meta = QLabel()
        self.life_meta.setObjectName("subtitle")
        self.life_meta.setWordWrap(True)
        self.energy_bar = QProgressBar()
        self.energy_bar.setObjectName("energyBar")
        self.energy_bar.setRange(0, 100)
        self.energy_bar.setTextVisible(False)
        self.energy_bar.setFixedHeight(8)
        self.autonomy_box = QPlainTextEdit()
        self.autonomy_box.setReadOnly(True)
        self.autonomy_box.setMinimumHeight(180)
        left_layout.addWidget(self.life_headline)
        left_layout.addWidget(self.life_meta)
        left_layout.addWidget(self.energy_bar)
        left_layout.addSpacing(6)
        auto_title = QLabel("今日自主小活动 & 随笔")
        auto_title.setStyleSheet("font-weight: 600; font-size: 11pt;")
        left_layout.addWidget(auto_title)
        left_layout.addWidget(self.autonomy_box, 1)

        right = QFrame()
        right.setObjectName("glass")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 18, 20, 18)
        right_layout.setSpacing(8)
        top_row = QHBoxLayout()
        trace_title = QLabel("今天留下的痕迹与日记")
        trace_title.setStyleSheet("font-weight: 600; font-size: 11pt;")
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(trace_title)
        top_row.addStretch()
        top_row.addWidget(refresh_btn)
        right_layout.addLayout(top_row)
        self.timeline_box = QPlainTextEdit()
        self.timeline_box.setReadOnly(True)
        right_layout.addWidget(self.timeline_box, 1)

        split.addWidget(left, 40)
        split.addWidget(right, 60)
        self.content.addLayout(split, 1)

    def refresh(self):
        chen = self.chen
        life = getattr(chen, "life", None) if chen else None
        st = dict(getattr(life, "state", {}) or {}) if life else {}
        awake = st.get("awake", True)
        energy = int(round(float(st.get("energy", 80) or 0)))
        activity = st.get("activity") or "安静待着"
        lived = life.lived_desc() if life and hasattr(life, "lived_desc") else ""
        emo_str = "平静"
        try:
            emo = getattr(chen, "emotion", None)
            if emo:
                ename, eval_ = emo.dominant()
                if ename:
                    emo_str = f"{ename}（{int(eval_ * 100)}%）"
        except Exception:
            pass
        self.life_headline.setText("在睡觉" if not awake else activity)
        meta_parts = [f"精力 {energy}%", f"心境：{emo_str}"]
        if lived:
            meta_parts.append(lived)
        self.life_meta.setText("  ·  ".join(meta_parts))
        if hasattr(self, "energy_bar"):
            self.energy_bar.setValue(max(0, min(100, energy)))

        # 自主活动与随笔摘要
        auto_lines = []
        autonomy = getattr(chen, "autonomy", None) if chen else None
        if autonomy and isinstance(getattr(autonomy, "state", None), dict):
            astate = autonomy.state
            used = int(astate.get("actions", 0) or 0)
            cap = int(getattr(autonomy, "max_actions_per_day", 4) or 4)
            w_used = int(astate.get("write_actions", 0) or 0)
            w_cap = int(getattr(autonomy, "max_write_actions_per_day", 2) or 2)
            auto_lines.append(f"今日自主行动：{used}/{cap} 次（随笔/整理/房间：{w_used}/{w_cap} 次）")
            hist = list(astate.get("history") or [])[-8:]
            if hist:
                auto_lines.append("")
                for h in reversed(hist):
                    at_short = str(h.get("at", ""))[-8:-3] or str(h.get("at", ""))
                    auto_lines.append(f"· {at_short}  {h.get('summary', '')}")
            else:
                auto_lines.append("\n今天还没有触发独处自主行动。")
        else:
            auto_lines.append("自主活动模块未就绪。")
        self.autonomy_box.setPlainText("\n".join(auto_lines))

        # 今日记录与公开内容
        lines = [f"今天：{datetime.now():%Y-%m-%d}", ""]
        day_log = getattr(chen, "day_log", None) if chen else None
        rows = day_log.today_lines() if day_log else []
        kind_map = {"life": "生活", "world": "世界", "world_action": "行动", "chat": "聊天", "web": "网上", "autonomy": "自主"}
        if rows:
            lines.append("【今日轨迹】")
            for item in reversed(rows[-24:]):
                k_label = kind_map.get(item.get("kind", ""), "记录")
                stamp = str(item.get("ts") or "")[-5:]
                lines.append(f"{stamp}  [{k_label}] {item.get('text', '')}")
        store = getattr(chen, "world_store", None) if chen else None
        items = store.visible_items("用户")["open"] if store else []
        if items:
            lines += ["", "【他留下的日记与念头】"]
            for item in reversed(items[-15:]):
                lines.append(f"[{item.get('type', '琐碎')}] {_line(item)}")
        if len(lines) <= 2:
            lines.append("今天还没有可显示的记录。")
        self.timeline_box.setPlainText("\n".join(lines))


class _SpaceDetailDialog(QDialog):
    """把空间里的真实物件展开查看，避免主页面被物件清单撑满。"""

    def __init__(self, title, sections, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} · {companion_name()}")
        self.setMinimumSize(560, 420)
        self.resize(640, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("spaceDialogHeading")
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        body = QVBoxLayout(holder)
        body.setContentsMargins(2, 2, 8, 2)
        body.setSpacing(16)
        for section_title, text in sections:
            section = QFrame()
            section.setObjectName("spaceDialogSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(18, 15, 18, 15)
            section_layout.setSpacing(6)
            label = QLabel(section_title)
            label.setObjectName("spaceDialogSectionTitle")
            section_layout.addWidget(label)
            content = QLabel(text or "暂时没有可显示的内容。")
            content.setObjectName("spaceDialogText")
            content.setWordWrap(True)
            section_layout.addWidget(content)
            body.addWidget(section)
        body.addStretch()
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        close = QPushButton("知道了")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.setStyleSheet("""
            QDialog { background: #22251f; }
            QLabel { color: #f4eee4; background: transparent; }
            QLabel#spaceDialogHeading { font-size: 17pt; font-weight: 600; }
            QLabel#spaceDialogSectionTitle { font-size: 11pt; font-weight: 600; }
            QLabel#spaceDialogText { color: #c6bdaf; font-size: 10pt; }
            QFrame#spaceDialogSection { background: rgba(24,27,24,220); border: 1px solid rgba(190,181,168,105); border-radius: 14px; }
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QPushButton { background: #96ae8f; color: #28251f; border: none; border-radius: 10px; padding: 8px 16px; font-weight: 600; }
            QPushButton:hover { background: #f4eee4; }
        """)


class SpacePanel(StaticPanelWindow):
    """“空间”页：伴生体留下的生活痕迹，以及两个紧凑的空间入口。"""

    def __init__(self, chen=None, parent=None, on_desktop=None):
        super().__init__(chen, panel_key="space", title="空间",
                         subtitle="伴生体把日子留在这里；桌面是常驻的小地方，小世界是偶尔会去的另一处。",
                         parent=parent)
        self._on_desktop = on_desktop
        self.setMinimumSize(760, 560)
        self.resize(900, 680)
        self._runtime_notice = ""
        self._build_space_view()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def _build_space_view(self):
        self.content.setSpacing(14)

        status = QFrame()
        status.setObjectName("spaceSection")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(22, 19, 22, 18)
        status_layout.setSpacing(5)
        title = QLabel("伴生体现在在哪里")
        title.setObjectName("spaceSectionTitle")
        self.location = QLabel()
        self.location.setObjectName("spaceLocation")
        self.activity = QLabel()
        self.activity.setObjectName("spaceActivity")
        self.activity.setWordWrap(True)
        self.context = QLabel()
        self.context.setObjectName("spaceMuted")
        self.context.setWordWrap(True)
        status_layout.addWidget(title)
        status_layout.addWidget(self.location)
        status_layout.addWidget(self.activity)
        status_layout.addWidget(self.context)
        self.content.addWidget(status)

        traces = QHBoxLayout()
        traces.setSpacing(14)
        traces.addWidget(self._build_trace_section(), 1)
        traces.addWidget(self._build_recent_section(), 1)
        self.content.addLayout(traces, 1)

        entry_heading = QLabel("可以去看看")
        entry_heading.setObjectName("spaceSectionTitle")
        self.content.addWidget(entry_heading)
        entries = QHBoxLayout()
        entries.setSpacing(14)
        entries.addWidget(self._build_entry(
            "他的桌面", "独自拥有的数字桌面，物件和痕迹都会慢慢留下来。",
            "看看桌面", self.open_desktop
        ), 1)
        entries.addWidget(self._build_entry(
            "小世界", "另一个可以进入的地方；天气和活动会自己往前走。",
            "进入", self.toggle_world
        ), 1)
        self.content.addLayout(entries)

    def _build_trace_section(self):
        box = QFrame()
        box.setObjectName("spaceSection")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(8)
        heading = QLabel("房间里的痕迹")
        heading.setObjectName("spaceSectionTitle")
        self.traces = QLabel()
        self.traces.setObjectName("spaceBody")
        self.traces.setWordWrap(True)
        self.house_hint = QLabel()
        self.house_hint.setObjectName("spaceMuted")
        self.house_hint.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(self.traces)
        layout.addWidget(self.house_hint)
        layout.addStretch()
        return box

    def _build_recent_section(self):
        box = QFrame()
        box.setObjectName("spaceSection")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(8)
        heading = QLabel("最近留下的东西")
        heading.setObjectName("spaceSectionTitle")
        self.recent = QLabel()
        self.recent.setObjectName("spaceBody")
        self.recent.setWordWrap(True)
        self.collection_hint = QLabel()
        self.collection_hint.setObjectName("spaceMuted")
        self.collection_hint.setWordWrap(True)
        self.runtime_hint = QLabel()
        self.runtime_hint.setObjectName("spaceNotice")
        self.runtime_hint.setWordWrap(True)
        self.runtime_hint.setVisible(False)
        layout.addWidget(heading)
        layout.addWidget(self.recent)
        layout.addWidget(self.collection_hint)
        layout.addWidget(self.runtime_hint)
        layout.addStretch()
        return box

    def _build_entry(self, title, description, action, callback):
        box = QFrame()
        box.setObjectName("spaceEntry")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("spaceEntryTitle")
        desc = QLabel(description)
        desc.setObjectName("spaceMuted")
        desc.setWordWrap(True)
        button = QPushButton(action)
        button.setObjectName("spaceEntryButton")
        button.clicked.connect(callback)
        layout.addWidget(label)
        layout.addWidget(desc, 1)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        if title == "小世界":
            self.world_button = button
        elif title == "他的桌面":
            self.desktop_button = button
        return box

    def _stylesheet(self):
        t = self._theme
        return super()._stylesheet() + f"""
        QFrame#spaceSection {{ background: {_rgba(t['tint'], 208)}; border: 1px solid {_rgba(t['muted'], 125)}; border-radius: 18px; }}
        QFrame#spaceEntry {{ background: {_rgba(t['tint'], 215)}; border: 1px solid {_rgba(t['muted'], 130)}; border-left: 3px solid rgb{t['accent']}; border-radius: 16px; }}
        QLabel#spaceSectionTitle {{ color: rgb{t['text']}; font-size: 11pt; font-weight: 600; }}
        QLabel#spaceLocation {{ color: rgb{t['text']}; font-size: 19pt; font-weight: 600; }}
        QLabel#spaceActivity {{ color: rgb{t['text']}; font-size: 11pt; }}
        QLabel#spaceEntryTitle {{ color: rgb{t['text']}; font-size: 13pt; font-weight: 600; }}
        QLabel#spaceBody {{ color: rgb{t['text']}; font-size: 10pt; line-height: 1.4; }}
        QLabel#spaceMuted {{ color: rgb{t['muted']}; font-size: 9pt; }}
        QLabel#spaceNotice {{ color: rgb{t['accent']}; font-size: 9pt; }}
        QPushButton#spaceEntryButton {{ background: rgb{t['accent']}; color: rgb{t['tint']}; border: none; border-radius: 10px; padding: 8px 14px; font-weight: 600; }}
        QPushButton#spaceEntryButton:hover {{ background: rgb{t['text']}; color: rgb{t['tint']}; }}
        """

    def _world_state(self):
        world = getattr(self.chen, "world", None)
        try:
            return world.get_state() if world else {}
        except Exception:
            return {}

    @staticmethod
    def _item_names(items, limit=4):
        result = []
        for item in list(items or [])[-limit:]:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                result.append(name[:18] + ("…" if len(name) > 18 else ""))
            else:
                text = str(item).strip()
                result.append(text[:18] + ("…" if len(text) > 18 else ""))
        return result

    def _presence(self, state):
        try:
            if hasattr(self.chen, "get_presence_snapshot"):
                return self.chen.get_presence_snapshot(persist=False) or {}
        except Exception:
            pass
        return state

    def refresh(self):
        state = self._world_state()
        presence = self._presence(state)
        house = state.get("house_state") or {}
        in_world = state.get("current_world") == "small_world"
        if in_world:
            location = state.get("activity_place") or "小世界"
            activity = state.get("activity") or presence.get("activity") or "正在这里待着"
            detail = []
            if state.get("weather"):
                detail.append(str(state["weather"]))
            if state.get("time_period"):
                detail.append(str(state["time_period"]))
            familiarity = (state.get("familiarity") or {}).get(state.get("activity_place"), 0)
            if familiarity:
                detail.append(f"熟悉度 {familiarity}")
            self.context.setText(" · ".join(detail) or "小世界正在运行。")
        else:
            location = "他的桌面"
            activity = presence.get("activity") or "安静待着"
            detail = []
            if state.get("last_visit_summary"):
                detail.append(f"上次从小世界回来：{state['last_visit_summary']}")
            if state.get("weather"):
                detail.append(f"外面的天气：{state['weather']}")
            self.context.setText("\n".join(detail) or "这是他的常驻空间。")
        self.location.setText(str(location))
        self.activity.setText(str(activity))

        desk = self._item_names(house.get("desk_items"))
        wall = self._item_names(house.get("wall_items"))
        drawer = self._item_names(house.get("drawer_items"))
        trace_lines = []
        if desk:
            trace_lines.append("桌上  ·  " + "、".join(desk[:3]))
        if wall:
            trace_lines.append("墙上  ·  " + "、".join(wall[:3]))
        if drawer:
            trace_lines.append("抽屉  ·  " + "、".join(drawer[:3]))
        self.traces.setText("\n".join(trace_lines) or "房间里还没有可显示的物件。")
        house_flags = []
        if house.get("light_on") is True:
            house_flags.append("灯亮着")
        if house.get("window_open") is True:
            house_flags.append("窗户开着")
        if house.get("bed_made") is False:
            house_flags.append("床还没整理")
        self.house_hint.setText(" · ".join(house_flags) or "房间保持着安静的样子。")

        world = getattr(self.chen, "world", None)
        collections = list(getattr(world, "collections", []) or []) if world else []
        recent = collections[-3:]
        recent_lines = []
        for item in recent:
            name = str(item.get("name", "一件东西"))
            rarity = str(item.get("rarity", "普通"))
            place = str(item.get("place", ""))
            recent_lines.append(f"{name}（{rarity}）" + (f" · {place}" if place else ""))
        self.recent.setText("\n".join(recent_lines) or "还没有从小世界带回收藏。")
        if collections:
            self.collection_hint.setText(f"收藏里一共留下了 {len(collections)} 件东西。")
        else:
            self.collection_hint.setText("以后遇到想留下的东西，可以带回来。")
        self.world_button.setText("离开" if in_world else "进入")
        unread = 0
        try:
            from .pyside_virtual_desktop import desktop_unread_count
            unread = desktop_unread_count(self.chen)
        except Exception:
            unread = 0
        self.desktop_button.setText(f"看看桌面 · {unread} 条新内容" if unread else "看看桌面")
        self.runtime_hint.setText(self._runtime_notice)
        self.runtime_hint.setVisible(bool(self._runtime_notice))

    def open_desktop(self):
        if callable(self._on_desktop):
            self._on_desktop()
            return
        state = self._world_state()
        house = state.get("house_state") or {}
        world = getattr(self.chen, "world", None)
        collections = list(getattr(world, "collections", []) or []) if world else []
        def details(items):
            rows = []
            for item in list(items or []):
                if isinstance(item, dict):
                    text = str(item.get("name", "一件东西"))
                    if item.get("desc"):
                        text += f"：{item['desc']}"
                    rows.append(text)
                else:
                    rows.append(str(item))
            return "\n".join(rows)
        collection_text = "\n".join(
            f"{item.get('name', '一件东西')}（{item.get('rarity', '普通')}）"
            for item in collections[-12:]
        )
        dialog = _SpaceDetailDialog("他的桌面", [
            ("桌上", details(house.get("desk_items"))),
            ("墙面", details(house.get("wall_items"))),
            ("抽屉", details(house.get("drawer_items"))),
            ("收藏", collection_text),
        ], self)
        dialog.exec()

    def _launch_mv_runtime(self):
        if os.name != "nt":
            return False, "当前系统不是 Windows，先保留小世界状态。"
        if not GODOT_RUNTIME.exists() or not GODOT_EXE.exists():
            return False, "小世界入口还在，但 Godot 房间文件暂时找不到。"
        try:
            probe = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Godot_v4.7.2-stable_win64.exe"],
                capture_output=True, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            raw = probe.stdout or b""
            out = ""
            for enc in ("utf-8", "gbk", "cp936"):
                try:
                    out = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not out:
                out = raw.decode("gbk", errors="ignore")
            if GODOT_EXE.name.lower() in out.lower():
                return True, "小世界状态已进入，Godot 房间已经在运行。"
            appdata_dir = GODOT_RUNTIME / ".godot_user"
            localappdata_dir = GODOT_RUNTIME / ".godot_local"
            appdata_dir.mkdir(parents=True, exist_ok=True)
            localappdata_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["APPDATA"] = str(appdata_dir)
            env["LOCALAPPDATA"] = str(localappdata_dir)
            subprocess.Popen(
                [str(GODOT_EXE), "--path", str(GODOT_RUNTIME)], cwd=str(GODOT_RUNTIME),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            return True, "小世界状态已进入，正在打开 Godot 房间。"
        except Exception as exc:
            return False, f"小世界入口还在，但 Godot 暂时没有打开：{exc}"

    def toggle_world(self):
        world = getattr(self.chen, "world", None)
        if not world:
            self._runtime_notice = "小世界状态还没有连接到当前运行时。"
            self.refresh()
            return
        try:
            if world.is_in_small_world():
                world.leave_small_world()
                self._runtime_notice = "已经回到他的桌面。"
            else:
                world.enter_small_world()
                _, self._runtime_notice = self._launch_mv_runtime()
        except Exception as exc:
            self._runtime_notice = f"空间状态没有更新：{exc}"
        self.refresh()


# 兼容旧调用；新的 UI 名称是“空间”。
WorldPanel = SpacePanel


class WorkshopPanel(StaticPanelWindow):
    """“修理台”后台控制台：窄导航、简洁总览和独立详情页。"""

    def __init__(self, chen=None, parent=None):
        self._detail_pages = {}
        self._setting_rows = {}
        self._home_cards = {}
        self._detail_badges = {}
        self._mcp_tools_layout = None
        self._mcp_enabled_check = None
        self._mcp_autostart_check = None
        self._overview_nav_btn = None
        self._workshop_notice = ""
        self._model_health_cache = ("状态未知", 0.0)
        super().__init__(chen, panel_key="workshop", title="修理台",
                         subtitle="查看运行状态、外部能力和需要你处理的权限。",
                         parent=parent)
        self.setMinimumSize(860, 600)
        self.resize(920, 680)
        self._build_workshop_view()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def _build_workshop_view(self):
        self.content.setSpacing(12)
        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(12)
        workspace.addWidget(self._build_sidebar(), 0)

        self.stack = QStackedWidget()
        self.home_page = self._build_home_page()
        self.stack.addWidget(self.home_page)
        self._detail_pages["runtime"] = self._build_runtime_page()
        self._detail_pages["mcp"] = self._build_mcp_page()
        self._detail_pages["connections"] = self._build_connections_page()
        self._detail_pages["security"] = self._build_security_page()
        self._detail_pages["logs"] = self._build_logs_page()
        for page in self._detail_pages.values():
            self.stack.addWidget(page)

        workspace.addWidget(self.stack, 1)
        self.content.addLayout(workspace, 1)
        self._sync_nav_selection("home")

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("workshopSidebar")
        sidebar.setFixedWidth(190)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(5)

        self._overview_nav_btn = QPushButton("总览\n当前状态与模块入口")
        self._overview_nav_btn.setObjectName("workshopSettingRow")
        self._overview_nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._overview_nav_btn.setCheckable(True)
        self._overview_nav_btn.setMinimumHeight(48)
        self._overview_nav_btn.clicked.connect(self.show_home)
        layout.addWidget(self._overview_nav_btn)

        layout.addSpacing(5)
        layout.addWidget(self._section_label("系统"))
        layout.addWidget(self._setting_row("runtime", "运行状态", "生命循环、模型和后台状态。", self.open_runtime))
        layout.addWidget(self._setting_row("mcp", "外部工具", "MCP 服务与工具范围。", self.open_mcp))
        layout.addWidget(self._setting_row("connections", "设备连接", "手机、桌宠、小世界和模型。", self.open_connections))

        layout.addSpacing(5)
        layout.addWidget(self._section_label("边界与记录"))
        layout.addWidget(self._setting_row("security", "权限安全", "确认规则与调用限制。", self.open_security))
        layout.addWidget(self._setting_row("logs", "日志诊断", "工具调用与异常摘要。", self.open_logs))

        layout.addStretch()
        hint = QLabel("右键窗口可更换静态背景")
        hint.setObjectName("workshopSidebarHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return sidebar

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                WorkshopPanel._clear_layout(item.layout())

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("workshopSectionLabel")
        return label

    def _setting_row(self, key, title, description, callback):
        row = QPushButton()
        row.setObjectName("workshopSettingRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setCheckable(True)
        row.setMinimumHeight(48)
        row.setToolTip(description)
        row.setText(f"{title}\n状态读取中")
        row.clicked.connect(callback)
        self._setting_rows[key] = row
        return row

    def _sync_nav_selection(self, active_key: str):
        if self._overview_nav_btn is not None:
            self._overview_nav_btn.setChecked(active_key == "home")
        for key, btn in self._setting_rows.items():
            btn.setChecked(key == active_key)

    def _build_dashboard_card(self, key: str, title: str, subtitle: str, actions: list[tuple[str, Callable[[], None]]]):
        card = QFrame()
        card.setObjectName("workshopCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("workshopCardTitle")
        head.addWidget(heading)
        head.addStretch()
        for btn_text, callback in actions:
            btn = QPushButton(btn_text)
            btn.setObjectName("workshopCardAction")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(callback)
            head.addWidget(btn)
        layout.addLayout(head)

        sub = QLabel(subtitle)
        sub.setObjectName("workshopMuted")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        divider = QFrame()
        divider.setObjectName("workshopDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        body = QLabel("正在读取状态…")
        body.setObjectName("workshopCardBody")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(body, 1)
        self._home_cards[key] = body
        return card

    def _build_home_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        summary = QFrame()
        summary.setObjectName("workshopOverview")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(14, 8, 14, 8)
        summary_layout.setSpacing(12)
        self.overview_status = QLabel()
        self.overview_status.setObjectName("workshopOverviewStatus")
        self.overview_detail = QLabel()
        self.overview_detail.setObjectName("workshopOverviewDetail")
        self.overview_detail.setWordWrap(True)
        summary_layout.addWidget(self.overview_status, 0)
        summary_layout.addWidget(self.overview_detail, 1)
        root.addWidget(summary)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        cards = [
            ("runtime", "运行状态", "生命循环、模型服务和后台循环。", [("详情", self.open_runtime)]),
            ("mcp", "外部工具", "MCP 开关、自启动策略与工具范围。", [("配置", self.open_mcp)]),
            ("connections", "设备与连接", "手机、桌宠、小世界与模型服务。", [("详情", self.open_connections)]),
            ("security", "权限与安全", "执行边界、确认规则与调用配额。", [("查看", self.open_security)]),
            ("logs", "日志与诊断", "最近工具调用与后台异常摘要。", [("查看", self.open_logs)]),
        ]
        for index, (key, title, subtitle, actions) in enumerate(cards):
            row, col = divmod(index, 2)
            grid.addWidget(self._build_dashboard_card(key, title, subtitle, actions), row, col)
        grid.addWidget(self._build_dashboard_card(
            "boundary_summary", "记录边界", "日志只保留安全摘要，不展示令牌和文件正文。", [("日志", self.open_logs)]
        ), 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for row in range(3):
            grid.setRowStretch(row, 1)
        root.addLayout(grid, 1)
        return page

    def _detail_shell(self, title, subtitle, badge_key: str | None = None):
        page = QFrame()
        page.setObjectName("workshopDetailPanel")
        root = QVBoxLayout(page)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("workshopDetailTitle")
        header.addWidget(heading)
        header.addStretch()
        back = QPushButton("‹ 返回总览")
        back.setObjectName("workshopBackButton")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.show_home)
        header.addWidget(back)
        root.addLayout(header)

        label = QLabel(subtitle)
        label.setObjectName("workshopMuted")
        label.setWordWrap(True)
        root.addWidget(label)

        divider = QFrame()
        divider.setObjectName("workshopDivider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        if badge_key:
            badge_bar = QLabel()
            badge_bar.setObjectName("workshopBadgeBar")
            badge_bar.setWordWrap(True)
            root.addWidget(badge_bar)
            self._detail_badges[badge_key] = badge_bar
        return page, root

    def _build_runtime_page(self):
        page, root = self._detail_shell(
            "运行状态",
            "这里显示伴生体当前的生命循环、模型服务和已连接客户端。",
            badge_key="runtime",
        )
        self.runtime_view = self._add_detail_text(root)
        return page

    def _build_connections_page(self):
        page, root = self._detail_shell(
            "设备与连接",
            "每个连接都只显示它实际暴露的状态；iPhone 与电脑同一 Wi-Fi 下可用下方按钮复制连接地址或快捷指令 Token。",
            badge_key="connections",
        )
        self.connections_view = self._add_detail_text(root)
        actions = QHBoxLayout()
        copy_url_btn = QPushButton("复制手机端完整链接")
        copy_url_btn.setObjectName("workshopActionButton")
        copy_url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_url_btn.clicked.connect(self._copy_mobile_url)
        copy_tok_btn = QPushButton("复制快捷指令 Token")
        copy_tok_btn.setObjectName("workshopActionButton")
        copy_tok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_tok_btn.clicked.connect(self._copy_mobile_token)
        actions.addWidget(copy_url_btn)
        actions.addWidget(copy_tok_btn)
        actions.addStretch()
        root.addLayout(actions)
        return page

    def _mobile_connection_info(self) -> tuple[str, str]:
        base_dir = Path(getattr(self.chen, "base_dir", ROOT))
        token_path = base_dir / "memory" / "remote_token.txt"
        token = ""
        try:
            if token_path.exists():
                token = token_path.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        ip = "127.0.0.1"
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        url = f"http://{ip}:8765/mobile#token={token}" if token else f"http://{ip}:8765/mobile"
        return ip, token

    def _copy_mobile_url(self):
        ip, token = self._mobile_connection_info()
        url = f"http://{ip}:8765/mobile#token={token}" if token else f"http://{ip}:8765/mobile"
        QApplication.clipboard().setText(url)
        QMessageBox.information(self, "已复制手机端链接", f"已复制到剪贴板：\n{url}\n\n在 iPhone Safari 打开即可直接进入伴生界面。")

    def _copy_mobile_token(self):
        _ip, token = self._mobile_connection_info()
        if not token:
            QMessageBox.information(self, "快捷指令 Token", "尚未生成 remote_token.txt，请先启动一次手机服务。")
            return
        QApplication.clipboard().setText(token)
        QMessageBox.information(self, "已复制 Token", "已复制 X-Chen-Token 到剪贴板，可粘贴到 iOS「快捷指令」请求头中。")

    def _build_security_page(self):
        page, root = self._detail_shell(
            "权限与安全",
            "执行类动作保持确认边界，日志只保留安全摘要。",
            badge_key="security",
        )
        self.security_view = self._add_detail_text(root)
        self._security_pending_box = QFrame()
        self._security_pending_box.setObjectName("workshopSection")
        self._security_pending_layout = QVBoxLayout(self._security_pending_box)
        self._security_pending_layout.setContentsMargins(12, 10, 12, 10)
        self._security_pending_layout.setSpacing(8)
        root.addWidget(self._security_pending_box)
        return page

    def _resolve_tool_request(self, request_id: str, approved: bool):
        runtime = getattr(self.chen, "runtime", None)
        if not runtime:
            return
        runtime.approve_tool(request_id, approved)
        self.refresh()

    def _render_pending_tool_requests(self):
        layout = getattr(self, "_security_pending_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        runtime = getattr(self.chen, "runtime", None)
        pending = runtime.pending_tool_requests() if runtime and hasattr(runtime, "pending_tool_requests") else []
        if not pending:
            lbl = QLabel("当前没有等待确认的工具请求。")
            lbl.setObjectName("workshopMuted")
            layout.addWidget(lbl)
            return
        for req in pending:
            row = QHBoxLayout()
            req_id = str(req.get("request_id", ""))
            tool_name = str(req.get("tool", "未知工具"))
            info = QLabel(f"待确认请求：{tool_name}（来源：{req.get('source', 'user')}）")
            info.setWordWrap(True)
            approve_btn = QPushButton("允许执行")
            approve_btn.setObjectName("workshopActionButton")
            approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            approve_btn.clicked.connect(lambda _c=False, rid=req_id: self._resolve_tool_request(rid, True))
            deny_btn = QPushButton("拒绝")
            deny_btn.setObjectName("workshopActionButton")
            deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            deny_btn.clicked.connect(lambda _c=False, rid=req_id: self._resolve_tool_request(rid, False))
            row.addWidget(info, 1)
            row.addWidget(approve_btn)
            row.addWidget(deny_btn)
            holder = QWidget()
            holder.setLayout(row)
            layout.addWidget(holder)

    def _build_logs_page(self):
        page, root = self._detail_shell(
            "日志与诊断",
            "最近工具调用来自本地审计日志，不显示令牌和文件正文。",
            badge_key="logs",
        )
        self.logs_view = self._add_detail_text(root)
        actions = QHBoxLayout()
        actions.addStretch()
        refresh = QPushButton("刷新记录")
        refresh.setObjectName("workshopActionButton")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        root.addLayout(actions)
        return page

    def _add_detail_text(self, root):
        view = QPlainTextEdit()
        view.setObjectName("workshopConsoleReadout")
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(view, 1)
        return view

    def _build_mcp_page(self):
        page, root = self._detail_shell(
            "MCP 外部工具",
            "把外部服务当成可管理的应用接入；默认关闭，执行动作仍需经过工具层。",
        )
        self.mcp_summary = QLabel()
        self.mcp_summary.setObjectName("workshopNotice")
        self.mcp_summary.setWordWrap(True)
        root.addWidget(self.mcp_summary)

        options = QFrame()
        options.setObjectName("workshopSection")
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(14, 10, 14, 10)
        options_layout.setSpacing(20)
        self._mcp_enabled_check = QCheckBox("启用 MCP 外部工具")
        self._mcp_enabled_check.clicked.connect(self._set_mcp_enabled)
        self._mcp_autostart_check = QCheckBox("伴生体启动时自动启动外部服务")
        self._mcp_autostart_check.clicked.connect(self._set_mcp_autostart)
        options_layout.addWidget(self._mcp_enabled_check)
        options_layout.addWidget(self._mcp_autostart_check)
        options_layout.addStretch()
        root.addWidget(options)

        tools_heading = self._section_label("已注册工具清单")
        root.addWidget(tools_heading)
        tools_scroll = QScrollArea()
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._mcp_tools_layout = QVBoxLayout(holder)
        self._mcp_tools_layout.setContentsMargins(0, 0, 6, 0)
        self._mcp_tools_layout.setSpacing(6)
        self._mcp_tools_layout.addStretch()
        tools_scroll.setWidget(holder)
        root.addWidget(tools_scroll, 1)

        empty = QLabel("外部服务列表：当前项目尚未接入独立 MCP Server；接入后将在此展示服务连接状态与工具范围。")
        empty.setObjectName("workshopMuted")
        empty.setWordWrap(True)
        root.addWidget(empty)
        return page

    def _config(self):
        config = getattr(self.chen, "config", {})
        return config if isinstance(config, dict) else {}

    def _save_config(self):
        base_dir = getattr(self.chen, "base_dir", ROOT)
        path = Path(base_dir) / "system" / "config.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._config(), ensure_ascii=False, indent=4), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _set_mcp_enabled(self, checked):
        config = self._config()
        config.setdefault("tools", {})["mcp_enabled"] = bool(checked)
        self._workshop_notice = "MCP 设置已保存；当前项目仍不会绕过工具层直接控制外部服务。" if self._save_config() else "MCP 设置未能写入配置文件。"
        self.refresh()

    def _set_mcp_autostart(self, checked):
        config = self._config()
        config.setdefault("runtime", {})["mcp_autostart"] = bool(checked)
        self._workshop_notice = "启动方式已保存，下次启动伴生体时生效。" if self._save_config() else "启动方式未能写入配置文件。"
        self.refresh()

    def _tool_specs(self):
        runtime = getattr(self.chen, "runtime", None)
        tools = getattr(runtime, "tools", None) if runtime else getattr(self.chen, "tools", None)
        try:
            return list(tools.describe()) if tools else []
        except Exception:
            return []

    @staticmethod
    def _level_text(spec):
        level = {"observe": "观察", "suggest": "建议", "execute": "执行"}.get(spec.get("level"), spec.get("level", "未知"))
        if spec.get("requires_confirmation"):
            level += " · 需确认"
        return level

    def _tool_detail(self, spec):
        description = str(spec.get("description", "暂时没有描述。"))
        QMessageBox.information(
            self,
            str(spec.get("name", "工具详情")),
            f"{description}\n\n类别：{spec.get('category', '未分类')}\n"
            f"权限等级：{self._level_text(spec)}\n"
            f"状态：{'已启用' if spec.get('enabled') else '未启用'}",
        )

    def _render_mcp_tools(self):
        if self._mcp_tools_layout is None:
            return
        self._clear_layout(self._mcp_tools_layout)
        specs = self._tool_specs()
        if not specs:
            empty = QLabel("还没有注册工具。")
            empty.setObjectName("workshopMuted")
            self._mcp_tools_layout.addWidget(empty)
        else:
            for spec in specs:
                state_tag = "启用" if spec.get("enabled", True) else "停用"
                name = spec.get("name", spec.get("id", "工具"))
                desc = spec.get("description", "无描述")
                button = QPushButton(
                    f"[{state_tag}] {name}  ·  {self._level_text(spec)}\n{desc}"
                )
                button.setObjectName("workshopToolRow")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setMinimumHeight(54)
                button.clicked.connect(lambda _checked=False, item=spec: self._tool_detail(item))
                self._mcp_tools_layout.addWidget(button)
        self._mcp_tools_layout.addStretch()

    def _model_health(self) -> str:
        model = getattr(self.chen, "model_server", None)
        model_state, checked_at = self._model_health_cache
        now = datetime.now().timestamp()
        if model and (now - checked_at) > 15:
            try:
                model_state = "健康" if model.is_healthy() else "未就绪"
            except Exception:
                model_state = "状态未知"
            self._model_health_cache = (model_state, now)
        elif not model:
            model_state = "未配置"
        return model_state

    def refresh(self):
        runtime = getattr(self.chen, "runtime", None)
        snapshot = runtime.snapshot() if runtime else {}
        config = self._config()
        tools = self._tool_specs()
        enabled = bool((config.get("tools", {}) or {}).get("mcp_enabled", False))
        runtime_cfg = config.get("runtime", {}) or {}
        autostart = bool(runtime_cfg.get("mcp_autostart", False))
        clients = list(snapshot.get("clients", []) or [])
        running = bool(snapshot.get("running"))
        pending_count = int(snapshot.get("pending_requests", 0) or 0)
        model_state = self._model_health()

        # 总览条只保留一处全局状态，避免与卡片和左侧导航重复堆叠。
        self.overview_status.setText("● 运行正常" if running else "○ 运行未启动")
        self.overview_detail.setText(
            f"{len(clients)} 个客户端 · {len(tools)} 个已注册工具 · {pending_count} 个待确认请求"
        )

        # 左侧导航栏紧凑状态更新（去除原本的三行堆叠与长串空格）
        self._setting_rows["runtime"].setText(
            f"运行状态\n{'运行中' if running else '未启动'} · {len(clients)} 个客户端"
        )
        self._setting_rows["mcp"].setText(
            f"MCP 外部工具\n{'已启用' if enabled else '默认关闭'} · {len(tools)} 个已注册工具"
        )
        connection_count = len(clients)
        self._setting_rows["connections"].setText(
            f"设备与连接\n{connection_count} 个客户端已注册"
        )
        self._setting_rows["security"].setText(
            f"权限与安全\n{pending_count} 个请求等待确认"
        )
        self._setting_rows["logs"].setText(
            "日志与诊断\n本地审计记录"
        )

        # 总览卡片只显示进入详情页前必须知道的摘要。
        if self._home_cards:
            started_raw = str(snapshot.get("started_at") or "没有记录")
            started_short = started_raw[-8:] if "T" in started_raw else started_raw
            self._home_cards["runtime"].setText(
                f"· 生命循环：{'运行中' if running else '未启动'}\n"
                f"· 模型服务：{model_state}\n"
                f"· 启动记录：{started_short}"
            )
            self._home_cards["mcp"].setText(
                f"· 服务状态：{'已启用' if enabled else '默认关闭'}\n"
                f"· 自启策略：{'随伴生体自动启动' if autostart else '手动按需启动'}\n"
                f"· 工具数量：已注册 {len(tools)} 个内部/外部工具"
            )
            features = config.get("features", {}) or {}
            phone_enabled = bool((features.get("phone_push", {}) or {}).get("enabled"))
            bridge_ok = bool(getattr(self.chen, "world_bridge", None))
            self._home_cards["connections"].setText(
                f"· 在线客户端：{'、'.join(clients) if clients else '无'}\n"
                f"· 手机推送接口：{'启用' if phone_enabled else '未启用'}\n"
                f"· 远程状态桥接：{'已注册' if bridge_ok else '未注册'}"
            )
            execute_count = sum(1 for item in tools if item.get("level") == "execute")
            confirm_count = sum(1 for item in tools if item.get("requires_confirmation"))
            limits = getattr(getattr(self.chen, "runtime_policy", None), "limits", None)
            per_min = getattr(limits, "max_tool_calls_per_minute", "未知")
            per_day = getattr(limits, "max_external_calls_per_day", "未知")
            self._home_cards["security"].setText(
                f"· 敏感工具：{execute_count} 个执行类 · {confirm_count} 个需确认\n"
                f"· 速率限制：{per_min} 次/分 · 外部 {per_day} 次/日\n"
                f"· 待确认请求：{pending_count} 个"
            )
            audit_path = Path(getattr(self.chen, "base_dir", ROOT)) / "logs" / "tool_calls.jsonl"
            try:
                audit_count = len(audit_path.read_text(encoding="utf-8").splitlines()) if audit_path.exists() else 0
            except OSError:
                audit_count = 0
            self._home_cards["logs"].setText(
                f"· 最近调用记录：{audit_count} 条\n"
                "· 只展示安全摘要，不显示令牌和文件正文"
            )
            self._home_cards["boundary_summary"].setText(
                f"· 当前待确认请求：{pending_count} 个\n"
                "· 执行类动作保留确认边界，日志只记录必要摘要"
            )

        # 详情页顶部摘要条与终端内容
        self.runtime_view.setPlainText(self._runtime_text(snapshot))
        self.connections_view.setPlainText(self._connections_text(snapshot))
        self.security_view.setPlainText(self._security_text(snapshot, config))
        audit_content = self._audit_text()
        self.logs_view.setPlainText(audit_content)

        if "runtime" in self._detail_badges:
            self._detail_badges["runtime"].setText(
                f"当前状态：{'● 运行中' if running else '○ 未启动'}   |   "
                f"模型：{model_state}   |   在线客户端：{len(clients)} 个"
            )
        if "connections" in self._detail_badges:
            self._detail_badges["connections"].setText(
                f"已挂载客户端：{len(clients)} 个   |   "
                f"远程状态桥接：{'已就绪' if getattr(self.chen, 'world_bridge', None) else '未注册'}"
            )
        if "security" in self._detail_badges:
            self._detail_badges["security"].setText(
                f"待确认请求：{pending_count} 个   |   "
                f"MCP 默认关闭：{'是' if not enabled else '否（已开启）'}"
            )
        if "logs" in self._detail_badges:
            self._detail_badges["logs"].setText(
                "日志源：logs/tool_calls.jsonl（展示最近 30 条调用记录）"
            )

        self._mcp_enabled_check.setChecked(enabled)
        self._mcp_autostart_check.setChecked(autostart)
        self.mcp_summary.setText(
            (self._workshop_notice + "\n" if self._workshop_notice else "") +
            ("MCP 已启用，但所有外部动作仍需经过伴生体的工具层和权限检查。"
             if enabled else "MCP 当前默认关闭；打开后也不会绕过工具层直接控制电脑。")
        )
        self._render_mcp_tools()
        self._render_pending_tool_requests()

    def _runtime_text(self, snapshot):
        model_state = self._model_health()
        return "\n".join([
            f"运行时：{'运行中' if snapshot.get('running') else '未启动'}",
            f"启动时间：{snapshot.get('started_at') or '没有记录'}",
            f"模型服务：{model_state}",
            f"当前客户端：{'、'.join(snapshot.get('clients', []) or []) or '无'}",
            f"待处理权限：{snapshot.get('pending_requests', 0)}",
        ])

    def _connections_text(self, snapshot):
        config = self._config()
        features = config.get("features", {}) or {}
        phone = features.get("phone_push", {}) or {}
        ip, token = self._mobile_connection_info()
        ext = getattr(self.chen, "external_presence", {}) or {}
        ext_line = f"{ext.get('device', '未同步')} · {ext.get('activity', '无')}（{ext.get('updated_at', '无记录')}）" if ext.get("activity") else "尚未收到手机状态同步"
        rows = [
            "已注册客户端：",
            *[f"- {client}" for client in (snapshot.get("clients", []) or [])],
            "",
            f"局域网伴生主页：http://{ip}:8765/mobile",
            f"快捷指令状态接口：POST http://{ip}:8765/api/presence",
            f"快捷指令聊天接口：POST http://{ip}:8765/api/chat",
            f"请求头认证字段：X-Chen-Token（点击下方按钮可一键复制）",
            f"最近手机同步：{ext_line}",
            f"本地推送服务：{'启用' if phone.get('enabled') else '未启用'}",
            f"小世界状态桥接：{'已注册' if getattr(self.chen, 'world_bridge', None) else '未注册'}",
        ]
        return "\n".join(rows)

    def _security_text(self, snapshot, config):
        limits = getattr(getattr(self.chen, "runtime_policy", None), "limits", None)
        tools = self._tool_specs()
        execute_count = sum(1 for item in tools if item.get("level") == "execute")
        confirm_count = sum(1 for item in tools if item.get("requires_confirmation"))
        return "\n".join([
            f"执行类工具：{execute_count} 个",
            f"需要确认的工具：{confirm_count} 个",
            f"待确认请求：{snapshot.get('pending_requests', 0)} 个",
            f"MCP 默认关闭：{'是' if not (config.get('tools', {}) or {}).get('mcp_enabled') else '否'}",
            f"每分钟工具调用上限：{getattr(limits, 'max_tool_calls_per_minute', '未知')}",
            f"每日外部调用上限：{getattr(limits, 'max_external_calls_per_day', '未知')}",
            "日志只记录安全摘要，不写入令牌和文件正文。",
        ])

    def _audit_text(self):
        base_dir = getattr(self.chen, "base_dir", ROOT)
        path = Path(base_dir) / "logs" / "tool_calls.jsonl"
        lines = []
        try:
            if path.exists():
                for raw in path.read_text(encoding="utf-8").splitlines()[-30:]:
                    try:
                        item = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    status = item.get("status") or ("成功" if item.get("ok") else "失败")
                    error = str(item.get("error", ""))[:120]
                    suffix = f"\n  {error}" if error else ""
                    lines.append(f"{item.get('at', '')} · {item.get('tool', '')} · {status}{suffix}")
        except OSError:
            pass
        return "\n\n".join(reversed(lines)) if lines else "暂时还没有工具调用记录。"

    def show_home(self):
        self.stack.setCurrentWidget(self.home_page)
        self._sync_nav_selection("home")

    def open_runtime(self):
        self.stack.setCurrentWidget(self._detail_pages["runtime"])
        self._sync_nav_selection("runtime")

    def open_mcp(self):
        self.stack.setCurrentWidget(self._detail_pages["mcp"])
        self._sync_nav_selection("mcp")

    def open_connections(self):
        self.stack.setCurrentWidget(self._detail_pages["connections"])
        self._sync_nav_selection("connections")

    def open_security(self):
        self.stack.setCurrentWidget(self._detail_pages["security"])
        self._sync_nav_selection("security")

    def open_logs(self):
        self.stack.setCurrentWidget(self._detail_pages["logs"])
        self._sync_nav_selection("logs")

    def _stylesheet(self):
        t = self._theme
        return super()._stylesheet() + f"""
        QFrame#workshopOverview {{
            background: {_rgba(t['tint'], 175)};
            border: 1px solid {_rgba(t['accent'], 140)};
            border-radius: 12px;
        }}
        QLabel#workshopOverviewStatus {{
            color: rgb{t['accent']};
            font-size: 11pt;
            font-weight: 600;
        }}
        QLabel#workshopOverviewDetail {{
            color: rgb{t['muted']};
            font-size: 8.5pt;
        }}
        QFrame#workshopSidebar {{
            background: {_rgba(t['tint'], 215)};
            border: 1px solid {_rgba(t['muted'], 115)};
            border-radius: 16px;
        }}
        QLabel#workshopSidebarHint {{
            color: {_rgba(t['muted'], 170)};
            font-size: 8.5pt;
            padding: 4px 4px 0 4px;
        }}
        QFrame#workshopCard, QFrame#workshopDetailPanel, QFrame#workshopSection {{
            background: {_rgba(t['tint'], 215)};
            border: 1px solid {_rgba(t['muted'], 120)};
            border-radius: 16px;
        }}
        QLabel#workshopCardTitle {{
            color: rgb{t['text']};
            font-size: 11.5pt;
            font-weight: 600;
        }}
        QLabel#workshopCardBody {{
            color: rgb{t['text']};
            font-size: 9.5pt;
            line-height: 1.45;
        }}
        QPushButton#workshopCardAction {{
            background: {_rgba(t['tint'], 160)};
            color: rgb{t['accent']};
            border: 1px solid {_rgba(t['accent'], 120)};
            border-radius: 8px;
            padding: 3px 10px;
            font-size: 8.5pt;
        }}
        QPushButton#workshopCardAction:hover {{
            background: rgb{t['accent']};
            color: rgb{t['tint']};
        }}
        QFrame#workshopDivider {{
            background: {_rgba(t['muted'], 80)};
            border: none;
        }}
        QLabel#workshopBadgeBar {{
            background: {_rgba(t['tint'], 165)};
            color: rgb{t['accent']};
            border: 1px solid {_rgba(t['muted'], 90)};
            border-radius: 10px;
            padding: 8px 12px;
            font-size: 9pt;
        }}
        QLabel#workshopDetailTitle {{
            color: rgb{t['text']};
            font-size: 14pt;
            font-weight: 600;
        }}
        QLabel#workshopSectionLabel {{
            color: rgb{t['muted']};
            font-size: 8.5pt;
            font-weight: 600;
            padding: 4px 6px 2px 6px;
        }}
        QLabel#workshopMuted {{
            color: rgb{t['muted']};
            font-size: 9pt;
        }}
        QLabel#workshopNotice {{
            color: rgb{t['accent']};
            font-size: 9.5pt;
            padding: 4px 0;
        }}
        QPushButton#workshopSettingRow {{
            background: {_rgba(t['tint'], 155)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(t['muted'], 80)};
            border-radius: 10px;
            padding: 7px 11px;
            text-align: left;
            font-size: 9pt;
        }}
        QPushButton#workshopSettingRow:hover {{
            border-color: {_rgba(t['accent'], 180)};
            background: {_rgba(t['tint'], 230)};
        }}
        QPushButton#workshopSettingRow:checked {{
            background: {_rgba(t['accent'], 42)};
            border: 1px solid rgb{t['accent']};
            color: rgb{t['text']};
            font-weight: 600;
        }}
        QPushButton#workshopToolRow {{
            background: {_rgba(t['tint'], 185)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(t['muted'], 100)};
            border-radius: 11px;
            padding: 9px 14px;
            text-align: left;
            font-size: 9.5pt;
        }}
        QPushButton#workshopToolRow:hover {{
            border-color: rgb{t['accent']};
            background: {_rgba(t['tint'], 235)};
        }}
        QPlainTextEdit#workshopConsoleReadout {{
            background: rgba(14, 16, 14, 185);
            color: rgb{t['text']};
            border: 1px solid {_rgba(t['muted'], 95)};
            border-radius: 12px;
            padding: 12px;
            font-family: "Consolas", "Microsoft YaHei UI", monospace;
            font-size: 9.5pt;
        }}
        QPushButton#workshopBackButton {{
            background: transparent;
            color: rgb{t['muted']};
            border: 1px solid {_rgba(t['muted'], 95)};
            border-radius: 8px;
            padding: 4px 10px;
            font-size: 9pt;
        }}
        QPushButton#workshopBackButton:hover {{
            color: rgb{t['accent']};
            border-color: rgb{t['accent']};
        }}
        QPushButton#workshopActionButton {{
            background: rgb{t['accent']};
            color: rgb{t['tint']};
            border: none;
            border-radius: 10px;
            padding: 7px 14px;
            font-weight: 600;
        }}
        QPushButton#workshopActionButton:hover {{
            background: rgb{t['text']};
        }}
        QCheckBox {{
            color: rgb{t['text']};
            spacing: 8px;
            padding: 3px 0;
            font-size: 9.5pt;
        }}
        QCheckBox::indicator {{
            width: 17px;
            height: 17px;
            border: 1px solid {_rgba(t['muted'], 170)};
            border-radius: 5px;
            background: {_rgba(t['tint'], 220)};
        }}
        QCheckBox::indicator:checked {{
            background: rgb{t['accent']};
            border-color: rgb{t['accent']};
        }}
        QStackedWidget {{ background: transparent; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {_rgba(t['muted'], 105)}; border-radius: 3px; min-height: 28px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """



class PetPanel(StaticPanelWindow):
    """“桌宠”专属控制台与互动界面（借鉴 GitHub DyberPet / VPet 的伴生桌宠面板设计）。

    支持在不开启视频首页的情况下独立管理桌宠：
    - 实时查看桌宠当前姿态立绘、屏幕坐标、自由走动/位置锁定状态与最近气泡台词
    - 一键点播 6 种高频伴生动作（递杯热饮、陪听会儿歌、出去溜达、伸懒腰、坐下歇会儿、去眯一会儿）
    - 一键显示/隐藏桌宠、锁定位置防遮挡、唤回屏幕中央、或直接呼出独立轻量聊天小窗
    """

    POSE_LABELS = {
        "idle": "安静待着",
        "music": "戴耳机听歌",
        "drink": "捧着热饮",
        "sleep": "闭眼熟睡中",
        "tired": "有点犯困",
        "sit": "安静坐着",
        "stretch": "伸懒腰活动",
        "happy": "心情不错",
        "world_return": "刚从小世界回来",
    }

    def __init__(self, chen=None, parent=None, *, desktop_pet=None, on_chat=None):
        self.desktop_pet = desktop_pet
        self.on_chat = on_chat
        self._last_bubble_line = "嗯，我在桌面上陪着你。"
        super().__init__(
            chen,
            panel_key="pet",
            title="桌宠 · 桌面伴生精灵",
            subtitle="不打开房间视频时，他也会以轻量透明桌宠陪在你屏幕边上；双击他的身体可直接打开独立聊天小窗。",
            parent=parent,
        )
        self.setMinimumSize(760, 540)
        self.resize(880, 620)
        self._build_pet_view()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def _resolve_pet(self):
        if self.desktop_pet is not None:
            return self.desktop_pet
        runtime = getattr(self.chen, "runtime", None) if self.chen else None
        clients = getattr(runtime, "_clients", {}) if runtime else {}
        if isinstance(clients, dict) and "desktop_pet" in clients:
            return clients["desktop_pet"]
        return None

    def _build_pet_view(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        split = QHBoxLayout()
        split.setSpacing(16)
        split.addWidget(self._build_left_card(), 38)
        split.addWidget(self._build_right_area(), 62)
        self.content.addLayout(split, 1)

    def _build_left_card(self):
        card = QFrame()
        card.setObjectName("petHeroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)

        self.vis_badge = QLabel()
        self.vis_badge.setObjectName("petBadge")
        layout.addWidget(self.vis_badge, 0, Qt.AlignmentFlag.AlignLeft)

        self.sprite_box = QLabel()
        self.sprite_box.setObjectName("petSpriteBox")
        self.sprite_box.setMinimumHeight(180)
        self.sprite_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sprite_box, 1)

        self.pose_title = QLabel()
        self.pose_title.setObjectName("petPoseTitle")
        self.coord_meta = QLabel()
        self.coord_meta.setObjectName("petMuted")
        self.coord_meta.setWordWrap(True)
        layout.addWidget(self.pose_title)
        layout.addWidget(self.coord_meta)

        quote_card = QFrame()
        quote_card.setObjectName("momentCard")
        q_layout = QVBoxLayout(quote_card)
        q_layout.setContentsMargins(12, 9, 12, 9)
        self.bubble_preview = QLabel()
        self.bubble_preview.setObjectName("petBubbleText")
        self.bubble_preview.setWordWrap(True)
        q_layout.addWidget(self.bubble_preview)
        layout.addWidget(quote_card)

        layout.addSpacing(4)
        self.toggle_btn = QPushButton("显示 / 隐藏桌宠")
        self.toggle_btn.setObjectName("petPrimaryBtn")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_pet_visibility)
        layout.addWidget(self.toggle_btn)

        row_btns = QHBoxLayout()
        row_btns.setSpacing(8)
        self.lock_btn = QPushButton("锁定位置")
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.clicked.connect(self.toggle_pet_lock)
        self.reset_btn = QPushButton("唤回默认位置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self.reset_pet_pos)
        row_btns.addWidget(self.lock_btn, 1)
        row_btns.addWidget(self.reset_btn, 1)
        layout.addLayout(row_btns)
        return card

    def _build_right_area(self):
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        action_card = QFrame()
        action_card.setObjectName("glass")
        ac_layout = QVBoxLayout(action_card)
        ac_layout.setContentsMargins(22, 18, 22, 18)
        ac_layout.setSpacing(10)
        ac_title = QLabel("动作点播与轻互动台")
        ac_title.setObjectName("petSectionTitle")
        ac_sub = QLabel("点击下方卡片即可让桌面上的伴生体切换姿态并冒出对应气泡，互动会同步计入你们的日常相处记录。")
        ac_sub.setObjectName("petMuted")
        ac_sub.setWordWrap(True)
        ac_layout.addWidget(ac_title)
        ac_layout.addWidget(ac_sub)

        grid = QGridLayout()
        grid.setSpacing(10)
        actions = [
            ("drink", "☕ 递杯热饮", "捧杯喝口热的，提醒你也歇会儿", "热乎的，你也去倒一杯吧。"),
            ("music", "🎵 陪听会儿歌", "戴上耳机进入安静听歌律动", "这首还挺好听的，陪你听会儿。"),
            ("walk", "🐾 出去溜达两步", "在屏幕工作区内自然走动", "坐太久了，出去溜达两步。"),
            ("stretch", "🙆 伸个懒腰", "舒展肩膀活动一下筋骨", "……呼，活动一下肩膀。"),
            ("sit", "🪑 安静坐下", "乖乖坐在屏幕角落陪你写字", "就在这儿坐着陪你，你忙你的。"),
            ("sleep", "💤 去眯一会儿", "闭眼进入安静打盹呼吸状态", "困了，先眯一会儿，有事叫我。"),
        ]
        for idx, (act_key, act_title, act_desc, line) in enumerate(actions):
            btn = QPushButton(f"{act_title}\n{act_desc}")
            btn.setObjectName("petActionTile")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(68)
            btn.clicked.connect(
                lambda _checked=False, k=act_key, s=line: self.trigger_pet_action(k, s)
            )
            grid.addWidget(btn, idx // 2, idx % 2)
        ac_layout.addLayout(grid)
        layout.addWidget(action_card, 3)

        link_card = QFrame()
        link_card.setObjectName("glass")
        lk_layout = QVBoxLayout(link_card)
        lk_layout.setContentsMargins(22, 16, 22, 16)
        lk_layout.setSpacing(8)
        lk_title = QLabel("轻量化伴生模式说明")
        lk_title.setObjectName("petSectionTitle")
        lk_desc = QLabel(
            "· 平时嫌视频首页占地方时，直接关掉首页窗口即可（后台自动暂停视频解码，0 显卡占用）。\n"
            "· 只留桌面上的伴生体桌宠 + 独立轻量聊天窗：双击桌宠身体随时弹出聊天框，右键桌宠也能快速切换动作。\n"
            "· 当伴生体进入 Godot 小世界散步时，桌宠会自动隐身避免出现两个分身；回到桌面后自动现身。"
        )
        lk_desc.setObjectName("petMuted")
        lk_desc.setWordWrap(True)
        lk_layout.addWidget(lk_title)
        lk_layout.addWidget(lk_desc)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        poke_btn = QPushButton("戳一下他（冒气泡）")
        poke_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        poke_btn.clicked.connect(self.poke_pet)
        bottom_row.addWidget(poke_btn)

        if callable(self.on_chat):
            chat_btn = QPushButton("打开独立聊天小窗")
            chat_btn.setObjectName("petPrimaryBtn")
            chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            chat_btn.clicked.connect(self.on_chat)
            bottom_row.addWidget(chat_btn)
        bottom_row.addStretch()
        lk_layout.addLayout(bottom_row)
        layout.addWidget(link_card, 2)
        return holder

    def _stylesheet(self):
        t = self._theme
        border_c = t.get("border", t["muted"])
        return super()._stylesheet() + f"""
        QFrame#petHeroCard {{
            background: {_rgba(t['tint'], 212)};
            border: 1px solid {_rgba(border_c, 135)};
            border-radius: 18px;
        }}
        QLabel#petBadge {{
            background: {_rgba(t['accent'], 48)};
            color: rgb{t['accent']};
            border: 1px solid {_rgba(t['accent'], 140)};
            border-radius: 10px;
            padding: 3px 10px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QLabel#petSpriteBox {{
            background: {_rgba(t['tint'], 145)};
            border: 1px solid {_rgba(border_c, 95)};
            border-radius: 16px;
            color: rgb{t['muted']};
            font-size: 10pt;
        }}
        QLabel#petPoseTitle {{
            color: rgb{t['text']};
            font-size: 15pt;
            font-weight: 600;
        }}
        QLabel#petSectionTitle {{
            color: rgb{t['text']};
            font-size: 11.5pt;
            font-weight: 600;
        }}
        QLabel#petBubbleText {{
            color: rgb{t['text']};
            font-size: 9.8pt;
        }}
        QLabel#petMuted {{
            color: rgb{t['muted']};
            font-size: 9.2pt;
        }}
        QPushButton#petActionTile {{
            background: {_rgba(t['tint'], 175)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(border_c, 115)};
            border-left: 3px solid rgb{t['accent']};
            border-radius: 13px;
            padding: 10px 14px;
            text-align: left;
            font-size: 9.5pt;
        }}
        QPushButton#petActionTile:hover {{
            background: {_rgba(t['accent'], 52)};
            border-color: rgb{t['accent']};
        }}
        QPushButton#petPrimaryBtn {{
            background: rgb{t['accent']};
            color: rgb(32, 28, 22);
            border: none;
            border-radius: 11px;
            padding: 9px 16px;
            font-weight: 600;
        }}
        QPushButton#petPrimaryBtn:hover {{
            background: rgb{t['text']};
        }}
        """

    def toggle_pet_visibility(self):
        pet_mgr = self._resolve_pet()
        if pet_mgr is not None:
            pet_mgr.toggle()
        self.refresh()

    def toggle_pet_lock(self):
        pet_mgr = self._resolve_pet()
        raw_pet = getattr(pet_mgr, "pet", None) if pet_mgr else None
        if raw_pet is not None and hasattr(raw_pet, "_toggle_lock"):
            raw_pet._toggle_lock()
        self.refresh()

    def reset_pet_pos(self):
        pet_mgr = self._resolve_pet()
        if pet_mgr is not None:
            pet_mgr.show()
            pet_mgr.reset_position()
            self._last_bubble_line = "回到默认位置了。"
            pet_mgr.say(self._last_bubble_line, 2500)
        self.refresh()

    def poke_pet(self):
        pet_mgr = self._resolve_pet()
        if pet_mgr is not None:
            pet_mgr.show()
            self._last_bubble_line = "……嗯？我在呢，双击我就能直接聊。"
            pet_mgr.say(self._last_bubble_line, 2800)
            if hasattr(pet_mgr, "_interact"):
                pet_mgr._interact("idle")
        self.refresh()

    def trigger_pet_action(self, action_key: str, line: str):
        pet_mgr = self._resolve_pet()
        self._last_bubble_line = line
        if pet_mgr is not None:
            pet_mgr.show()
            if action_key == "walk":
                pet_mgr.walk()
            elif action_key == "sleep":
                pet_mgr.sleep()
            elif action_key in ("drink", "stretch"):
                pet_mgr.play_action(action_key, 2600)
            else:
                pet_mgr.set_state(action_key)
            pet_mgr.say(line, 3200)
        self.refresh()

    def refresh(self):
        pet_mgr = self._resolve_pet()
        raw_pet = getattr(pet_mgr, "pet", None) if pet_mgr else None
        visible = bool(raw_pet and hasattr(raw_pet, "_visible") and raw_pet._visible())
        locked = bool(getattr(raw_pet, "_position_locked", False)) if raw_pet else False
        cur_state = str(
            getattr(raw_pet, "_current_state", None)
            or getattr(raw_pet, "_base_state", "idle")
            or "idle"
        )
        x = int(raw_pet.winfo_x()) if raw_pet and hasattr(raw_pet, "winfo_x") else 0
        y = int(raw_pet.winfo_y()) if raw_pet and hasattr(raw_pet, "winfo_y") else 0

        self.vis_badge.setText("● 桌宠正在桌面陪伴" if visible else "○ 桌宠当前已隐藏")
        self.toggle_btn.setText("隐藏桌宠" if visible else "在桌面显示桌宠")
        self.lock_btn.setText("解锁自由走动" if locked else "锁定当前位置")

        pose_cn = self.POSE_LABELS.get(cur_state, f"姿态：{cur_state}")
        self.pose_title.setText(pose_cn)
        lock_str = "位置已锁定（不乱走）" if locked else "允许在屏幕边缘散步"
        self.coord_meta.setText(f"桌面坐标：({x}, {y})  ·  {lock_str}")
        self.bubble_preview.setText(f"最近气泡：「{self._last_bubble_line}」")

        # 尝试加载对应状态的桌宠立绘预览
        sprite_path = ROOT / "assets" / "pet" / f"{cur_state}.png"
        if not sprite_path.exists():
            sprite_path = ROOT / "assets" / "pet" / "idle.png"
        if sprite_path.exists():
            img = QImage(str(sprite_path))
            if not img.isNull():
                scaled = img.scaled(
                    136,
                    156,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                from PySide6.QtGui import QPixmap
                self.sprite_box.setPixmap(QPixmap.fromImage(scaled))
                return
        self.sprite_box.setText(f"[ 伴生体桌宠立绘 · {pose_cn} ]")



class CompanionStudioPanel(StaticPanelWindow):
    """「伴生体工坊」开源外部用户专属创建与素材上传中心（零伴生体预设、支持直接上传自己的图与视频）。

    外部 GitHub 用户在此界面中：
    1. 从零填写属于自己的伴生体姓名、代词（TA/他/用户）、称呼与核心性格（绝不提供或泄露“伴生体”人设）；
    2. 直接在界面内点击「+ 上传视频/图片」上传自己的房间循环视频（.mp4）或静态背景（.png/.jpg）；
    3. 直接在界面内点击「+ 上传立绘」上传自己的桌宠各状态透明 PNG 立绘（自动计算透明包围盒并对齐脚底基准线）。
    """

    def __init__(self, chen=None, parent=None, *, on_applied=None):
        self.on_applied = on_applied
        self._profile = load_companion_profile()
        self._scene_thumb_labels: dict[str, QLabel] = {}
        self._scene_status_labels: dict[str, QLabel] = {}
        self._pet_thumb_labels: dict[str, QLabel] = {}
        super().__init__(
            chen,
            panel_key="studio",
            title="伴生体工坊 · 创建与上传你的专属本地伴生体",
            subtitle="在这里填写独属于你的伴生体人设，并直接上传你自己的房间画面与桌宠立绘图片。",
            parent=parent,
        )
        self.setMinimumSize(860, 600)
        self.resize(980, 680)
        self._build_studio_view()
        self._load_into_fields(self._profile)
        self.refresh_asset_previews()

    def _build_studio_view(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 顶部通用性格框架栏（无任何伴生体设定）
        preset_bar = QFrame()
        preset_bar.setObjectName("glass")
        pb_layout = QHBoxLayout(preset_bar)
        pb_layout.setContentsMargins(18, 10, 18, 10)
        pb_layout.setSpacing(8)
        pb_label = QLabel("性格框架模板：")
        pb_label.setObjectName("studioSectionTitle")
        pb_layout.addWidget(pb_label)
        self._preset_btns: dict[str, QPushButton] = {}
        for pid, pdata in COMPANION_PRESETS.items():
            btn = QPushButton(pdata["preset_name"])
            btn.setObjectName("studioPresetBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=pid: self.apply_preset_template(k))
            pb_layout.addWidget(btn)
            self._preset_btns[pid] = btn
        pb_layout.addStretch()

        btn_import = QPushButton("导入人设包 (.json)")
        btn_import.setObjectName("studioMiniBtn")
        btn_import.clicked.connect(self.import_preset)
        btn_export = QPushButton("导出人设包 (.json)")
        btn_export.setObjectName("studioMiniBtn")
        btn_export.clicked.connect(self.export_preset)
        pb_layout.addWidget(btn_import)
        pb_layout.addWidget(btn_export)
        self.content.addWidget(preset_bar)

        split = QHBoxLayout()
        split.setSpacing(14)
        split.addWidget(self._build_identity_column(), 46)
        split.addWidget(self._build_upload_and_brain_column(), 54)
        self.content.addLayout(split, 1)

    def _build_identity_column(self):
        card = QFrame()
        card.setObjectName("glass")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)

        title = QLabel("1. 编写你的专属伴生体人设")
        title.setObjectName("studioSectionTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        form = QVBoxLayout(holder)
        form.setContentsMargins(0, 2, 6, 4)
        form.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("输入伴生体名字（必填）")
        self.pronoun_edit = QLineEdit()
        self.pronoun_edit.setPlaceholderText("TA / 他 / 用户")
        self.pronoun_edit.setMaximumWidth(86)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("TA 对你的称呼（必填）")
        row1.addLayout(self._labeled_field("伴生体名字", self.name_edit), 2)
        row1.addLayout(self._labeled_field("代词", self.pronoun_edit), 1)
        row1.addLayout(self._labeled_field("对你的称呼", self.user_edit), 2)
        form.addLayout(row1)

        self.stage_edit = QLineEdit()
        self.stage_edit.setPlaceholderText("例如：初识的伴生体 / 熟悉的陪伴者 / 默契搭档")
        form.addLayout(self._labeled_field("初始关系定位", self.stage_edit))

        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("一句话描述 TA 的身份背景与居住在电脑里的状态")
        form.addLayout(self._labeled_field("身份与世界观描述", self.role_edit))

        traits_label = QLabel("核心性格、说话语气与相处禁忌（将直接生成 TA 的专属意识角色卡）")
        traits_label.setObjectName("studioFieldLabel")
        form.addWidget(traits_label)
        self.traits_edit = QPlainTextEdit()
        self.traits_edit.setMinimumHeight(105)
        self.traits_edit.setPlaceholderText(
            "在这里写下独属于你的伴生体的说话方式：\n"
            "例如：句式长短、偏好的语气、性格特点、以及绝对不要出现的雷区话术…"
        )
        form.addWidget(self.traits_edit, 1)

        self.boundary_check = QCheckBox("启用本地数字伴生体真实边界（不编造人类童年，不声称看见未发送内容）")
        self.boundary_check.setChecked(True)
        form.addWidget(self.boundary_check)

        # 模型连接配置折叠在左栏底部
        llm_head = QHBoxLayout()
        llm_lbl = QLabel("本地推理服务地址（LM Studio / Ollama）")
        llm_lbl.setObjectName("studioFieldLabel")
        llm_head.addWidget(llm_lbl)
        llm_head.addStretch()
        btn_lms = QPushButton("LM Studio :1234")
        btn_lms.setObjectName("studioMiniBtn")
        btn_lms.clicked.connect(lambda: self.local_url_edit.setText("http://127.0.0.1:1234/v1"))
        btn_oll = QPushButton("Ollama :11434")
        btn_oll.setObjectName("studioMiniBtn")
        btn_oll.clicked.connect(lambda: self.local_url_edit.setText("http://127.0.0.1:11434/v1"))
        llm_head.addWidget(btn_lms)
        llm_head.addWidget(btn_oll)
        form.addLayout(llm_head)

        row_llm = QHBoxLayout()
        row_llm.setSpacing(8)
        self.local_url_edit = QLineEdit()
        self.local_url_edit.setPlaceholderText("http://127.0.0.1:1234/v1")
        self.local_model_edit = QLineEdit()
        self.local_model_edit.setPlaceholderText("本地模型名（可留空）")
        row_llm.addWidget(self.local_url_edit, 3)
        row_llm.addWidget(self.local_model_edit, 2)
        form.addLayout(row_llm)

        self.ft_check = QCheckBox("启用微调极简 Prompt 模式（finetuned_mode）")
        form.addWidget(self.ft_check)

        # 隐藏保留云端地址字段以保持配置兼容
        self.cloud_url_edit = QLineEdit("https://api.deepseek.com/v1")
        self.cloud_model_edit = QLineEdit("deepseek-chat")

        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)
        return card

    def _build_upload_and_brain_column(self):
        card = QFrame()
        card.setObjectName("glass")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)

        title = QLabel("2. 上传你自己的房间画面与桌宠立绘")
        title.setObjectName("studioSectionTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        form = QVBoxLayout(holder)
        form.setContentsMargins(0, 2, 6, 4)
        form.setSpacing(8)

        scene_lbl = QLabel("A. 上传房间场景视频 (.mp4) 或背景壁纸 (.png / .jpg)")
        scene_lbl.setObjectName("studioFieldLabel")
        form.addWidget(scene_lbl)

        for slot_key, slot_title, slot_hint in SCENE_UPLOAD_SLOTS:
            row_frame = QFrame()
            row_frame.setObjectName("studioUploadSlot")
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(10, 6, 10, 6)
            r_layout.setSpacing(10)

            thumb = QLabel("无图")
            thumb.setObjectName("studioThumb")
            thumb.setFixedSize(54, 36)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._scene_thumb_labels[slot_key] = thumb
            r_layout.addWidget(thumb)

            info_col = QVBoxLayout()
            info_col.setSpacing(1)
            t_lbl = QLabel(slot_title)
            t_lbl.setObjectName("studioSlotTitle")
            s_lbl = QLabel(slot_hint)
            s_lbl.setObjectName("studioSlotSub")
            self._scene_status_labels[slot_key] = s_lbl
            info_col.addWidget(t_lbl)
            info_col.addWidget(s_lbl)
            r_layout.addLayout(info_col, 1)

            up_btn = QPushButton("+ 上传图/视频")
            up_btn.setObjectName("studioUploadBtn")
            up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            up_btn.clicked.connect(lambda _checked=False, k=slot_key: self.upload_scene_slot(k))
            r_layout.addWidget(up_btn)
            form.addWidget(row_frame)

        form.addSpacing(4)
        pet_lbl = QLabel("B. 上传桌宠各状态透明立绘 (.png / .webp，自动对齐透明包围盒)")
        pet_lbl.setObjectName("studioFieldLabel")
        form.addWidget(pet_lbl)

        pet_grid = QGridLayout()
        pet_grid.setSpacing(8)
        for idx, (p_key, p_title, _p_hint) in enumerate(PET_UPLOAD_SLOTS):
            p_card = QFrame()
            p_card.setObjectName("studioUploadSlot")
            p_lay = QHBoxLayout(p_card)
            p_lay.setContentsMargins(8, 6, 8, 6)
            p_lay.setSpacing(8)

            p_thumb = QLabel("立绘")
            p_thumb.setObjectName("studioThumb")
            p_thumb.setFixedSize(40, 44)
            p_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pet_thumb_labels[p_key] = p_thumb
            p_lay.addWidget(p_thumb)

            p_col = QVBoxLayout()
            p_col.setSpacing(3)
            pt_lbl = QLabel(p_title)
            pt_lbl.setObjectName("studioSlotTitle")
            p_btn = QPushButton("+ 上传立绘")
            p_btn.setObjectName("studioUploadBtn")
            p_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            p_btn.clicked.connect(lambda _checked=False, k=p_key: self.upload_pet_slot(k))
            p_col.addWidget(pt_lbl)
            p_col.addWidget(p_btn)
            p_lay.addLayout(p_col, 1)

            pet_grid.addWidget(p_card, idx // 2, idx % 2)
        form.addLayout(pet_grid)

        self.status_notice = QLabel("填写属于你的伴生体名字并上传图片后，点击下方按钮立即生效。")
        self.status_notice.setObjectName("studioNotice")
        self.status_notice.setWordWrap(True)
        form.addWidget(self.status_notice)
        form.addStretch()

        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        save_btn = QPushButton("保存人设与素材 · 启动我的专属伴生体")
        save_btn.setObjectName("studioPrimaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self.save_and_apply)
        outer.addWidget(save_btn)
        return card

    @staticmethod
    def _labeled_field(label_text: str, widget: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        lbl = QLabel(label_text)
        lbl.setObjectName("studioFieldLabel")
        box.addWidget(lbl)
        box.addWidget(widget)
        return box

    def upload_scene_slot(self, slot_key: str):
        if slot_key == "window_bg":
            filt = "图片 (*.png *.jpg *.jpeg *.webp *.bmp)"
        else:
            filt = "视频或图片 (*.mp4 *.webm *.mov *.png *.jpg *.jpeg *.webp)"
        path, _ = QFileDialog.getOpenFileName(self, "上传你自己的房间画面或背景图", "", filt)
        if not path:
            return
        try:
            dest = install_custom_scene_asset(slot_key, path)
            self.status_notice.setText(f"✓ 已上传并绑定画面：{dest.name}")
            self.refresh_asset_previews()
            refresh_open_backgrounds()
        except Exception as exc:
            QMessageBox.warning(self, "上传失败", f"无法导入画面文件：{exc}")

    def upload_pet_slot(self, state_key: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"上传桌宠 {state_key} 状态透明立绘", "", "立绘图片 (*.png *.webp *.bmp)"
        )
        if not path:
            return
        try:
            dest = install_custom_pet_sprite(state_key, path)
            self.status_notice.setText(f"✓ 已上传桌宠立绘 {dest.name}（已自动计算透明包围盒）！")
            self.refresh_asset_previews()
        except Exception as exc:
            QMessageBox.warning(self, "上传失败", f"无法导入桌宠立绘：{exc}")

    def refresh_asset_previews(self):
        uploaded_scenes = get_uploaded_scene_map()
        from PySide6.QtGui import QPixmap
        for slot_key, _title, hint in SCENE_UPLOAD_SLOTS:
            f_path = uploaded_scenes.get(slot_key)
            s_lbl = self._scene_status_labels.get(slot_key)
            t_lbl = self._scene_thumb_labels.get(slot_key)
            if f_path and Path(f_path).exists():
                p_obj = Path(f_path)
                if s_lbl:
                    s_lbl.setText(f"✓ 已上传：{p_obj.name}")
                if t_lbl:
                    if p_obj.suffix.lower() in IMAGE_EXT:
                        img = QImage(str(p_obj))
                        if not img.isNull():
                            scaled = img.scaled(54, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                            t_lbl.setPixmap(QPixmap.fromImage(scaled))
                            continue
                    t_lbl.setText("视频")
            else:
                if s_lbl:
                    s_lbl.setText(f"未上传 · {hint}")
                if t_lbl:
                    t_lbl.setPixmap(QPixmap())
                    t_lbl.setText("待上传")

        for p_key, _ptitle, _phint in PET_UPLOAD_SLOTS:
            t_lbl = self._pet_thumb_labels.get(p_key)
            custom_p = ROOT / "workspace" / "custom_assets" / "pet" / f"{p_key}.png"
            if custom_p.exists() and t_lbl:
                img = QImage(str(custom_p))
                if not img.isNull():
                    scaled = img.scaled(40, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    t_lbl.setPixmap(QPixmap.fromImage(scaled))
                    continue
            if t_lbl:
                t_lbl.setPixmap(QPixmap())
                t_lbl.setText("待上传")

    def apply_preset_template(self, preset_id: str):
        preset = COMPANION_PRESETS.get(preset_id)
        if not preset:
            return
        merged = dict(self._profile)
        # 保留用户已经输入的姓名与称呼，仅套用性格框架
        cur_name = self.name_edit.text().strip()
        cur_user = self.user_edit.text().strip()
        merged.update(preset)
        if cur_name:
            merged["companion_name"] = cur_name
        if cur_user:
            merged["user_name"] = cur_user
        self._load_into_fields(merged)
        self.status_notice.setText(f"已套用「{preset['preset_name']}」框架，请填写你的伴生体名字并上传你自己的图片。")

    def _load_into_fields(self, data: dict):
        self._profile = dict(data)
        pid = str(data.get("preset_id") or "blank_custom")
        for k, btn in self._preset_btns.items():
            btn.setChecked(k == pid)
        raw_name = str(data.get("companion_name") or "")
        raw_user = str(data.get("user_name") or "")
        self.name_edit.setText(raw_name)
        self.pronoun_edit.setText(str(data.get("pronoun") or "TA"))
        self.user_edit.setText(raw_user)
        self.stage_edit.setText(str(data.get("relationship_stage") or "初识的伴生体"))
        self.role_edit.setText(str(data.get("age_and_role") or ""))
        self.traits_edit.setPlainText(str(data.get("personality_traits") or ""))
        self.boundary_check.setChecked(bool(data.get("strict_digital_boundary", True)))
        self.local_url_edit.setText(str(data.get("local_llm_url") or "http://127.0.0.1:1234/v1"))
        self.local_model_edit.setText(str(data.get("local_llm_model") or ""))
        self.ft_check.setChecked(bool(data.get("finetuned_mode", False)))

    def _collect_fields(self) -> dict:
        checked_pid = next((k for k, b in self._preset_btns.items() if b.isChecked()), "blank_custom")
        return {
            "preset_id": checked_pid,
            "companion_name": self.name_edit.text().strip() or "伴生体",
            "pronoun": self.pronoun_edit.text().strip() or "TA",
            "user_name": self.user_edit.text().strip() or "你",
            "relationship_stage": self.stage_edit.text().strip() or "初识的伴生体",
            "age_and_role": self.role_edit.text().strip(),
            "personality_traits": self.traits_edit.toPlainText().strip(),
            "strict_digital_boundary": self.boundary_check.isChecked(),
            "local_llm_url": self.local_url_edit.text().strip() or "http://127.0.0.1:1234/v1",
            "local_llm_model": self.local_model_edit.text().strip(),
            "finetuned_mode": self.ft_check.isChecked(),
            "cloud_api_url": self.cloud_url_edit.text().strip() or "https://api.deepseek.com/v1",
            "cloud_api_model": self.cloud_model_edit.text().strip() or "deepseek-chat",
        }

    def save_and_apply(self):
        profile = self._collect_fields()
        saved_path = save_and_apply_companion_profile(profile)
        self._profile = profile
        cname = profile["companion_name"]
        self.setWindowTitle(f"伴生体工坊 · {cname}")
        self.status_notice.setText(
            f"✓ 已保存并激活你的专属伴生体「{cname}」（人设卡与自定义图片已全局生效）！"
        )
        if callable(self.on_applied):
            try:
                self.on_applied(profile)
            except Exception:
                pass

    def export_preset(self):
        profile = self._collect_fields()
        default_name = f"companion_{profile['companion_name']}.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出伴生体预设包", default_name, "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_notice.setText(f"✓ 预设包已导出至：{path}")

    def import_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入伴生体预设包", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._load_into_fields(data)
                self.status_notice.setText(f"✓ 已导入预设「{data.get('companion_name', '未知')}」，请点击底部按钮保存应用。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"无法解析预设文件：{exc}")

    def _stylesheet(self):
        t = self._theme
        border_c = t.get("border", t["muted"])
        return super()._stylesheet() + f"""
        QLabel#studioSectionTitle {{
            color: rgb{t['text']};
            font-size: 11.5pt;
            font-weight: 600;
        }}
        QLabel#studioFieldLabel {{
            color: rgb{t['muted']};
            font-size: 9pt;
            font-weight: 600;
        }}
        QLabel#studioSlotTitle {{
            color: rgb{t['text']};
            font-size: 9.5pt;
            font-weight: 600;
        }}
        QLabel#studioSlotSub {{
            color: rgb{t['muted']};
            font-size: 8.4pt;
        }}
        QLabel#studioThumb {{
            background: rgba(14, 16, 15, 195);
            color: rgb{t['muted']};
            border: 1px dashed {_rgba(border_c, 130)};
            border-radius: 8px;
            font-size: 8pt;
        }}
        QFrame#studioUploadSlot {{
            background: {_rgba(t['tint'], 175)};
            border: 1px solid {_rgba(border_c, 110)};
            border-radius: 12px;
        }}
        QPushButton#studioUploadBtn {{
            background: {_rgba(t['accent'], 48)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(t['accent'], 155)};
            border-radius: 9px;
            padding: 4px 10px;
            font-size: 8.6pt;
            font-weight: 600;
        }}
        QPushButton#studioUploadBtn:hover {{
            background: rgb{t['accent']};
            color: rgb(32, 28, 22);
        }}
        QLabel#studioNotice {{
            color: rgb{t['accent']};
            font-size: 9.2pt;
            padding: 4px 0;
        }}
        QLineEdit {{
            background: {_rgba(t['tint'], 190)};
            color: rgb{t['text']};
            border: 1px solid {_rgba(border_c, 120)};
            border-radius: 10px;
            padding: 6px 10px;
            font-size: 9.5pt;
        }}
        QLineEdit:focus {{
            border-color: rgb{t['accent']};
        }}
        QPushButton#studioPresetBtn {{
            background: {_rgba(t['tint'], 160)};
            color: rgb{t['muted']};
            border: 1px solid {_rgba(border_c, 115)};
            border-radius: 10px;
            padding: 5px 11px;
            font-size: 9pt;
        }}
        QPushButton#studioPresetBtn:hover {{
            color: rgb{t['text']};
            border-color: rgb{t['accent']};
        }}
        QPushButton#studioPresetBtn:checked {{
            background: {_rgba(t['accent'], 55)};
            color: rgb{t['text']};
            border: 1px solid rgb{t['accent']};
            font-weight: 600;
        }}
        QPushButton#studioMiniBtn {{
            background: {_rgba(t['tint'], 150)};
            color: rgb{t['muted']};
            border: 1px solid {_rgba(border_c, 100)};
            border-radius: 8px;
            padding: 3px 9px;
            font-size: 8.5pt;
        }}
        QPushButton#studioMiniBtn:hover {{
            color: rgb{t['accent']};
            border-color: rgb{t['accent']};
        }}
        QPushButton#studioPrimaryBtn {{
            background: rgb{t['accent']};
            color: rgb(32, 28, 22);
            border: none;
            border-radius: 12px;
            padding: 10px 18px;
            font-size: 10pt;
            font-weight: 600;
        }}
        QPushButton#studioPrimaryBtn:hover {{
            background: rgb{t['text']};
        }}
        """
