# -*- coding: utf-8 -*-
"""PySide6 生活首页（全时段 16 支无缝视频 + 实时毛玻璃模糊 + 五时段动态光影 + 房间内嵌聊天抽屉）。

这个模块负责首页呈现和入口。伴生体的聊天、关系、生活、小世界和后台
仍然由调用方传入，支持在房间内直接滑出右侧半透明毛玻璃聊天抽屉，或一键拆出为独立悬浮小窗。

PySide6 未安装时模块仍可被语法检查；真正创建窗口时会抛出可读的安装提示。
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Callable, Optional

try:  # PySide6 是可选的，避免现有 Tk UI 在依赖缺失时无法启动。
    from PySide6.QtCore import QRectF, QTimer, QUrl, Qt, Signal
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontDatabase,
        QImage,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QStackedLayout,
        QWidget,
    )
    PYSIDE6_ERROR = None
except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
    PYSIDE6_ERROR = exc
    QApplication = QMainWindow = QMessageBox = QPushButton = QStackedLayout = QWidget = object
    QRectF = QTimer = QUrl = Qt = QColor = QFont = QFontDatabase = QImage = QLinearGradient = QPainter = QPainterPath = QPen = QPixmap = object
    Signal = lambda *a, **kw: None
    QAudioOutput = QMediaPlayer = QVideoSink = object

try:
    from .ui_tokens import get_period_theme
    from .companion_profile import companion_name, find_companion_label
except ImportError:  # pragma: no cover
    from chen.interface.ui_tokens import get_period_theme
    from chen.interface.companion_profile import companion_name, find_companion_label


ROOT = Path(__file__).resolve().parents[3]
SCENES_DIR = ROOT / "assets" / "scenes"
DEFAULT_BG = ROOT / "assets" / "room_night.png"
SETTINGS_PATH = ROOT / "workspace" / "living_home_settings.json"
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".avi")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _read_json(path: Path, default):
    """读取首页所需的本地快照；失败时保持明确的空状态。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


class LocalStateProvider:
    """独立原型使用的只读数据源。

    正式入口传入 Chen 实例时，首页直接读取 Chen 的对象；独立运行 demo 时，
    这里只读 memory 快照，不启动模型、不写日志，也不凭空生成经历。
    """

    def __init__(self, root: Path = ROOT):
        self.memory = root / "memory"

    def get_presence_snapshot(self, persist=False):
        snapshot = _read_json(self.memory / "presence_snapshot.json", {})
        if snapshot:
            updated_at = snapshot.get("updated_at")
            try:
                observed = datetime.fromisoformat(str(updated_at))
                if (datetime.now() - observed).total_seconds() > 6 * 3600:
                    return {}
            except (TypeError, ValueError):
                return {}
            return snapshot
        world = _read_json(self.memory / "world_state.json", {})
        life = _read_json(self.memory / "life_state.json", {})
        return {
            "current_world": world.get("current_world", ""),
            "activity_place": world.get("activity_place", ""),
            "activity": world.get("activity") or life.get("activity", ""),
            "awake": life.get("awake"),
            "music": _read_json(self.memory / "music_state.json", {}),
        }

    def today_lines(self):
        path = self.memory / "life_logs" / f"{datetime.now():%Y-%m-%d}.jsonl"
        try:
            rows = []
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                item = json.loads(raw)
                if isinstance(item, dict):
                    rows.append(item)
            return rows
        except (OSError, ValueError, TypeError):
            return []

    def world_state(self):
        return _read_json(self.memory / "world_state.json", {})


def _load_ui_font() -> str:
    """让打包环境也能显示中文；Windows 可直接从系统字体目录加载。"""
    for path in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if path.exists():
            try:
                families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(path)))
                if families:
                    return families[0]
            except Exception:
                pass
    return "Microsoft YaHei UI"


def _default_background() -> QPixmap:
    """无素材时提供一个通用的抽象房间底色，不依赖作者的照片或视频。"""
    image = QImage(1600, 1000, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 0, image.height())
    gradient.setColorAt(0.0, QColor("#252a27"))
    gradient.setColorAt(0.58, QColor("#1b201d"))
    gradient.setColorAt(1.0, QColor("#111411"))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(205, 170, 112, 28))
    painter.drawEllipse(1170, 120, 220, 220)
    painter.setBrush(QColor(37, 43, 38, 230))
    painter.drawRoundedRect(180, 120, 460, 330, 24, 24)
    painter.setBrush(QColor(21, 25, 22, 210))
    painter.drawRoundedRect(230, 170, 360, 230, 16, 16)
    painter.setBrush(QColor(50, 55, 47, 210))
    painter.drawRoundedRect(0, 720, 1600, 280, 0, 0)
    painter.end()
    return QPixmap.fromImage(image)


def _load_background_pixmap(path: Path) -> QPixmap:
    """加载用户/项目背景，文件缺失或损坏时回退到通用底图。"""
    pixmap = QPixmap(str(path)) if path and path.exists() else QPixmap()
    return pixmap if not pixmap.isNull() else _default_background()


PERIODS = (
    ("day", "白天", "06:00 – 11:00"),
    ("noon", "中午", "11:00 – 14:00"),
    ("afternoon", "下午", "14:00 – 17:00"),
    ("dusk", "黄昏", "17:00 – 19:00"),
    ("night", "晚上", "19:00 – 次日 06:00"),
)

EMPTY_SCENES = {
    "day": "empty_morning.mp4",
    "noon": "empty_noon_afternoon.mp4",
    "afternoon": "empty_noon_afternoon.mp4",
    "dusk": "empty_dusk.mp4",
    "night": "empty_night.mp4",
}

