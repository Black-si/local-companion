# -*- coding: utf-8 -*-
"""伴生体的独立轻量伴侣聊天窗口（无需开启厚重的视频首页即可随时单开聊天，并自带快捷面板入口）。

首页的“找他”、桌宠双击、或托盘菜单都会唤起这个独立轻量窗口。
窗口顶栏集成「我们 · 空间 · 桌宠 · 修理台 · 房间」轻量胶入口，把视频首页关到托盘（0 视频开销）
也能只留这个小窗一边写作业/写代码一边和他聊。
"""
from __future__ import annotations

import itertools
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

try:
    from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import (
        QColor,
        QFont,
        QGuiApplication,
        QImage,
        QKeySequence,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
        QShortcut,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMenu,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )
    try:
        from .pyside_panels import refresh_open_backgrounds
    except ImportError:  # pragma: no cover - direct file execution
        from chen.interface.pyside_panels import refresh_open_backgrounds
    PYSIDE6_ERROR = None
except ImportError as exc:  # pragma: no cover - optional dependency
    PYSIDE6_ERROR = exc
    QApplication = QFileDialog = QFrame = QHBoxLayout = QLabel = QMenu = object
    QPlainTextEdit = QPushButton = QScrollArea = QVBoxLayout = QWidget = object
    QEvent = QRect = QRectF = Qt = QTimer = QColor = QFont = QGuiApplication = QImage = QKeySequence = QPainter = QPainterPath = QPen = QPixmap = QShortcut = object
    Signal = lambda *a, **kw: None

try:
    from .ui_tokens import get_period_theme
    from .companion_profile import companion_name
except ImportError:  # pragma: no cover
    from chen.interface.ui_tokens import get_period_theme
    from chen.interface.companion_profile import companion_name


ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "workspace" / "living_home_settings.json"
DEFAULT_BG = ROOT / "assets" / "room_night.png"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _read_settings() -> dict:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
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


def _rgb(color) -> str:
    r, g, b = (int(max(0, min(255, value))) for value in color)
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(color, alpha: int) -> str:
    r, g, b = (int(max(0, min(255, value))) for value in color)
    return f"rgba({r},{g},{b},{alpha})"