# 全时段 16 支无缝循环视频映射（首尾平滑衔接 + 动作序列帧段锁定）：
# - alternate_2.mp4：白天坐在地毯圆桌前，宠物趴在身旁
# - eating_noon.mp4 / noon.mp4：中午在地毯圆桌前吃东西/喝茶，宠物在旁
# - afternoon.mp4：晴朗午后，前 3.5s 走到床边躺下拿手机，3.5s 后稳定躺在床上看手机
# - phone_dusk.mp4：黄昏夕阳，前 3.5s 走到床边躺下拿手机，3.5s 后稳定躺在床上看手机
# - day.mp4：夜晚星空+书桌暖黄台灯亮起，坐在地毯圆桌前，宠物在旁
# - computer_day.mp4 / computer_night.mp4：前 5.0s 走到右侧书桌坐下，5.0s 后稳定在电脑前敲键盘
# - phone_night.mp4：夜晚台灯下躺在床上看手机，屏幕微光映在脸上（3.5s 后稳定循环）
# - sleep_night.mp4 / sleep.mp4：深夜电脑息屏，闭眼躺在床上安静熟睡（0s 全片无缝呼吸循环）
HOME_SCENES = {
    "day": "alternate_2.mp4",
    "noon": "eating_noon.mp4",
    "afternoon": "afternoon.mp4",
    "dusk": "phone_dusk.mp4",
    "night": "day.mp4",
}
COMPUTER_SCENE = "computer_day.mp4"
COMPUTER_NIGHT_SCENE = "computer_night.mp4"
PHONE_DUSK_SCENE = "phone_dusk.mp4"
PHONE_NIGHT_SCENE = "phone_night.mp4"
SLEEP_NIGHT_SCENE = "sleep_night.mp4"
COMPUTER_LOOP_START_MS = 5000
BED_LOOP_START_MS = 3500
SEQUENCE_LOOP_STARTS_MS = {
    "computer_day": COMPUTER_LOOP_START_MS,
    "computer_night": COMPUTER_LOOP_START_MS,
    "phone_dusk": BED_LOOP_START_MS,
    "phone_night": BED_LOOP_START_MS,
    "home_dusk": BED_LOOP_START_MS,
    "home_afternoon": BED_LOOP_START_MS,
    "afternoon": BED_LOOP_START_MS,
    "rest_afternoon": BED_LOOP_START_MS,
    "rest_dusk": BED_LOOP_START_MS,
    "rest_night": BED_LOOP_START_MS,
}

# 每个视频对应的画面实况标签（供首页左下角状态栏、聊天顶栏与聊天 Prompt 同步感知，增强视频与对话连携）
SCENE_VISUAL_FACTS = {
    "home_day": ("坐在地毯圆桌前", "宠物趴在脚边睡"),
    "home_noon": ("在圆桌前吃午饭", "宠物趴在旁边"),
    "home_afternoon": ("躺在床上看手机", "午后阳光洒在床尾与地毯上"),
    "afternoon": ("躺在床上看手机", "午后阳光洒在床尾与地毯上"),
    "home_dusk": ("靠在床头看手机", "窗外是黄昏夕阳"),
    "phone_dusk": ("靠在床头看手机", "窗外是黄昏夕阳"),
    "phone_night": ("夜里躺在床上看手机", "手机屏幕微光映在脸上"),
    "home_night": ("台灯与电脑亮着", "和宠物待在地毯上"),
    "day": ("台灯与电脑亮着", "和宠物待在地毯上"),
    "computer_day": ("坐在右侧书桌电脑前", "宠物留在地毯上打盹"),
    "computer_night": ("夜里坐在书桌电脑前", "台灯与屏幕光映在侧脸"),
    "eating_noon": ("在圆桌前吃东西", "宠物在旁边陪着"),
    "noon": ("在圆桌前吃东西", "宠物在旁边陪着"),
    "sleep_night": ("夜深了，书桌上的电脑已经息屏", "房间只留一盏暖黄小夜灯"),
    "sleep": ("夜深了，书桌上的电脑已经息屏", "房间只留一盏暖黄小夜灯"),
    "away_day": ("房间空着，他去了小世界", "晨光洒在书桌上"),
    "away_noon": ("房间空着，他去了小世界", "窗帘随风轻轻晃动"),
    "away_afternoon": ("房间空着，他去了小世界", "午后阳光很安静"),
    "away_dusk": ("房间空着，他去了小世界", "夕阳照在空地毯上"),
    "away_night": ("房间空着，他去了小世界", "书桌台灯还替他亮着"),
}

ACTION_SCENE_TOKENS = (
    ("reading", ("看书", "读书", "阅读")),
    ("organizing", ("整理", "收拾")),
    ("music", ("听歌", "听音乐", "音乐")),
    ("eating", ("吃东西", "吃饭", "吃午饭", "吃晚饭", "吃早饭", "喝茶", "用餐", "进食")),
    ("phone", ("玩手机", "看手机", "刷手机", "用手机", "手机")),
    ("writing", ("写东西", "写字", "写作", "随笔")),
    ("rest", ("休息", "趴着", "躺", "眯一会儿")),
    ("window", ("窗边", "窗前")),
    ("cat", ("摸宠物", "逗宠物", "陪宠物")),
)


def period_key(hour: Optional[int] = None) -> str:
    """返回当前场景时段。可传 hour 供测试使用。"""
    h = datetime.now().hour if hour is None else int(hour)
    if 6 <= h < 11:
        return "day"
    if 11 <= h < 14:
        return "noon"
    if 14 <= h < 17:
        return "afternoon"
    if 17 <= h < 19:
        return "dusk"
    return "night"


def load_scene_settings(path: Path = SETTINGS_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError, TypeError):
        return {}


def _custom_scene(key: str, settings: Optional[dict] = None) -> Optional[Path]:
    settings = settings or {}
    custom = settings.get("scenes", {})
    if isinstance(custom, dict):
        selected = custom.get(key)
        if selected:
            candidate = Path(str(selected))
            if candidate.exists():
                return candidate
    return None