def _theme_from_image(image: QImage, hour: Optional[int] = None) -> dict:
    pt = get_period_theme(hour=hour)
    if image is None or getattr(image, "isNull", lambda: True)():
        return {
            "tint": pt["tint"],
            "text": pt["text"],
            "muted": pt["muted"],
            "border": pt["border"],
            "accent": pt["accent"],
            "on_accent": pt["on_accent"],
            "period_label": pt["label"],
        }
    small = image.scaled(48, 32, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pixels = [QColor(small.pixel(x, y)) for y in range(small.height()) for x in range(small.width())]
    mean = tuple(
        sum(getattr(pixel, channel)() for pixel in pixels) / len(pixels)
        for channel in ("red", "green", "blue")
    )
    light = (0.299 * mean[0] + 0.587 * mean[1] + 0.114 * mean[2]) > 182
    base_tint = pt["tint"]
    tint = tuple(
        int(mean[i] * 0.22 + base_tint[i] * 0.50 + (245 if light else 14) * 0.28)
        for i in range(3)
    )
    text = (38, 34, 30) if light else pt["text"]
    muted = tuple(int(text[i] * 0.62 + tint[i] * 0.38) for i in range(3))
    border = tuple(int(pt["border"][i] * 0.65 + text[i] * 0.35) for i in range(3))
    accent = pt["accent"] if not light else (95, 126, 92)
    return {
        "tint": tint,
        "text": text,
        "muted": muted,
        "border": border,
        "accent": accent,
        "on_accent": pt["on_accent"] if not light else (250, 247, 240),
        "period_label": pt["label"],
    }


def _blur_qimage(image: QImage, downscale: int = 8) -> QImage:
    """快速多级平滑缩放实现柔和景深毛玻璃底图。"""
    if image is None or image.isNull():
        return QImage()
    w = max(16, image.width() // downscale)
    h = max(16, image.height() // downscale)
    small = image.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(
        image.width(),
        image.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class StubBackend:
    """无 Chen 实例时用于视觉验证的明确占位后端。"""

    lines = itertools.cycle(("（本地演示回复：聊天后端尚未接入）",))

    def respond(self, text: str, alone: bool):
        return [(450, "say", next(self.lines))]


class Bubble(QFrame):
    """非对称圆角伴侣气泡：去除了生硬的 2px 调试框，支持时间悬停与细腻呼吸层次。"""

    def __init__(self, who: str, text: str, timestamp: Optional[datetime] = None):
        super().__init__()
        self.who = who
        self.setObjectName("bubbleMe" if who == "me" else "bubbleHim")
        self.text = text
        self.timestamp = timestamp or datetime.now()
        self.setToolTip(self.timestamp.strftime("%H:%M:%S"))

        self.label = QLabel(text)
        self.label.setObjectName("bubbleTextMe" if who == "me" else "bubbleTextHim")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 9)
        layout.setSpacing(3)
        layout.addWidget(self.label)

    def relayout(self, available: int) -> None:
        max_width = max(130, int(available * 0.82)) - 28
        rect = self.label.fontMetrics().boundingRect(
            QRect(0, 0, max_width, 100000),
            Qt.TextFlag.TextWordWrap.value,
            self.text,
        )
        self.label.setFixedSize(min(max_width, rect.width() + 6), rect.height() + 4)


class InputBox(QPlainTextEdit):
    submitted = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("input")
        self.setPlaceholderText(f"跟{companion_name()}说点什么…（Enter 发送，Shift+Enter 换行）")
        self.setFixedHeight(48)
        self.textChanged.connect(self.fit_height)

    def fit_height(self) -> None:
        lines = max(1, int(self.document().size().height()))
        height = min(max(lines * self.fontMetrics().lineSpacing() + 26, 48), 126)
        self.setFixedHeight(height)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            text = self.toPlainText().strip()
            if text:
                self.submitted.emit(text)
                self.clear()
            return
        super().keyPressEvent(event)


class ChatWindow(QWidget):
    """独立轻量伴侣聊天小窗（自带顶部快捷导航栏，无需开启厚重的视频首页即可独立使用）。"""

    reply_ready = Signal(str, object)

    def __init__(
        self,
        chen=None,
        get_alone: Optional[Callable[[], bool]] = None,
        set_alone: Optional[Callable[[bool], None]] = None,
        get_presence: Optional[Callable[[], dict]] = None,
        parent: QWidget | None = None,
        *,
        embedded_mode: bool = False,
        on_toggle_dock: Optional[Callable[[], None]] = None,
        on_close_drawer: Optional[Callable[[], None]] = None,
        nav_callbacks: Optional[dict[str, Callable[[], None]]] = None,
    ):
        super().__init__(parent)
        self.chen = chen
        self.get_alone = get_alone or (lambda: self._alone)
        self.set_alone_callback = set_alone
        self.get_presence = get_presence
        self.embedded_mode = bool(embedded_mode)
        self.on_toggle_dock = on_toggle_dock
        self.on_close_drawer = on_close_drawer
        self.nav_callbacks = dict(nav_callbacks or {})
        self._alone = False
        self._pinned = False
        self._waiting = False
        self._typing_step = 0
        self.backend = StubBackend()
        self.bubbles: list[Bubble] = []
        self._seen_log_ids: set[int] = set()
        self.last_ts: Optional[datetime] = None
        self.pending: Optional[QLabel] = None
        self.empty: Optional[QLabel] = None
        self.background = QImage()
        self.blurred_background = QImage()

        self.setWindowTitle(f"{companion_name()} · 聊天")
        self.setMinimumSize(340, 460)
        self.build_ui()
        self.reload_background()
        if not self.embedded_mode:
            self.restore_geometry()
        QShortcut(QKeySequence("F2"), self).activated.connect(self.toggle_alone)
        self.reply_ready.connect(self._finish_reply)

        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(360)
        self._typing_timer.timeout.connect(self._animate_typing)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(2000)
        self.restore_history()
        self.sync()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        bar = QFrame()
        bar.setObjectName("glassHeader")
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 12, 10)
        bar_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.presence_dot = QLabel("●")
        self.presence_dot.setObjectName("presenceDot")
        self.status = QLabel()
        self.status.setObjectName("title")
        top_row.addWidget(self.presence_dot)
        top_row.addWidget(self.status)
        top_row.addStretch()

        self.background_button = QPushButton("背景")
        self.background_button.setObjectName("pin")
        background_menu = QMenu(self.background_button)
        background_menu.addAction("换所有非首页窗口背景…", lambda: self.choose_background("default"))
        background_menu.addAction("只换聊天窗口背景…", lambda: self.choose_background("chat"))
        background_menu.addAction("恢复聊天窗口背景", self.reset_background)
        self.background_button.setMenu(background_menu)
        top_row.addWidget(self.background_button)

        self.pin = QPushButton("置顶")
        self.pin.setObjectName("pin")
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self.set_pinned)
        top_row.addWidget(self.pin)
        bar_layout.addLayout(top_row)

        self.sub_status = QLabel()
        self.sub_status.setObjectName("subStatus")
        self.sub_status.setWordWrap(True)
        bar_layout.addWidget(self.sub_status)

        # 独立轻量快捷栏：关掉视频首页时，也能从聊天窗一键打开「我们 / 今天 / 空间 / 桌宠 / 修理台 / 房间」
        if self.nav_callbacks:
            nav_row = QHBoxLayout()
            nav_row.setSpacing(6)
            for key, label in (
                ("us", "我们"),
                ("days", "今天"),
                ("places", "空间"),
                ("pet", "桌宠"),
                ("studio", "伴生体"),
                ("workshop", "修理台"),
                ("home", "房间视频"),
            ):
                cb = self.nav_callbacks.get(key)
                if callable(cb):
                    btn = QPushButton(label)
                    btn.setObjectName("navPill")
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.clicked.connect(lambda _checked=False, fn=cb: fn())
                    nav_row.addWidget(btn)
            nav_row.addStretch()
            bar_layout.addLayout(nav_row)

        root.addWidget(bar)

        self.banner = QLabel("他现在想一个人安静待会儿，敲门他也会听见。")
        self.banner.setObjectName("banner")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.hide()
        root.addWidget(self.banner)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.messages = QVBoxLayout(holder)
        self.messages.setContentsMargins(6, 8, 6, 8)
        self.messages.setSpacing(10)
        self.messages.addStretch()
        self.empty = QLabel("房间很安静，随时可以找他说话。")
        self.empty.setObjectName("stamp")
        self.messages.addWidget(self.empty, 0, Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = InputBox()
        self.input.submitted.connect(self.send)
        input_row.addWidget(self.input, 1)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendBtn")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setFixedHeight(46)
        self.send_button.clicked.connect(self._submit_from_button)
        input_row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        root.addLayout(input_row)

    def _submit_from_button(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.send(text)

    def reload_background(self) -> None:
        settings = _read_settings()
        selected = (settings.get("window_bg") or {}).get("chat") or (settings.get("window_bg") or {}).get("default")
        path = Path(str(selected)) if selected and Path(str(selected)).exists() else DEFAULT_BG
        self.background = QImage(str(path))
        self.blurred_background = _blur_qimage(self.background, downscale=7)
        self.theme = _theme_from_image(self.background)
        self.setStyleSheet(self.stylesheet())
        self.update()

    def stylesheet(self) -> str:
        t = self.theme
        return f"""
        QLabel {{ background: transparent; color: {_rgb(t['text'])}; }}
        QLabel#presenceDot {{ color: {_rgb(t['accent'])}; font-size: 11pt; font-weight: bold; }}
        QLabel#title {{ font-size: 11pt; font-weight: 600; color: {_rgb(t['text'])}; }}
        QLabel#subStatus {{ color: {_rgb(t['muted'])}; font-size: 8.8pt; }}
        QLabel#stamp {{
            color: {_rgb(t['muted'])};
            background: {_rgba(t['tint'], 135)};
            border-radius: 9px;
            padding: 2px 10px;
            font-size: 8.5pt;
        }}
        QLabel#banner {{
            color: {_rgb(t['text'])};
            background: {_rgba(t['accent'], 42)};
            border: 1px solid {_rgba(t['accent'], 110)};
            border-radius: 11px;
            padding: 5px 10px;
            font-size: 8.8pt;
        }}
        QLabel#action {{
            color: {_rgb(t['accent'])};
            background: {_rgba(t['tint'], 165)};
            border: 1px solid {_rgba(t['border'], 95)};
            border-radius: 12px;
            padding: 5px 12px;
            font-size: 9pt;
        }}
        QFrame#glassHeader {{
            background: {_rgba(t['tint'], 212)};
            border: 1px solid {_rgba(t['border'], 140)};
            border-radius: 18px;
        }}
        QFrame#bubbleHim {{
            background: {_rgba(t['tint'], 222)};
            border: 1px solid {_rgba(t['border'], 135)};
            border-left: 3px solid {_rgba(t['accent'], 195)};
            border-top-left-radius: 6px;
            border-top-right-radius: 18px;
            border-bottom-left-radius: 18px;
            border-bottom-right-radius: 18px;
        }}
        QFrame#bubbleMe {{
            background: {_rgba(t['accent'], 68)};
            border: 1px solid {_rgba(t['accent'], 160)};
            border-top-left-radius: 18px;
            border-top-right-radius: 6px;
            border-bottom-left-radius: 18px;
            border-bottom-right-radius: 18px;
        }}
        QLabel#bubbleTextHim, QLabel#bubbleTextMe {{
            color: {_rgb(t['text'])};
            font-size: 10pt;
        }}
        QPushButton#pin, QPushButton#navPill {{
            background: {_rgba(t['tint'], 150)};
            border: 1px solid {_rgba(t['border'], 140)};
            border-radius: 10px;
            color: {_rgb(t['muted'])};
            padding: 3px 9px;
            font-size: 8.6pt;
        }}
        QPushButton#pin:hover, QPushButton#navPill:hover {{
            color: {_rgb(t['text'])};
            border: 1px solid {_rgb(t['accent'])};
            background: {_rgba(t['accent'], 50)};
        }}
        QPushButton#pin:checked {{
            color: {_rgb(t['on_accent'])};
            background: {_rgb(t['accent'])};
            border: 1px solid {_rgb(t['accent'])};
        }}
        QPushButton#sendBtn {{
            background: {_rgb(t['accent'])};
            color: {_rgb(t['on_accent'])};
            border: none;
            border-radius: 14px;
            padding: 0 14px;
            font-weight: 600;
            font-size: 9.5pt;
        }}
        QPushButton#sendBtn:hover {{
            background: {_rgb(t['text'])};
            color: {_rgb(t['on_accent'])};
        }}
        QPlainTextEdit#input {{
            background: {_rgba(t['tint'], 212)};
            border: 1px solid {_rgba(t['border'], 145)};
            border-radius: 16px;
            padding: 8px 12px;
            color: {_rgb(t['text'])};
            selection-background-color: {_rgb(t['accent'])};
            selection-color: {_rgb(t['on_accent'])};
        }}
        QPlainTextEdit#input:focus {{
            border: 1px solid {_rgb(t['accent'])};
        }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {_rgba(t['text'], 75)}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r, g, b = (int(value) for value in self.theme["tint"])
        image = self.blurred_background if not self.blurred_background.isNull() else self.background
        if image.isNull():
            painter.fillRect(self.rect(), QColor("#171916"))
        else:
            scale = max(self.width() / image.width(), self.height() / image.height())
            sw, sh = self.width() / scale, self.height() / scale
            source = QRectF((image.width() - sw) / 2, (image.height() - sh) / 2, sw, sh)
            painter.drawImage(QRectF(self.rect()), image, source)
            painter.fillRect(self.rect(), QColor(r, g, b, 108))
        painter.end()

    def contextMenuEvent(self, event):  # noqa: N802 - Qt API
        menu = QMenu(self)
        menu.addAction("换所有非首页窗口背景…", lambda: self.choose_background("default"))
        menu.addAction("只换聊天窗口背景…", lambda: self.choose_background("chat"))
        menu.addAction("恢复默认背景", self.reset_background)
        menu.exec(event.globalPos())

    def choose_background(self, name: str = "chat"):
        patterns = " ".join("*" + suffix for suffix in IMAGE_EXT)
        path, _ = QFileDialog.getOpenFileName(self, "选择聊天背景", "", f"图片 ({patterns})")
        if not path:
            return
        settings = _read_settings()
        settings.setdefault("window_bg", {})[name] = path
        _write_settings(settings)
        refresh_open_backgrounds()

    def reset_background(self):
        settings = _read_settings()
        settings.setdefault("window_bg", {}).pop("chat", None)
        _write_settings(settings)
        refresh_open_backgrounds()

    def sync(self) -> None:
        alone = bool(self.get_alone())
        state = self._presence()
        place = state.get("activity_place") or "房间"
        activity = state.get("activity") or "在电脑里陪着你"
        awake = state.get("awake", True)
        if awake is False:
            activity = "睡着了"
            self.presence_dot.setText("☾")
        elif alone:
            activity = "想一个人待着"
            self.presence_dot.setText("◌")
        else:
            self.presence_dot.setText("●")
        self.status.setText(f"{place}  |  {activity}")

        try:
            try:
                from .pyside_home import resolve_scene, SCENE_VISUAL_FACTS
            except ImportError:
                from chen.interface.pyside_home import resolve_scene, SCENE_VISUAL_FACTS
            skey, _, _ = resolve_scene(datetime.now().hour, state)
            vfacts = SCENE_VISUAL_FACTS.get(skey, ())
            self.sub_status.setText(" · ".join(vfacts) if vfacts else "房间很安静")
        except Exception:
            self.sub_status.setText("")

        self.banner.setVisible(alone)
        if not self._waiting:
            self.sync_external_messages()

    def _presence(self) -> dict:
        try:
            if self.get_presence:
                return self.get_presence() or {}
            if self.chen:
                return self.chen.get_presence_snapshot(persist=False) or {}
        except Exception:
            pass
        return {}

    def toggle_alone(self) -> None:
        current = bool(self.get_alone())
        if self.set_alone_callback:
            self.set_alone_callback(not current)
        else:
            self._alone = not current
        self.sync()

    def avail(self) -> int:
        return self.scroll.viewport().width() - 12

    def to_bottom(self) -> None:
        scrollbar = self.scroll.verticalScrollBar()
        QTimer.singleShot(30, lambda: scrollbar.setValue(scrollbar.maximum()))

    def clear_empty(self) -> None:
        if self.empty:
            self.messages.removeWidget(self.empty)
            self.empty.deleteLater()
            self.empty = None

    def stamp_if_needed(self, timestamp: Optional[datetime] = None) -> None:
        now = timestamp or datetime.now()
        if self.last_ts is None or now - self.last_ts > timedelta(minutes=30):
            stamp = QLabel(f"{now:%H:%M}")
            stamp.setObjectName("stamp")
            self.messages.addWidget(stamp, 0, Qt.AlignmentFlag.AlignCenter)
        self.last_ts = now

    def add_bubble(self, who: str, text: str, timestamp: Optional[datetime] = None) -> None:
        self.clear_empty()
        self.stamp_if_needed(timestamp)
        bubble = Bubble(who, text, timestamp=timestamp)
        self.messages.addWidget(
            bubble,
            0,
            Qt.AlignmentFlag.AlignRight if who == "me" else Qt.AlignmentFlag.AlignLeft,
        )
        self.bubbles.append(bubble)
        bubble.relayout(self.avail())
        self.to_bottom()

    def _animate_typing(self) -> None:
        if not self.pending:
            self._typing_timer.stop()
            return
        dots = ("·  ", "·· ", "···")[self._typing_step % 3]
        self._typing_step += 1
        self.pending.setText(f"{companion_name()}正在输入 {dots}")

    def clear_pending(self) -> None:
        self._typing_timer.stop()
        if self.pending:
            self.messages.removeWidget(self.pending)
            self.pending.deleteLater()
            self.pending = None

    def send(self, text: str) -> None:
        if self._waiting:
            return
        self.add_bubble("me", text)
        self._waiting = True
        self._typing_step = 0
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.pending = QLabel(f"{companion_name()}正在输入 ·  ")
        self.pending.setObjectName("action")
        self.messages.addWidget(self.pending, 0, Qt.AlignmentFlag.AlignLeft)
        self._typing_timer.start()
        self.to_bottom()
        alone = bool(self.get_alone())
        if self.chen is None:
            delay = 0
            for delay_ms, kind, reply in self.backend.respond(text, alone):
                delay += delay_ms
                QTimer.singleShot(delay, lambda k=kind, s=reply: self.reply_ready.emit(s, k))
            return
        threading.Thread(target=self._chat_worker, args=(text,), daemon=True).start()

    def _chat_worker(self, text: str) -> None:
        try:
            reply = self.chen.chat(text)
            self.reply_ready.emit(str(reply or ""), "say")
        except Exception as exc:
            self.reply_ready.emit(str(exc), "error")

    def _finish_reply(self, text: str, kind: object) -> None:
        self.clear_pending()
        if kind == "act":
            action = QLabel(text)
            action.setObjectName("action")
            self.messages.addWidget(action, 0, Qt.AlignmentFlag.AlignLeft)
            self.to_bottom()
            return
        history = getattr(self.chen, "chat_log", None) if self.chen else None
        if history:
            for item in history[-4:]:
                if isinstance(item, dict):
                    self._seen_log_ids.add(id(item))
        self._waiting = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        if kind == "error":
            self.add_bubble("him", f"暂时没能回应：{text}")
        elif text:
            self.add_bubble("him", text)
        self.input.setFocus()

    def _render_log_item(self, item: dict) -> None:
        self._seen_log_ids.add(id(item))
        text = str(item.get("text", "") or "").strip()
        if not text:
            return
        role = item.get("role")
        who = "me" if role == "he" else "him" if role == "chen" else None
        if not who:
            return
        stamp = None
        raw_ts = str(item.get("ts", "") or "")
        try:
            stamp = datetime.fromisoformat(raw_ts)
        except ValueError:
            pass
        self.add_bubble(who, text, stamp)

    def restore_history(self) -> None:
        history = getattr(self.chen, "chat_log", None) if self.chen else None
        if not history:
            return
        for item in history:
            if isinstance(item, dict):
                self._seen_log_ids.add(id(item))
        for item in history[-100:]:
            if isinstance(item, dict):
                self._render_log_item(item)

    def sync_external_messages(self) -> None:
        history = getattr(self.chen, "chat_log", None) if self.chen else None
        if not history:
            return
        for item in list(history[-20:]):
            if isinstance(item, dict) and id(item) not in self._seen_log_ids:
                self._render_log_item(item)

    def set_pinned(self, enabled: bool) -> None:
        self._pinned = bool(enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._pinned)
        self.show()
        if not enabled:
            self.setWindowOpacity(1.0)

    def event(self, event):  # noqa: N802 - Qt API
        if self._pinned:
            if event.type() == QEvent.Type.WindowDeactivate:
                self.setWindowOpacity(0.88)
            elif event.type() == QEvent.Type.WindowActivate:
                self.setWindowOpacity(1.0)
        return super().event(event)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        for bubble in self.bubbles:
            bubble.relayout(self.avail())

    def restore_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        geometry = (_read_settings().get("chat") or {}).get("geometry")
        if isinstance(geometry, list) and len(geometry) == 4 and geometry[2] >= 300 and geometry[3] >= 420:
            candidate = QRect(*[int(value) for value in geometry])
            if candidate.intersects(screen):
                self.setGeometry(candidate)
                return
        width, height = 420, min(690, int(screen.height() * 0.82))
        self.resize(width, height)
        self.move(screen.right() - width - 24, screen.top() + (screen.height() - height) // 2)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        geometry = self.geometry()
        settings = _read_settings()
        settings["chat"] = {"geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()]}
        _write_settings(settings)
        super().closeEvent(event)


def run_demo() -> int:
    if PYSIDE6_ERROR is not None:
        raise RuntimeError("PySide6 尚未安装。请运行：python -m pip install PySide6") from PYSIDE6_ERROR
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = ChatWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_demo())