def _has_scene_asset(key: str, settings: Optional[dict] = None) -> bool:
    if _custom_scene(key, settings):
        return True
    return any((SCENES_DIR / f"{key}{suffix}").is_file() for suffix in VIDEO_EXT + IMAGE_EXT)


def scene_path(key: str, settings: Optional[dict] = None) -> Path:
    """Resolve a named scene key and preserve the static room fallback."""
    custom_path = _custom_scene(key, settings)
    if custom_path:
        return custom_path
    known = {
        **{f"away_{period}": file for period, file in EMPTY_SCENES.items()},
        **{f"home_{period}": file for period, file in HOME_SCENES.items()},
        "computer_day": COMPUTER_SCENE,
        "computer_night": COMPUTER_NIGHT_SCENE,
        "phone_dusk": PHONE_DUSK_SCENE,
        "phone_night": PHONE_NIGHT_SCENE,
        "sleep_night": SLEEP_NIGHT_SCENE,
        "sleep": "sleep.mp4",
    }
    if key in known:
        candidate = SCENES_DIR / known[key]
        if candidate.exists():
            return candidate
    for suffix in VIDEO_EXT + IMAGE_EXT:
        candidate = SCENES_DIR / f"{key}{suffix}"
        if candidate.exists():
            return candidate
    return DEFAULT_BG


def resolve_scene(hour: int, presence: dict, settings: Optional[dict] = None) -> tuple[str, Path, int]:
    """Select video from real time, current world and action; return key/path/loop start."""
    period = period_key(hour)
    world = str((presence or {}).get("current_world", "") or "")
    at_home = world in ("desktop", "home", "room", "bedroom")
    activity = str((presence or {}).get("activity", "") or "").casefold()
    awake = (presence or {}).get("awake", True)
    computer_action = any(token in activity for token in (
        "电脑", "摆弄电脑", "屏幕前", "游戏", "写代码", "浏览器", "看视频",
        "写东西", "随笔", "整理目录", "研究",
    ))
    bed_rest_action = any(token in activity for token in (
        "休息", "趴着", "躺", "眯一会儿", "手机",
    ))
    daylight = 6 <= hour < 19

    if at_home and not awake:
        sleep_key = f"sleep_{period}"
        if _has_scene_asset(sleep_key, settings):
            key = sleep_key
        elif _has_scene_asset("sleep_night", settings):
            key = "sleep_night"
        elif _has_scene_asset("sleep", settings):
            key = "sleep"
        else:
            key = "home_dusk" if _has_scene_asset("afternoon", settings) else "home_sleep"
        loop_start = SEQUENCE_LOOP_STARTS_MS.get(key, 0)
    elif at_home and awake and computer_action:
        if not daylight and _has_scene_asset("computer_night", settings):
            key = "computer_night"
        else:
            key = "computer_day"
        loop_start = SEQUENCE_LOOP_STARTS_MS[key]
    elif at_home and awake and bed_rest_action and period in ("afternoon", "dusk", "night"):
        if period == "night" and _has_scene_asset("phone_night", settings):
            key = "phone_night"
        elif period == "afternoon" and _has_scene_asset("afternoon", settings):
            key = "afternoon"
        elif _has_scene_asset("phone_dusk", settings):
            key = "phone_dusk"
        else:
            key = "home_dusk"
        loop_start = SEQUENCE_LOOP_STARTS_MS.get(key, BED_LOOP_START_MS)
    elif at_home:
        action_key = next(
            (name for name, tokens in ACTION_SCENE_TOKENS if any(token in activity for token in tokens)),
            None,
        )
        action_candidates = [f"{action_key}_{period}"] if action_key else []
        if action_key == "eating":
            action_candidates.append("eating_noon")
        if action_key == "phone" and period in ("noon", "afternoon"):
            action_candidates.append("phone_day")
        if action_key:
            action_candidates.append(action_key)
        key = next(
            (candidate for candidate in action_candidates if _has_scene_asset(candidate, settings)),
            f"home_{period}",
        )
        loop_start = SEQUENCE_LOOP_STARTS_MS.get(key, 0)
    else:
        key, loop_start = f"away_{period}", 0
    return key, scene_path(key, settings), loop_start


def _hex(value: QColor) -> str:
    return value.name(QColor.HexRgb)


if PYSIDE6_ERROR is None:

    class SceneVideo(QWidget):
        """Paint retained video frames in the same coordinate space as the UI.

        Supports crossfade transitions when switching clips so there is never
        a black flash or harsh cut.
        """

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.frame = QImage()
            self.prev_frame = QImage()
            self.blend_alpha = 1.0
            self._blend_timer = QTimer(self)
            self._blend_timer.setInterval(25)
            self._blend_timer.timeout.connect(self._step_blend)
            self.setAttribute(Qt.WA_OpaquePaintEvent)

        def begin_transition(self):
            """切换视频时保留上一支视频的末帧作为过渡底图，避免黑闪硬切。"""
            if not self.frame.isNull():
                self.prev_frame = self.frame.copy()
                self.blend_alpha = 0.0

        def _step_blend(self):
            self.blend_alpha = min(1.0, self.blend_alpha + 0.12)
            if self.blend_alpha >= 1.0:
                self.prev_frame = QImage()
                self._blend_timer.stop()
            self.update()

        def set_frame(self, frame):
            image = frame.toImage()
            if not image.isNull():
                first_of_transition = not self.prev_frame.isNull() and not self._blend_timer.isActive()
                self.frame = image
                if first_of_transition:
                    self._blend_timer.start()
                self.update()

        def clear_frame(self):
            self.begin_transition()
            self.update()

        def paintEvent(self, event):  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#171916"))
            if not self.frame.isNull():
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                scale = max(self.width() / self.frame.width(), self.height() / self.frame.height())
                width, height = self.width() / scale, self.height() / scale
                source = QRectF(
                    (self.frame.width() - width) / 2,
                    (self.frame.height() - height) / 2,
                    width,
                    height,
                )
                if not self.prev_frame.isNull() and self.blend_alpha < 1.0:
                    painter.drawImage(QRectF(self.rect()), self.prev_frame, source)
                    painter.setOpacity(self.blend_alpha)
                painter.drawImage(QRectF(self.rect()), self.frame, source)
                painter.setOpacity(1.0)
            painter.end()


    class SceneOverlay(QWidget):
        """视频之上的透明交互层：绘制真毛玻璃面板、环境轻浮标、房间交互热区与内嵌聊天抽屉。"""

        action = Signal(str)

        def __init__(self, owner: "PySideLivingHome", parent: QWidget | None = None):
            super().__init__(parent or owner)
            self.owner = owner
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self.setMouseTracking(True)
            self._hover_hotspot: Optional[tuple[str, str, int, int]] = None
            self.buttons: dict[str, QPushButton] = {}
            for key in ("us", "places", "workshop", "chat", "days", "pet"):
                button = QPushButton(self)
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, name=key: self.action.emit(name))
                self.buttons[key] = button

        def resizeEvent(self, event):  # noqa: N802 - Qt API
            self._place_buttons()
            super().resizeEvent(event)

        def _drawer_width(self) -> int:
            drawer = getattr(self.owner, "_chat_drawer", None)
            if drawer is not None and drawer.isVisible():
                return min(410, max(330, int(self.width() * 0.35)))
            return 0

        def _place_buttons(self):
            w, h = self.width(), self.height()
            margin = max(24, int(w * 0.035))
            x = w - margin
            unread = self.owner.desktop_unread_count()
            for key, label in (("workshop", "修理台"), ("pet", "桌宠"), ("places", "空间"), ("us", "我们")):
                if key == "workshop":
                    width = 78
                elif key == "places" and unread:
                    width = 88
                else:
                    width = 64
                x -= width
                self.buttons[key].setGeometry(x, margin, width, 40)
                if key == "places":
                    self.buttons[key].setText(f"空间 · {unread}" if unread else label)
                else:
                    self.buttons[key].setText(label)
                x -= 8
            self._buttons_left_x = x

            drawer_w = self._drawer_width()
            drawer = getattr(self.owner, "_chat_drawer", None)
            if drawer is not None and drawer.isVisible():
                drawer_top = margin + 52
                drawer_h = max(320, h - drawer_top - margin)
                drawer.setGeometry(w - margin - drawer_w, drawer_top, drawer_w, drawer_h)

            y0 = h - margin - 196
            avail_bottom_w = w - 2 * margin - (drawer_w + 16 if drawer_w else 0)
            left_w = max(310, min(int(w * 0.29), int(avail_bottom_w * 0.46)))
            self.buttons["chat"].setGeometry(margin + 20, y0 + 20, left_w - 40, 44)
            if drawer_w:
                self.buttons["chat"].setText("收起聊天")
            else:
                self.buttons["chat"].setText("敲敲门" if self.owner.alone else find_companion_label())

            right_x = margin + left_w + 16
            right_w = max(240, avail_bottom_w - left_w - 16)
            self.buttons["days"].setGeometry(max(right_x + right_w - 92, right_x), y0 + 14, 68, 34)
            self.buttons["days"].setText("全部")

        def _style_button(self, button: QPushButton, primary: bool = False):
            accent = self.owner.accent
            text = self.owner.on_accent if primary else self.owner.text
            r, g, b = self.owner.glass_tint
            br, bg_c, bb = self.owner.glass_border
            if primary and self.owner.alone and not self._drawer_width():
                background = f"rgba({r},{g},{b},140)"
                text = self.owner.text
            elif primary:
                background = _hex(accent)
            else:
                background = f"rgba({r},{g},{b},155)"
            border = _hex(accent) if primary else f"rgba({br},{bg_c},{bb},150)"
            button.setStyleSheet(
                "QPushButton {"
                f"background: {background}; color: {_hex(text)}; border: 1px solid {border};"
                f"border-radius: 13px; padding: 0 12px; font-family: '{self.owner.font_family}'; font-size: 9.8pt; font-weight: {'600' if primary else '500'};"
                f"}} QPushButton:hover {{ border-color: {_hex(accent)}; background: {'rgba(255,255,255,0.22)' if not primary else _hex(self.owner.text)}; color: {_hex(self.owner.on_accent) if primary else _hex(self.owner.text)}; }}"
                "QPushButton:pressed { padding-top: 2px; }"
            )

        def _room_hotspots(self) -> list[tuple[str, str, QRectF]]:
            w, h = float(self.width()), float(self.height())
            margin = max(24, int(w * 0.035))
            y_limit = h - margin - 206
            drawer_w = self._drawer_width()
            x_limit = w - margin - drawer_w - 12 if drawer_w else w
            if y_limit <= 120:
                return []
            skey = getattr(self.owner, "_scene_key", "") or ""
            spots = []
            desk_x = w * 0.60
            if desk_x + w * 0.16 <= x_limit:
                spots.append((
                    "places",
                    "伴生体正在用电脑 · 点击查看空间" if "computer" in skey else "查看伴生体的电脑空间",
                    QRectF(desk_x, h * 0.25, min(w * 0.22, x_limit - desk_x), min(h * 0.35, y_limit - h * 0.25)),
                ))
            if not skey.startswith("away_"):
                if any(k in skey for k in ("afternoon", "dusk", "phone", "sleep")):
                    spots.append((
                        "chat",
                        "在床边找伴生体聊聊",
                        QRectF(w * 0.18, h * 0.28, w * 0.23, min(h * 0.28, y_limit - h * 0.28)),
                    ))
                elif "computer" not in skey:
                    spots.append((
                        "chat",
                        "找地毯上的伴生体聊聊",
                        QRectF(w * 0.35, h * 0.36, min(w * 0.22, max(0.0, x_limit - w * 0.35)), min(h * 0.32, y_limit - h * 0.36)),
                    ))
            return spots

        def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
            pos = event.position()
            hit = None
            for action_name, label, rect in self._room_hotspots():
                if rect.contains(pos):
                    hit = (action_name, label, int(pos.x()), int(pos.y()))
                    break
            if hit != self._hover_hotspot:
                self._hover_hotspot = hit
                self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
                self.update()
            super().mouseMoveEvent(event)

        def mousePressEvent(self, event):  # noqa: N802 - Qt API
            if event.button() == Qt.LeftButton:
                pos = event.position()
                for action_name, _label, rect in self._room_hotspots():
                    if rect.contains(pos):
                        self.action.emit(action_name)
                        event.accept()
                        return
            super().mousePressEvent(event)

        def _draw_ambient_badges(self, painter: QPainter, w: int, h: int, drawer_w: int):
            """在房间画面中绘制极克制的环境轻浮标（如正在播放的歌、桌面新随笔提示）。"""
            state = self.owner._presence_snapshot()
            music = state.get("music") or {}
            badges: list[tuple[int, int, str]] = []
            if music.get("playing") and music.get("title") and not drawer_w:
                title = str(music["title"])[:16]
                badges.append((int(w * 0.62), int(h * 0.18), f"♪ 正在听《{title}》"))
            if self.owner.has_recent_essay() and not drawer_w:
                badges.append((int(w * 0.66), int(h * 0.56), "▪ 书桌上有今日随笔"))

            painter.setFont(QFont(self.owner.font_family, 8.8))
            r, g, b = self.owner.glass_tint
            for bx, by, text in badges:
                tw = painter.fontMetrics().horizontalAdvance(text) + 22
                th = 26
                bx = max(24, min(bx, w - tw - 24))
                painter.setBrush(QColor(r, g, b, 185))
                painter.setPen(QPen(self.owner.accent, 1))
                painter.drawRoundedRect(bx, by, tw, th, 13, 13)
                painter.setPen(self.owner.text)
                painter.drawText(QRectF(bx, by, tw, th), Qt.AlignCenter, text)

        def paintEvent(self, event):  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            w, h = self.width(), self.height()

            # 视频首帧准备好前先显示静态房间，避免窗口上方出现空白或黑闪。
            if not self.owner.video.isVisible() and not self.owner.fallback.isNull():
                pixmap = self.owner.fallback.scaled(
                    self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                x = (pixmap.width() - w) // 2
                y = (pixmap.height() - h) // 2
                painter.drawPixmap(0, 0, pixmap, x, y, w, h)
                painter.fillRect(self.rect(), QColor(12, 14, 12, 42))

            margin = max(24, int(w * 0.035))
            drawer_w = self._drawer_width()
            avail_bottom_w = w - 2 * margin - (drawer_w + 16 if drawer_w else 0)
            y0 = h - margin - 196
            left_w = max(310, min(int(w * 0.29), int(avail_bottom_w * 0.46)))
            right_x = margin + left_w + 16
            right_w = max(240, avail_bottom_w - left_w - 16)

            self._glass(painter, margin, y0, left_w, 196)
            self._glass(painter, right_x, y0, right_w, 196)

            r, g, b = self.owner.glass_tint
            br, bg_c, bb = self.owner.glass_border

            # 左下“此刻”卡片内容（右上角内嵌低调无框本地时间，去掉突兀的顶部悬浮时间胶囊）
            painter.setPen(self.owner.accent)
            painter.setFont(QFont("Segoe UI Symbol", 9))
            painter.drawText(margin + 24, y0 + 100, "●")

            place, status, desc = self.owner.current_presence()
            painter.setPen(self.owner.text)
            painter.setFont(QFont(self.owner.font_family, 11.5, QFont.Bold))
            painter.drawText(margin + 42, y0 + 102, place)
            offset = painter.fontMetrics().horizontalAdvance(place) + 56
            painter.setPen(self.owner.muted)
            painter.drawLine(margin + offset - 14, y0 + 88, margin + offset - 14, y0 + 108)
            painter.setPen(self.owner.text)
            painter.setFont(QFont(self.owner.font_family, 11))
            max_status_w = max(60, left_w - offset - 68)
            status_elided = painter.fontMetrics().elidedText(status, Qt.ElideRight, max_status_w)
            painter.drawText(margin + offset, y0 + 102, status_elided)

            # 在卡片内右侧低调显示当前时间，不遮挡房间窗户画面
            painter.setPen(self.owner.muted)
            painter.setFont(QFont("Consolas", 9.5))
            now_str = datetime.now().strftime("%H:%M")
            tw_clock = painter.fontMetrics().horizontalAdvance(now_str)
            painter.drawText(margin + left_w - 24 - tw_clock, y0 + 101, now_str)

            # 下方两行环境与状态描述：去重、柔和次级色、严格按卡片宽度自动省略防贴边溢出
            facts = self.owner.room_facts()
            sub_lines: list[str] = []
            clean_desc = desc.rstrip("。").strip()
            if clean_desc and clean_desc != status.strip():
                sub_lines.append(desc)
            for fc in facts:
                if fc and fc not in sub_lines:
                    sub_lines.append(fc)
            if not sub_lines:
                sub_lines.append(desc)

            painter.setPen(self.owner.muted)
            painter.setFont(QFont(self.owner.font_family, 9.5))
            max_line_w = max(120, left_w - 48)
            for idx_l, sline in enumerate(sub_lines[:2]):
                elided = painter.fontMetrics().elidedText(sline, Qt.ElideRight, max_line_w)
                painter.drawText(margin + 24, y0 + 134 + idx_l * 28, elided)

            # 右下“今天”卡片内容
            painter.setPen(self.owner.text)
            painter.setFont(QFont(self.owner.font_family, 12, QFont.Bold))
            painter.drawText(right_x + 26, y0 + 34, "今天")
            painter.setPen(QColor(br, bg_c, bb, 90))
            painter.drawLine(right_x + 26, y0 + 52, right_x + right_w - 26, y0 + 52)

            recent = self.owner.recent_lines()
            max_line_chars = max(14, (right_w - 148) // 14)
            for index, (stamp, line) in enumerate(recent):
                y = y0 + 82 + index * 32
                painter.setPen(self.owner.accent if index == 0 else self.owner.muted)
                painter.setFont(QFont("Segoe UI Symbol", 9))
                painter.drawText(right_x + 28, y, "●" if index == 0 else "○")
                painter.setPen(self.owner.muted)
                painter.setFont(QFont("Consolas", 9))
                painter.drawText(right_x + 52, y, stamp)
                painter.setPen(self.owner.text)
                painter.setFont(QFont(self.owner.font_family, 9.8))
                painter.drawText(right_x + 112, y, line[:max_line_chars])

            if not recent:
                painter.setPen(self.owner.muted)
                painter.setFont(QFont(self.owner.font_family, 10))
                painter.drawText(right_x + 28, y0 + 92, "今天还没有可显示的记录。")

            self._draw_ambient_badges(painter, w, h, drawer_w)

            if self._hover_hotspot:
                _act, hlabel, hx, hy = self._hover_hotspot
                painter.setFont(QFont(self.owner.font_family, 9))
                tw = painter.fontMetrics().horizontalAdvance(hlabel) + 24
                th = 30
                tx = max(24, min(hx - tw // 2, w - tw - 24))
                ty = max(64, hy - 38)
                painter.setBrush(QColor(r, g, b, 225))
                painter.setPen(QPen(self.owner.accent, 1.2))
                painter.drawRoundedRect(tx, ty, tw, th, 14, 14)
                painter.setPen(self.owner.text)
                painter.drawText(QRectF(tx, ty, tw, th), Qt.AlignCenter, hlabel)

            for key, button in self.buttons.items():
                self._style_button(button, primary=key == "chat")
            painter.end()

        def _glass(self, painter: QPainter, x: int, y: int, w: int, h: int, radius: int = 22):
            """实时采样底层视频帧做降采样高斯模糊，再叠加时段光影毛玻璃与高光描边。"""
            rect_f = QRectF(x, y, w, h)
            path = QPainterPath()
            path.addRoundedRect(rect_f, radius, radius)

            vframe = self.owner.video.frame
            if vframe.isNull() and not self.owner.fallback.isNull():
                vframe = self.owner.fallback.toImage()

            painter.save()
            painter.setClipPath(path)
            if not vframe.isNull() and self.width() > 0 and self.height() > 0:
                scale = max(self.width() / vframe.width(), self.height() / vframe.height())
                vw, vh = self.width() / scale, self.height() / scale
                sx0 = (vframe.width() - vw) / 2.0 + (x / scale)
                sy0 = (vframe.height() - vh) / 2.0 + (y / scale)
                sw = max(12.0, w / scale)
                sh = max(12.0, h / scale)
                patch = vframe.copy(int(sx0), int(sy0), int(sw), int(sh))
                small = patch.scaled(
                    max(8, int(w // 8)),
                    max(8, int(h // 8)),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                blurred = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                painter.drawImage(rect_f, blurred)

            r, g, b = self.owner.glass_tint
            painter.fillRect(rect_f, QColor(r, g, b, self.owner.card_alpha))
            painter.restore()

            br, bg_c, bb = self.owner.glass_border
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(br, bg_c, bb, 138), 1.1))
            painter.drawRoundedRect(rect_f, radius, radius)


    class PySideLivingHome(QMainWindow):
        """可独立运行也可作为正式首页迁移基础的 Qt 窗口。"""

        def __init__(
            self,
            chen=None,
            on_chat: Optional[Callable[[], None]] = None,
            on_relationship: Optional[Callable[[], None]] = None,
            on_life: Optional[Callable[[], None]] = None,
            on_world: Optional[Callable[[], None]] = None,
            on_workshop: Optional[Callable[[], None]] = None,
            on_pet: Optional[Callable[[], None]] = None,
            on_studio: Optional[Callable[[], None]] = None,
            on_close: Optional[Callable[[], None]] = None,
        ):
            super().__init__()
            self.chen = chen
            self.source = chen or LocalStateProvider()
            self.on_close = on_close
            self._allow_close = False
            self.alone = False  # F2 仅用于开发演示，不写入伴生体状态。
            self._chat_window = None
            self._chat_drawer = None
            self._chat_floating = None
            self._chat_docked = True
            self.callbacks = {
                "chat": on_chat,
                "us": on_relationship,
                "days": on_life,
                "places": on_world,
                "workshop": on_workshop,
                "pet": on_pet,
                "studio": on_studio,
            }
            self.settings = load_scene_settings()
            self.key = period_key()
            self.font_family = _load_ui_font()
            self._apply_period_theme(self.key)

            self.setWindowTitle(f"{companion_name()}的世界")
            self.setMinimumSize(820, 560)
            self.resize(1060, 680)
            self._setup_scene()
            self.overlay = SceneOverlay(self, self.scene_container)
            self.scene_stack.addWidget(self.overlay)
            self.scene_stack.setCurrentWidget(self.overlay)
            self.overlay.action.connect(self._dispatch)
            self.clock = QTimer(self)
            self.clock.timeout.connect(self._watch_period)
            self.clock.start(3000)

        def _apply_period_theme(self, period: Optional[str] = None):
            pt = get_period_theme(period)
            self.period_label = pt["label"]
            self.glass_tint = pt["tint"]
            self.glass_border = pt["border"]
            self.card_alpha = pt["card_alpha"]
            self.accent = QColor(pt["accent_hex"])
            self.text = QColor(pt["text_hex"])
            self.muted = QColor(pt["muted_hex"])
            self.on_accent = QColor(pt["on_accent_hex"])

        def _setup_scene(self):
            self.scene_container = QWidget(self)
            self.scene_stack = QStackedLayout(self.scene_container)
            self.scene_stack.setContentsMargins(0, 0, 0, 0)
            self.scene_stack.setSpacing(0)
            self.scene_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
            self.setCentralWidget(self.scene_container)

            self.video = SceneVideo(self.scene_container)
            self.scene_stack.addWidget(self.video)
            self.video.hide()
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(0.0)
            self.player.setAudioOutput(self.audio)
            self.video_sink = QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.video_sink.videoFrameChanged.connect(self._video_frame)
            self.player.errorOccurred.connect(self._media_error)
            self.player.mediaStatusChanged.connect(self._media_status)
            self.fallback = _load_background_pixmap(DEFAULT_BG)
            self._scene_key = None
            self._scene_path = None
            self._loop_start_ms = 0
            self._looping = False
            self._load_period_scene()

        def _load_period_scene(self):
            presence = self._presence_snapshot()
            now_hour = datetime.now().hour
            new_period = period_key(now_hour)
            if new_period != self.key:
                self.key = new_period
                self._apply_period_theme(self.key)
            key, source, loop_start = resolve_scene(now_hour, presence, self.settings)
            if key == self._scene_key and source == self._scene_path:
                return
            self._apply_period_theme(self.key)
            if (
                source == self._scene_path
                and key not in SEQUENCE_LOOP_STARTS_MS
                and self._scene_key not in SEQUENCE_LOOP_STARTS_MS
            ):
                self._scene_key, self._loop_start_ms = key, loop_start
                self.update()
                return
            self._scene_key, self._scene_path, self._loop_start_ms = key, source, loop_start
            self._looping = False
            self.fallback = _load_background_pixmap(source if source.suffix.lower() in IMAGE_EXT else DEFAULT_BG)
            self.player.stop()
            self.video.clear_frame()
            if self.video.frame.isNull():
                self.video.hide()
            if source.suffix.lower() in VIDEO_EXT:
                self.player.setSource(QUrl.fromLocalFile(str(source)))
                self.player.setLoops(
                    QMediaPlayer.Loops.Once if loop_start else QMediaPlayer.Loops.Infinite
                )
                self.player.play()
            if hasattr(self, "overlay"):
                self.overlay.update()

        def _media_error(self, *_args):
            self.video.hide()
            if hasattr(self, "overlay"):
                self.overlay.update()

        def _video_frame(self, frame):
            if not frame.isValid():
                return
            if self._looping and frame.startTime() >= 0 and frame.startTime() // 1000 < self._loop_start_ms:
                return
            self.video.set_frame(frame)
            if not self.video.frame.isNull():
                self.video.show()
                if hasattr(self, "overlay"):
                    self.overlay.update()

        def _media_status(self, status):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                self._restart_video_loop()

        def _restart_video_loop(self):
            """Loop without replaying a sequence clip's entrance action."""
            if self._scene_path is None or self._scene_path.suffix.lower() not in VIDEO_EXT:
                return
            duration = self.player.duration()
            loop_start = max(0, int(self._loop_start_ms))
            if loop_start >= duration:
                self._loop_start_ms = loop_start = 0
            self._looping = True
            self.player.setPosition(loop_start)
            self.player.play()

        def _watch_period(self):
            self._load_period_scene()
            if hasattr(self, "overlay"):
                self.overlay._place_buttons()
            self.overlay.update()

        def desktop_unread_count(self):
            try:
                from .pyside_virtual_desktop import desktop_unread_count
                return desktop_unread_count(self.chen)
            except Exception:
                return 0

        def has_recent_essay(self) -> bool:
            """检查今天是否有伴生体新写的随笔或自主记录，供房间桌面浮标显示。"""
            try:
                autonomy = getattr(self.chen, "autonomy", None) if self.chen else None
                if autonomy and isinstance(getattr(autonomy, "state", None), dict):
                    if int(autonomy.state.get("write_actions", 0) or 0) > 0:
                        return True
            except Exception:
                pass
            return False

        def _presence_snapshot(self):
            try:
                return self.source.get_presence_snapshot(persist=False) or {}
            except Exception:
                return {}

        def _world_state(self):
            try:
                if self.chen and getattr(self.chen, "world", None):
                    return self.chen.world.get_state() or {}
                return self.source.world_state() or {}
            except Exception:
                return {}

        def current_presence(self):
            state = self._presence_snapshot()
            if not state:
                return "房间", "安静待着", "此刻在房间里安静待着。"
            raw_place = str(state.get("activity_place") or "").strip()
            if not raw_place or raw_place in ("位置未知", "未知"):
                place = "小世界" if state.get("current_world") == "small_world" else "房间"
            else:
                place = raw_place
            activity = state.get("activity") or "安静待着"
            awake = state.get("awake")
            try:
                life = getattr(self.chen, "life", None)
                if life:
                    awake = life.state.get("awake", awake)
                    activity = life.state.get("activity") or activity
            except Exception:
                pass
            if awake is False:
                activity = "睡着了"
            if self.alone:
                return place, "想一个人待着", "要敲门的话，他可能会应。"
            return place, activity, f"{activity}。"

        def room_facts(self):
            """结合当前播放的场景视频实况与房间状态展示环境细节。"""
            state = self._presence_snapshot()
            facts = []
            visual_hints = SCENE_VISUAL_FACTS.get(self._scene_key or "", ())
            for vh in visual_hints:
                if vh not in facts:
                    facts.append(vh)
            music = state.get("music") or {}
            if music.get("playing") and music.get("title"):
                facts.insert(0, f"正在播放《{str(music['title'])[:18]}》")
            house = self._world_state().get("house_state") or {}
            if house.get("light_on") is True and "台灯" not in "".join(facts):
                facts.append("灯亮着")
            if house.get("window_open") is True:
                facts.append("窗户开着")
            return facts[:2]

        def recent_lines(self):
            try:
                day_log = getattr(self.source, "day_log", None)
                rows_source = day_log.today_lines() if day_log else self.source.today_lines()
                if rows_source:
                    rows = []
                    for item in reversed(rows_source):
                        if item.get("kind") not in ("life", "world", "chat", "autonomy", "web"):
                            continue
                        stamp = str(item.get("ts", ""))[-5:]
                        line = str(item.get("text", ""))[:32]
                        if line and not line.startswith("环境变化:"):
                            rows.append((stamp, line))
                        if len(rows) >= 3:
                            break
                    if rows:
                        return rows
            except Exception:
                pass
            return []

        def _dispatch(self, name: str):
            callback = self.callbacks.get(name)
            if callback:
                callback()
            elif name == "chat":
                self.open_chat()
            elif name == "us":
                QMessageBox.information(self, "我们", "这里会打开正式的关系界面。")
            elif name == "days":
                QMessageBox.information(self, "今天", "这里会打开正式的生活记录。")
            elif name == "places":
                QMessageBox.information(self, "空间", "这里会打开伴生体留下生活痕迹的空间。")
            elif name == "studio":
                try:
                    from .pyside_panels import CompanionStudioPanel
                    win = CompanionStudioPanel(self.chen)
                    win.show()
                    win.raise_()
                    self._studio_win = win
                except Exception:
                    QMessageBox.information(self, "伴生体工坊", "这里会打开开源伴生体定制界面。")
            else:
                QMessageBox.information(self, "修理台", "这里会打开正式的后台和插件中心。")

        def keyPressEvent(self, event):  # noqa: N802 - Qt API
            if event.key() == Qt.Key_F2:
                self.set_alone(not self.alone)
                return
            super().keyPressEvent(event)

        def set_alone(self, value: bool):
            """Keep the home action and the chat banner on one state source."""
            self.alone = bool(value)
            if hasattr(self, "overlay"):
                self.overlay._place_buttons()
                self.overlay.update()
            if self._chat_window is not None and self._chat_window.isVisible():
                self._chat_window.sync()

        def _show_self(self):
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

        def open_chat(self):
            """始终打开独立轻量的聊天窗口（无需保持厚重的视频首页开启）。"""
            try:
                from .pyside_chat import ChatWindow
            except ImportError:
                from chen.interface.pyside_chat import ChatWindow
            if self._chat_window is None:
                nav_cbs = {
                    "us": lambda: self._dispatch("us"),
                    "days": lambda: self._dispatch("days"),
                    "places": lambda: self._dispatch("places"),
                    "pet": lambda: self._dispatch("pet"),
                    "studio": lambda: self._dispatch("studio"),
                    "workshop": lambda: self._dispatch("workshop"),
                    "home": self._show_self,
                }
                self._chat_window = ChatWindow(
                    chen=self.chen,
                    get_alone=lambda: self.alone,
                    set_alone=self.set_alone,
                    get_presence=self._presence_snapshot,
                    nav_callbacks=nav_cbs,
                )
            self._chat_window.reload_background()
            self._chat_window.sync()
            self._chat_window.show()
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            self._chat_window.input.setFocus()

        def hideEvent(self, event):  # noqa: N802 - Qt API
            # 隐藏首页到托盘时自动暂停视频解码，保证只开独立聊天小窗或桌宠时 0 视频开销
            try:
                self.player.pause()
            except Exception:
                pass
            super().hideEvent(event)

        def showEvent(self, event):  # noqa: N802 - Qt API
            super().showEvent(event)
            try:
                if self._scene_path and self._scene_path.suffix.lower() in VIDEO_EXT:
                    self.player.play()
            except Exception:
                pass

        def resizeEvent(self, event):  # noqa: N802 - Qt API
            super().resizeEvent(event)
            if not hasattr(self, "scene_container") or not hasattr(self, "overlay"):
                return
            rect = self.scene_container.rect()
            self.video.setGeometry(rect)
            self.overlay.setGeometry(rect)
            self.overlay.raise_()

        def closeEvent(self, event):  # noqa: N802 - Qt API
            # 首页的关闭按钮只隐藏到托盘；真正退出由托盘菜单“退出伴生体”完成。
            if not self._allow_close:
                if self.on_close:
                    self.on_close()
                event.ignore()
                return
            self.player.stop()
            if self._chat_window is not None:
                self._chat_window.close()
            event.accept()


else:

    class PySideLivingHome:  # pragma: no cover - only used for dependency diagnostics
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "PySide6 尚未安装。请在项目使用的 Python 环境运行：python -m pip install PySide6"
            ) from PYSIDE6_ERROR


def run_demo():
    """供 workspace/pyside_home_demo.py 调用的最小启动入口。"""
    if PYSIDE6_ERROR is not None:
        raise RuntimeError(
            "PySide6 尚未安装。请在项目使用的 Python 环境运行：python -m pip install PySide6"
        ) from PYSIDE6_ERROR
    app = QApplication.instance() or QApplication([])
    window = PySideLivingHome()
    window.show()
    return app.exec()
