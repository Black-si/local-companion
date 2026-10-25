"""纯原生 PySide6 桌宠实现（零 Tkinter 依赖、零 16ms 轮询泵、原生透明与平滑动画）。

彻底取代旧版“Qt 定时器泵送隐藏 Tk 窗口 + Win32 Ctypes GDI 贴图”的桥接方案：
- 使用 Qt 原生无边框透明置顶窗口（FramelessWindowHint | WindowStaysOnTopHint | Tool + WA_TranslucentBackground）
- 启动/按需使用 Pillow Lanczos 配合屏幕 HiDPI 缩放比预生成高清 QPixmap 缓存，杜绝运行时重复解码与 GDI 句柄泄漏
- 呼吸起伏在 paintEvent 内部偏移绘制坐标，不再高频移动操作系统顶级窗口位置
- 结合 assets/pet/layout.json 的包围盒数据，提供精准悬浮气泡（PetSpeechBubble）与现代暗色 QMenu 右键菜单
- 完整保留 PySideDesktopPet 与 .pet / .window 的对外接口，100% 兼容 pyside_app.py、chen.runtime 与专项回归测试
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
from typing import Callable, Optional

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from PySide6.QtCore import QObject, QPoint, QRectF, QTimer, Qt
    from PySide6.QtGui import (
        QColor,
        QContextMenuEvent,
        QFont,
        QGuiApplication,
        QImage,
        QKeyEvent,
        QMouseEvent,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    PYSIDE6_AVAILABLE = True
except ImportError:  # pragma: no cover - 允许无 PySide6 的纯测试环境导入
    PYSIDE6_AVAILABLE = False
    QObject = object
    QWidget = object

from .desktop_pet import DesktopPet, load_pet_layout


ROOT = Path(__file__).resolve().parents[3]
PET_ASSETS = ROOT / "assets" / "pet"
LAYOUT_PATH = PET_ASSETS / "layout.json"
POSITION_PATH = ROOT / "workspace" / "desktop_pet_position.json"

# 桌宠轻交互台词（克制、符合伴生体安静性格，仅在点击/状态切换时低频浮现）
INTERACTION_LINES = {
    "click": ("……怎么了？", "嗯，我在。", "有事找我？", "耳朵别乱碰。"),
    "happy": ("今天心情还不错。", "嗯，听你说。"),
    "drag": ("……慢点拎。", "放我下来。", "别晃……"),
    "sleep": ("困了，先歇会儿……", "晚安，有事叫我。"),
    "wake": ("……醒了。", "嗯，没睡死。"),
    "music": ("这首还挺好听的。", "陪你听会儿歌。"),
    "drink": ("热乎的，你也去倒一杯吧。", "喝口热的歇会儿。"),
    "walk": ("出去溜达两步。", "活动一下。"),
}


def desktop_pet_asset_map(asset_dir: Path = PET_ASSETS) -> dict[str, str]:
    """返回桌宠完整 21 张标准生产状态图映射（保持与资产注册测试严格一致）。"""
    names = (
        "idle",
        "blink",
        "blink_half",
        "world_return",
        "world_return_2",
        "music",
        "music_2",
        "tired",
        "tired_rub",
        "sleep",
        "side_walk_1",
        "side_walk_2",
        "side_walk_3",
        "side_walk_4",
        "happy",
        "surprised",
        "drag",
        "sit",
        "stretch",
        "stretch_up",
        "sit_stretch",
    )
    return {name: str(asset_dir / f"{name}.png") for name in names}


def all_pet_asset_map(asset_dir: Path = PET_ASSETS) -> dict[str, str]:
    """包含正面行走帧 walk_1..4 与喝热饮 drink 在内的完整 26 张资源映射。"""
    assets = desktop_pet_asset_map(asset_dir)
    for extra in ("walk_1", "walk_2", "walk_3", "walk_4", "drink"):
        candidate = asset_dir / f"{extra}.png"
        if candidate.exists():
            assets[extra] = str(candidate)
    return assets


if PYSIDE6_AVAILABLE:

    class PetSpeechBubble(QWidget):
        """悬浮在桌宠头顶的轻量磨砂对话/状态气泡。"""

        def __init__(self, parent: Optional[QWidget] = None):
            super().__init__(parent)
            self._text = ""
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowTransparentForInput
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(self.hide)

        def show_message(self, text: str, anchor_global: QPoint, duration_ms: int = 3000):
            text = (text or "").strip()
            if not text:
                self.hide()
                return
            self._text = text
            font = QFont("Microsoft YaHei UI", 9)
            self.setFont(font)
            metrics = self.fontMetrics()
            text_w = min(200, max(64, metrics.horizontalAdvance(text) + 24))
            lines = max(1, (metrics.horizontalAdvance(text) // 180) + 1)
            text_h = 30 + (lines - 1) * 18
            self.resize(text_w, text_h + 8)

            x = anchor_global.x() - self.width() // 2
            y = max(8, anchor_global.y() - self.height() - 4)
            self.move(x, y)
            self.show()
            self.raise_()
            self.update()
            self._hide_timer.start(max(800, int(duration_ms)))

        def paintEvent(self, _event):  # noqa: N802 - Qt API
            if not self._text:
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            body_rect = QRectF(1, 1, self.width() - 2, self.height() - 9)
            path = QPainterPath()
            path.addRoundedRect(body_rect, 10, 10)

            # 底部小三角指向伴生体头顶
            cx = self.width() / 2.0
            by = body_rect.bottom()
            path.moveTo(cx - 5, by)
            path.lineTo(cx, by + 6)
            path.lineTo(cx + 5, by)
            path.closeSubpath()

            painter.setBrush(QColor(32, 34, 32, 230))
            painter.setPen(QPen(QColor(150, 172, 143, 160), 1.0))
            painter.drawPath(path)

            painter.setPen(QColor("#f4eee4"))
            painter.drawText(
                body_rect.adjusted(10, 4, -10, -4),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                self._text,
            )
            painter.end()

    class NativeQtPetWindow(QWidget):
        """纯 PySide6 原生渲染的桌宠置顶窗口，同时提供与 DesktopPet 完全一致的状态机属性。"""

        PET_MAX_WIDTH = DesktopPet.PET_MAX_WIDTH
        PET_MAX_HEIGHT = DesktopPet.PET_MAX_HEIGHT
        PET_SOURCE_SIZE = DesktopPet.PET_SOURCE_SIZE
        STATE_LOOPS = DesktopPet.STATE_LOOPS
        STATE_PRESETS = {
            **DesktopPet.STATE_PRESETS,
            "drink": (1600, 1),
        }

        def __init__(
            self,
            asset_path: str,
            on_click: Optional[Callable[[], None]] = None,
            state_assets: Optional[dict[str, str]] = None,
            context_actions: Optional[dict[str, Callable]] = None,
            position_path: Optional[str] = None,
            on_interact: Optional[Callable[[str], None]] = None,
            layout_path: Optional[str] = None,
        ):
            super().__init__(None)
            self.asset_path = asset_path
            self.on_click = on_click
            self.on_interact = on_interact
            self.state_assets = dict(state_assets or {})
            self.context_actions = dict(context_actions or {})
            self.position_path = position_path
            self.layout_data = load_pet_layout(layout_path or LAYOUT_PATH)

            display_size = self.layout_data.get("display_size")
            if isinstance(display_size, (list, tuple)) and len(display_size) == 2:
                self.PET_MAX_WIDTH = int(display_size[0])
                self.PET_MAX_HEIGHT = int(display_size[1])

            canvas_size = self.layout_data.get("canvas_size")
            if isinstance(canvas_size, (list, tuple)) and len(canvas_size) == 2:
                self.PET_SOURCE_SIZE = (int(canvas_size[0]), int(canvas_size[1]))

            # 窗口样式：无边框 + 置顶 + 工具窗口（不在任务栏出现第二个图标）+ 原生 Per-pixel Alpha
            self.setWindowTitle("伴生体")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setFixedSize(self.PET_MAX_WIDTH, self.PET_MAX_HEIGHT)

            # 状态机与缓存字段（与 DesktopPet 保持同构，便于外部状态检查）
            self.window = self
            self._locked = False
            self._state = "idle"
            self._base_state = "idle"
            self._current_asset: Optional[str] = None
            self._flip_current = False
            self._pixmap_cache: dict[tuple[str, bool, float], QPixmap] = {}
            self._alpha_cache: dict[tuple[str, bool], QImage] = {}
            self._current_pixmap: Optional[QPixmap] = None
            self._current_alpha_img: Optional[QImage] = None

            # 呼吸动画（仅在 paintEvent 内偏移绘制 Y 坐标，绝不抖动 OS 窗口）
            self._idle_interval = 1800
            self._idle_amplitude = 1
            self._idle_phase = 0
            self._breath_offset = 0
            self._idle_origin: Optional[tuple[int, int]] = None

            # 动作序列与循环
            self._action_sequence: Optional[list[tuple[str, int]]] = None
            self._action_sequence_index = 0
            self._idle_action_active = False
            self._state_loop_index = 0
            self._state_loop_frames: list[str] = []
            self._state_loop_interval: int | list[int] | tuple[int, ...] = 1000

            # 行走状态（30fps 丝滑步进 + 独立步态切帧）
            self._walking = False
            self._walk_target: Optional[tuple[int, int]] = None
            self._walk_pos: Optional[tuple[float, float]] = None
            self._walk_frame = 0
            self._walk_tick_ms = 32
            self._walk_speed_px_per_sec = 92.0
            self._walk_frame_accum_ms = 0
            self._walk_frame_interval_ms = 160

            # 鼠标拖拽状态
            self._drag: Optional[tuple[int, int, int, int]] = None
            self._press_pos: Optional[tuple[int, int]] = None
            self._did_move = False

            # 权威位置初始化
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            init_x, init_y = self._load_position(
                sw - self.PET_MAX_WIDTH - 36,
                sh - self.PET_MAX_HEIGHT - 84,
                self.PET_MAX_WIDTH,
                self.PET_MAX_HEIGHT,
            )
            self._pos = [init_x, init_y]
            self._idle_origin = (init_x, init_y)
            self.move(init_x, init_y)

            # 头顶气泡组件
            self.bubble = PetSpeechBubble()

            # 定时器组（全部按需低频触发，平时零空转）
            self._breath_timer = QTimer(self)
            self._breath_timer.timeout.connect(self._idle_step)

            self._blink_timer = QTimer(self)
            self._blink_timer.setSingleShot(True)
            self._blink_timer.timeout.connect(self._blink_step)

            self._auto_walk_timer = QTimer(self)
            self._auto_walk_timer.setSingleShot(True)
            self._auto_walk_timer.timeout.connect(self._auto_walk_step)

            self._idle_action_timer = QTimer(self)
            self._idle_action_timer.setSingleShot(True)
            self._idle_action_timer.timeout.connect(self._idle_action_step)

            self._action_timer = QTimer(self)
            self._action_timer.setSingleShot(True)
            self._action_timer.timeout.connect(self._run_action_sequence)

            self._walk_timer = QTimer(self)
            self._walk_timer.setInterval(self._walk_tick_ms)
            self._walk_timer.timeout.connect(self._walk_step)

            self._state_loop_timer = QTimer(self)
            self._state_loop_timer.setSingleShot(True)
            self._state_loop_timer.timeout.connect(self._state_loop_tick)

            self._state_loop_end_timer = QTimer(self)
            self._state_loop_end_timer.setSingleShot(True)
            self._state_loop_end_timer.timeout.connect(self._on_state_loop_timeout)
            self._state_loop_then = "idle"

            # 预热默认待机帧
            self._reload_asset(self.state_assets.get("idle", self.asset_path))

        # --- 兼容旧版 Tk window 状态检查的方法 ---
        def winfo_exists(self) -> bool:
            return True

        def state(self) -> str:
            return "normal" if self.isVisible() else "withdrawn"

        def winfo_screenwidth(self) -> int:
            screen = QGuiApplication.primaryScreen()
            return screen.geometry().width() if screen else 1920

        def winfo_screenheight(self) -> int:
            screen = QGuiApplication.primaryScreen()
            return screen.geometry().height() if screen else 1080

        def winfo_width(self) -> int:
            return self.width() or self.PET_MAX_WIDTH

        def winfo_height(self) -> int:
            return self.height() or self.PET_MAX_HEIGHT

        def winfo_x(self) -> int:
            return int(self._pos[0])

        def winfo_y(self) -> int:
            return int(self._pos[1])

        def _visible(self) -> bool:
            return bool(self.isVisible())

        # --- 坐标、屏幕工作区与持久化 ---
        def _screen_bounds(self) -> tuple[int, int, int, int]:
            screen = QGuiApplication.ScreenAt(QPoint(self.x(), self.y())) if hasattr(QGuiApplication, "ScreenAt") else None
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                return avail.left(), avail.top(), avail.right() + 1, avail.bottom() + 1
            return 0, 0, 1920, 1080

        def _clamp_position(
            self, x: int | float, y: int | float, width: Optional[int] = None, height: Optional[int] = None
        ) -> tuple[int, int]:
            left, top, right, bottom = self._screen_bounds()
            w = width or self.PET_MAX_WIDTH
            h = height or self.PET_MAX_HEIGHT
            cx = round(max(left, min(float(x), max(left, right - w))))
            cy = round(max(top, min(float(y), max(top, bottom - h))))
            return cx, cy

        def _load_position(self, default_x: int, default_y: int, width: int, height: int) -> tuple[int, int]:
            if self.position_path:
                try:
                    with open(self.position_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        x = int(data.get("x", default_x))
                        y = int(data.get("y", default_y))
                        self._locked = data.get("locked") is True
                        return self._clamp_position(x, y, width, height)
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
            return self._clamp_position(default_x, default_y, width, height)

        def _save_position(self):
            if not self.position_path:
                return
            try:
                path = os.path.abspath(self.position_path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "x": int(self._pos[0]),
                            "y": int(self._pos[1]),
                            "locked": self._locked,
                        },
                        f,
                    )
                os.replace(tmp, path)
            except OSError:
                pass

        # --- layout.json 包围盒与气泡锚点 ---
        def frame_bounds(self, state_name: Optional[str] = None) -> tuple[int, int, int, int]:
            key = state_name or self._state or "idle"
            frames = self.layout_data.get("frames") or {}
            info = frames.get(key) or frames.get("idle") or {}
            bounds = info.get("visible_bounds")
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                sx = self.PET_MAX_WIDTH / max(1, self.PET_SOURCE_SIZE[0])
                sy = self.PET_MAX_HEIGHT / max(1, self.PET_SOURCE_SIZE[1])
                return (
                    int(round(bounds[0] * sx)),
                    int(round(bounds[1] * sy)),
                    int(round(bounds[2] * sx)),
                    int(round(bounds[3] * sy)),
                )
            return (24, 36, self.PET_MAX_WIDTH - 24, self.PET_MAX_HEIGHT - 4)

        def _head_anchor_global(self) -> QPoint:
            left, top, right, _ = self.frame_bounds(self._state)
            head_x = self.x() + (left + right) // 2
            head_y = self.y() + max(8, top - 4)
            return QPoint(head_x, head_y)

        def say(self, text: str, duration_ms: int = 2800):
            """在伴生体头顶弹出轻量状态/对话气泡。"""
            if not self.isVisible():
                return
            self.bubble.show_message(text, self._head_anchor_global(), duration_ms)

        # --- 高清立绘加载与缓存（支持 HiDPI 与 Lanczos 重采样）---
        def _device_pixel_ratio(self) -> float:
            screen = QGuiApplication.primaryScreen()
            dpr = float(screen.devicePixelRatio()) if screen else 1.0
            return max(1.0, min(3.0, round(dpr * 4) / 4))

        def _load_pixmap_and_alpha(self, path: str, flip: bool = False) -> tuple[Optional[QPixmap], Optional[QImage]]:
            path = os.fspath(path)
            if not os.path.exists(path):
                return None, None
            dpr = self._device_pixel_ratio()
            cache_key = (path, flip, dpr)
            alpha_key = (path, flip)

            if cache_key in self._pixmap_cache and alpha_key in self._alpha_cache:
                return self._pixmap_cache[cache_key], self._alpha_cache[alpha_key]

            target_w = max(1, int(round(self.PET_MAX_WIDTH * dpr)))
            target_h = max(1, int(round(self.PET_MAX_HEIGHT * dpr)))

            if Image is not None:
                with Image.open(path) as source:
                    rgba = source.convert("RGBA")
                    if rgba.size == self.PET_SOURCE_SIZE:
                        logical_img = rgba.resize(
                            (self.PET_MAX_WIDTH, self.PET_MAX_HEIGHT),
                            Image.Resampling.LANCZOS,
                        )
                        hidpi_img = (
                            logical_img
                            if dpr == 1.0
                            else rgba.resize((target_w, target_h), Image.Resampling.LANCZOS)
                        )
                    else:
                        bbox = rgba.getchannel("A").getbbox()
                        if bbox:
                            rgba = rgba.crop(bbox)
                        rgba.thumbnail((115, 280), Image.Resampling.LANCZOS)
                        logical_img = Image.new(
                            "RGBA", (self.PET_MAX_WIDTH, self.PET_MAX_HEIGHT), (0, 0, 0, 0)
                        )
                        lx = (self.PET_MAX_WIDTH - rgba.width) // 2
                        ly = self.PET_MAX_HEIGHT - rgba.height
                        logical_img.alpha_composite(rgba, (lx, ly))
                        hidpi_img = (
                            logical_img
                            if dpr == 1.0
                            else logical_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                        )

                    if flip:
                        logical_img = logical_img.transpose(Image.FLIP_LEFT_RIGHT)
                        hidpi_img = hidpi_img.transpose(Image.FLIP_LEFT_RIGHT)

                    raw_logical = logical_img.tobytes("raw", "RGBA")
                    qimg_logical = QImage(
                        raw_logical,
                        logical_img.width,
                        logical_img.height,
                        logical_img.width * 4,
                        QImage.Format.Format_RGBA8888,
                    ).copy()

                    raw_hidpi = hidpi_img.tobytes("raw", "RGBA")
                    qimg_hidpi = QImage(
                        raw_hidpi,
                        hidpi_img.width,
                        hidpi_img.height,
                        hidpi_img.width * 4,
                        QImage.Format.Format_RGBA8888,
                    ).copy()

                    pixmap = QPixmap.fromImage(qimg_hidpi)
                    pixmap.setDevicePixelRatio(dpr)
            else:
                raw_pix = QPixmap(path)
                if raw_pix.isNull():
                    return None, None
                qimg_logical = raw_pix.toImage().scaled(
                    self.PET_MAX_WIDTH,
                    self.PET_MAX_HEIGHT,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if flip:
                    qimg_logical = qimg_logical.mirrored(True, False)
                pixmap = QPixmap.fromImage(
                    raw_pix.toImage().scaled(
                        target_w,
                        target_h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ).mirrored(flip, False)
                )
                pixmap.setDevicePixelRatio(dpr)

            self._pixmap_cache[cache_key] = pixmap
            self._alpha_cache[alpha_key] = qimg_logical
            return pixmap, qimg_logical

        def _reload_asset(self, path: Optional[str]):
            if not path or not os.path.exists(path):
                return
            try:
                pixmap, alpha_img = self._load_pixmap_and_alpha(path, flip=self._flip_current)
                if pixmap is not None:
                    self._current_pixmap = pixmap
                    self._current_alpha_img = alpha_img
                    self._current_asset = path
                    self.update()
            except Exception as exc:
                print(f"[桌宠] 立绘加载失败：{exc}")

        # --- 绘制事件（原生 Per-Pixel Alpha + 内部呼吸偏移）---
        def paintEvent(self, _event):  # noqa: N802 - Qt API
            if self._current_pixmap is None or self._current_pixmap.isNull():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(0, self._breath_offset, self._current_pixmap)
            painter.end()

        # --- 行为调度与状态机 ---
        def _pause_behavior(self):
            self._breath_timer.stop()
            self._breath_offset = 0
            self._blink_timer.stop()
            self._auto_walk_timer.stop()
            self._idle_action_timer.stop()
            self._cancel_state_loop()

        def _resume_behavior(self):
            if not self._visible() or self._drag or self._walking or self._action_sequence:
                return
            self._schedule_idle()
            self._schedule_blink()
            self._schedule_auto_walk()
            self._schedule_idle_action()
            self._start_state_loop(self._base_state)

        def _schedule_idle(self):
            self._breath_timer.stop()
            if self._visible() and not self._drag and not self._walking and not self._action_sequence:
                self._breath_timer.start(self._idle_interval)

        def _idle_step(self):
            if not self._visible() or self._drag or self._walking or self._action_sequence:
                return
            self._idle_phase = (self._idle_phase + 1) % 4
            self._breath_offset = (0, -self._idle_amplitude, 0, self._idle_amplitude)[self._idle_phase]
            self.update()

        def _start_state_loop(self, state: str, timed: bool = True):
            self._cancel_state_loop()
            loop = self.STATE_LOOPS.get(state)
            if not loop or not self._visible():
                return
            frames = [f for f in loop["frames"] if f in self.state_assets]
            if len(frames) < 2:
                return
            self._state_loop_frames = frames
            self._state_loop_interval = loop["interval"]
            self._state_loop_index = 0
            self._state_loop_tick()
            duration = loop.get("duration")
            if duration and timed:
                self._state_loop_then = loop.get("then", "idle")
                self._state_loop_end_timer.start(int(duration))

        def _state_loop_tick(self):
            if not self._visible() or self._drag or self._walking or not self._state_loop_frames:
                return
            idx = self._state_loop_index % len(self._state_loop_frames)
            name = self._state_loop_frames[idx]
            self._state_loop_index += 1
            asset = self.state_assets.get(name)
            if asset and self._current_asset != asset and not self._drag:
                self._reload_asset(asset)
            interval = self._state_loop_interval
            delay = interval[idx % len(interval)] if isinstance(interval, (list, tuple)) else int(interval)
            self._state_loop_timer.start(max(60, int(delay)))

        def _on_state_loop_timeout(self):
            self.set_state(self._state_loop_then)

        def _cancel_state_loop(self):
            self._state_loop_timer.stop()
            self._state_loop_end_timer.stop()
            self._state_loop_frames = []
            self._state_loop_index = 0

        def set_state(self, state: str = "idle"):
            state = state if state in self.STATE_PRESETS else "idle"
            if state == self._base_state and self._state == state:
                return
            self._base_state = state
            self._idle_interval, self._idle_amplitude = self.STATE_PRESETS[state]
            if self._drag:
                return
            self._cancel_action()
            self._cancel_walk()
            self._pause_behavior()
            self._state = self._base_state
            selected = self.state_assets.get(self._state, self.asset_path)
            if selected != self._current_asset or self._flip_current:
                self._flip_current = False
                self._reload_asset(selected)
            if self._visible():
                self._resume_behavior()

        def play_action(self, action: str, duration_ms: int = 1200):
            self.play_sequence([(action, duration_ms)])

        def play_sequence(self, actions: list[tuple[str, int]]):
            sequence = [
                (name, int(duration))
                for name, duration in actions
                if name in self.state_assets and int(duration) > 0
            ]
            if not sequence or not self._visible() or self._drag:
                return
            self._cancel_action()
            self._cancel_walk()
            self._pause_behavior()
            self._action_sequence = sequence
            self._action_sequence_index = 0
            self._run_action_sequence()

        def _run_action_sequence(self):
            if not self._visible() or self._drag:
                self._cancel_action()
                return
            if not self._action_sequence or self._action_sequence_index >= len(self._action_sequence):
                self._restore_base_state()
                return
            action, duration = self._action_sequence[self._action_sequence_index]
            self._action_sequence_index += 1
            self._cancel_state_loop()
            self._state = action
            self._flip_current = False
            self._reload_asset(self.state_assets.get(action, self.asset_path))
            self._start_state_loop(action, timed=False)
            if self._visible():
                self._action_timer.start(max(40, int(duration)))

        def _restore_base_state(self):
            self._cancel_action()
            self._idle_action_active = False
            self._cancel_state_loop()
            self._state = self._base_state
            self._flip_current = False
            self._reload_asset(self.state_assets.get(self._base_state, self.asset_path))
            self._resume_behavior()

        def _cancel_action(self):
            self._action_timer.stop()
            self._action_sequence = None
            self._action_sequence_index = 0

        # --- 眨眼与随机待机小动作 ---
        def _schedule_blink(self):
            self._blink_timer.stop()
            if self._visible() and self._base_state == "idle" and "blink" in self.state_assets:
                self._blink_timer.start(random.randint(11000, 20000))

        def _blink_step(self):
            if not self._visible():
                return
            if self._state == self._base_state == "idle" and not self._drag and not self._walking:
                if "blink_half" in self.state_assets:
                    self.play_sequence([("blink_half", 90), ("blink", 140), ("blink_half", 90)])
                else:
                    self.play_action("blink", 380)
            self._schedule_blink()

        def _schedule_idle_action(self):
            self._idle_action_timer.stop()
            if self._visible() and self._base_state in ("idle", "music", "tired"):
                self._idle_action_timer.start(random.randint(18000, 34000))

        def _idle_action_step(self):
            if not self._visible():
                return
            if (
                self._drag
                or self._walking
                or self._idle_action_active
                or self._action_sequence
                or self._base_state not in ("idle", "music", "tired")
            ):
                self._schedule_idle_action()
                return
            if self._base_state == "tired":
                if "tired_rub" in self.state_assets:
                    self._idle_action_active = True
                    self.play_sequence([("tired_rub", 2600)])
                else:
                    self._schedule_idle_action()
                return

            choices = ["sit", "stretch"]
            if "drink" in self.state_assets:
                choices.append("drink")
            action = random.choice(choices)
            if action == "drink":
                self._idle_action_active = True
                self.play_sequence([("drink", random.randint(5000, 8000))])
            elif action == "sit" and "sit" in self.state_assets:
                duration = random.randint(8000, 14000)
                sequence = [("sit", duration)]
                if "sit_stretch" in self.state_assets and random.random() < 0.5:
                    sequence = [("sit", duration - 2500), ("sit_stretch", 2500)]
                self._idle_action_active = True
                self.play_sequence(sequence)
            elif action == "stretch" and "stretch" in self.state_assets:
                self._idle_action_active = True
                self.play_sequence(
                    [
                        ("stretch_up", 750),
                        ("stretch", 2300),
                        ("stretch_up", 750),
                    ]
                )
            else:
                self._schedule_idle_action()

        # --- 30fps 平滑行走 ---
        def start_walk(self, target_x: int, target_y: int):
            if not self._visible() or self._locked or self._drag or self._base_state == "sleep":
                return
            self._cancel_action()
            self._cancel_walk()
            self._pause_behavior()
            self._walking = True
            tx, ty = self._clamp_position(target_x, target_y)
            self._walk_target = (tx, ty)
            self._walk_pos = (float(self._pos[0]), float(self._pos[1]))
            self._flip_current = tx < self._pos[0]
            self._walk_frame = 0
            self._walk_frame_accum_ms = 0
            self._apply_walk_frame()
            self._walk_timer.start()

        def _apply_walk_frame(self):
            frame_key = f"side_walk_{self._walk_frame + 1}"
            if frame_key not in self.state_assets:
                frame_key = f"walk_{self._walk_frame + 1}"
            self._state = frame_key
            asset = self.state_assets.get(frame_key, self.asset_path)
            self._reload_asset(asset)

        def _walk_step(self):
            if not self._visible() or not self._walking or not self._walk_pos or not self._walk_target:
                self._cancel_walk()
                return
            cx, cy = self._walk_pos
            tx, ty = self._walk_target
            dx, dy = tx - cx, ty - cy
            dist = (dx * dx + dy * dy) ** 0.5
            step_px = self._walk_speed_px_per_sec * (self._walk_tick_ms / 1000.0)

            if dist <= step_px:
                self._pos = [tx, ty]
                self.move(tx, ty)
                if self.bubble.isVisible():
                    self.bubble.move(
                        self._head_anchor_global().x() - self.bubble.width() // 2,
                        max(8, self._head_anchor_global().y() - self.bubble.height() - 4),
                    )
                self._finish_walk()
                return

            nx = cx + (dx / dist) * step_px
            ny = cy + (dy / dist) * step_px
            self._walk_pos = (nx, ny)
            ix, iy = round(nx), round(ny)
            self._pos = [ix, iy]
            self.move(ix, iy)
            if self.bubble.isVisible():
                self.bubble.move(
                    self._head_anchor_global().x() - self.bubble.width() // 2,
                    max(8, self._head_anchor_global().y() - self.bubble.height() - 4),
                )

            self._walk_frame_accum_ms += self._walk_tick_ms
            if self._walk_frame_accum_ms >= self._walk_frame_interval_ms:
                self._walk_frame_accum_ms = 0
                self._walk_frame = (self._walk_frame + 1) % 4
                self._apply_walk_frame()

        def _finish_walk(self):
            self._walk_timer.stop()
            self._walking = False
            self._walk_target = None
            self._walk_pos = None
            self._flip_current = False
            self._idle_origin = (int(self._pos[0]), int(self._pos[1]))
            self._save_position()
            self._restore_base_state()

        def _cancel_walk(self):
            self._walk_timer.stop()
            if self._walking:
                self._idle_origin = (int(self._pos[0]), int(self._pos[1]))
            self._walking = False
            self._walk_target = None
            self._walk_pos = None
            self._flip_current = False

        def _schedule_auto_walk(self):
            self._auto_walk_timer.stop()
            if self._visible() and not self._locked and self._base_state == "idle":
                self._auto_walk_timer.start(random.randint(22000, 45000))

        def _auto_walk_step(self):
            if not self._visible():
                return
            if (
                self._locked
                or self._drag
                or self._walking
                or self._action_sequence
                or self._idle_action_active
                or self._base_state != "idle"
            ):
                self._schedule_auto_walk()
                return
            tx = int(self._pos[0]) + random.choice((-1, 1)) * random.randint(70, 170)
            ty = int(self._pos[1])
            self.start_walk(tx, ty)

        # --- 鼠标点击、拖拽與右键菜单 ---
        def _is_opaque_at(self, local_pos: QPoint) -> bool:
            """检查点击位置是否落在人物可见像素或包围盒内，防止点击大片透明边缘误触。"""
            x, y = local_pos.x(), local_pos.y()
            if self._current_alpha_img is not None and not self._current_alpha_img.isNull():
                if 0 <= x < self._current_alpha_img.width() and 0 <= y < self._current_alpha_img.height():
                    if self._current_alpha_img.pixelColor(x, y).alpha() > 16:
                        return True
            left, top, right, bottom = self.frame_bounds(self._state)
            return left <= x <= right and top <= y <= bottom

        def mousePressEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
            if event.button() == Qt.MouseButton.LeftButton:
                gpos = event.globalPosition().toPoint()
                self._pause_behavior()
                self._cancel_action()
                self._cancel_walk()
                self._drag = (gpos.x(), gpos.y(), int(self._pos[0]), int(self._pos[1]))
                self._press_pos = (gpos.x(), gpos.y())
                self._did_move = False
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
            if not self._drag or self._locked:
                return
            gpos = event.globalPosition().toPoint()
            sx, sy, wx, wy = self._drag
            if self._press_pos:
                self._did_move = self._did_move or (
                    abs(gpos.x() - self._press_pos[0]) > 4
                    or abs(gpos.y() - self._press_pos[1]) > 4
                )
            if not self._did_move:
                return
            if "drag" in self.state_assets and self._base_state != "sleep" and self._state != "drag":
                self._state = "drag"
                self._flip_current = False
                self._reload_asset(self.state_assets["drag"])
                if random.random() < 0.35:
                    self.say(random.choice(INTERACTION_LINES["drag"]), 1800)

            nx, ny = self._clamp_position(wx + gpos.x() - sx, wy + gpos.y() - sy)
            self._pos = [nx, ny]
            self.move(nx, ny)
            if self.bubble.isVisible():
                self.bubble.move(
                    self._head_anchor_global().x() - self.bubble.width() // 2,
                    max(8, self._head_anchor_global().y() - self.bubble.height() - 4),
                )
            event.accept()

        def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
            if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
                was_click = not self._did_move
                self._drag = None
                self._press_pos = None
                self._did_move = False
                self._idle_origin = (int(self._pos[0]), int(self._pos[1]))

                if was_click:
                    if self._base_state == "sleep":
                        self.set_state("idle")
                        self.say(random.choice(INTERACTION_LINES["wake"]), 2400)
                    elif "surprised" in self.state_assets:
                        self.play_sequence([("surprised", 600), ("happy", 450)])
                        self.say(random.choice(INTERACTION_LINES["click"]), 2400)
                    else:
                        self._restore_base_state()
                    if self.on_interact:
                        self.on_interact(self._base_state)
                else:
                    self._restore_base_state()
                self._save_position()
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
            if event.button() == Qt.MouseButton.LeftButton:
                if "happy" in self.state_assets:
                    self.play_action("happy", 900)
                if self.on_click:
                    self.on_click()
                event.accept()
                return
            super().mouseDoubleClickEvent(event)

        def keyPressEvent(self, event: QKeyEvent):  # noqa: N802 - Qt API
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                event.accept()
                return
            super().keyPressEvent(event)

        def contextMenuEvent(self, event: QContextMenuEvent):  # noqa: N802 - Qt API
            menu = QMenu(self)
            menu.setStyleSheet(
                """
                QMenu {
                    background-color: #202220;
                    color: #f4eee4;
                    border: 1px solid rgba(150, 172, 143, 0.55);
                    border-radius: 10px;
                    padding: 6px;
                    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
                    font-size: 9.5pt;
                }
                QMenu::item {
                    padding: 6px 22px 6px 14px;
                    border-radius: 6px;
                }
                QMenu::item:selected {
                    background-color: #96ac8f;
                    color: #202220;
                    font-weight: bold;
                }
                QMenu::separator {
                    height: 1px;
                    background: rgba(255, 255, 255, 0.14);
                    margin: 5px 8px;
                }
                """
            )

            chat_act = menu.addAction("和伴生体说话")
            chat_act.triggered.connect(self._run_context("chat"))

            walk_act = menu.addAction("让他走一走")
            walk_act.triggered.connect(self._run_context("walk"))

            if "drink" in self.state_assets:
                drink_act = menu.addAction("递给他一杯热饮")
                drink_act.triggered.connect(self._trigger_drink)

            music_label = "停止听歌" if self._base_state == "music" else "陪他听会儿歌"
            music_act = menu.addAction(music_label)
            music_act.triggered.connect(self._toggle_music)

            stretch_act = menu.addAction("让他伸个懒腰")
            stretch_act.triggered.connect(
                lambda: self.play_sequence(
                    [("stretch_up", 700), ("stretch", 2100), ("stretch_up", 700)]
                )
            )

            world_act = menu.addAction("让他回小世界")
            world_act.triggered.connect(self._run_context("small_world"))

            sleep_label = "叫醒他" if self._base_state == "sleep" else "让他休息"
            sleep_act = menu.addAction(sleep_label)
            sleep_act.triggered.connect(self._toggle_sleep)

            menu.addSeparator()

            lock_act = menu.addAction("锁定当前位置")
            lock_act.setCheckable(True)
            lock_act.setChecked(self._locked)
            lock_act.triggered.connect(self._toggle_lock)

            reset_act = menu.addAction("恢复默认位置")
            reset_act.triggered.connect(self._reset_position)

            hide_act = menu.addAction("隐藏桌宠")
            hide_act.triggered.connect(self.hide)

            menu.exec(event.globalPos())

        def _run_context(self, name: str):
            def _invoke():
                cb = self.context_actions.get(name)
                if cb:
                    cb()

            return _invoke

        def _trigger_drink(self):
            self.play_sequence([("drink", 6500)])
            self.say(random.choice(INTERACTION_LINES["drink"]), 2800)

        def _toggle_music(self):
            if self._base_state == "music":
                self.set_state("idle")
            else:
                self.set_state("music")
                self.say(random.choice(INTERACTION_LINES["music"]), 2600)

        def _toggle_sleep(self):
            if self._base_state == "sleep":
                self.set_state("idle")
                self.say(random.choice(INTERACTION_LINES["wake"]), 2400)
            else:
                cb = self.context_actions.get("sleep")
                if cb:
                    cb()
                else:
                    self.set_state("sleep")
                self.say(random.choice(INTERACTION_LINES["sleep"]), 2600)

        def _toggle_lock(self):
            self._locked = not self._locked
            self._cancel_walk()
            self._restore_base_state()
            self._save_position()
            cb = self.context_actions.get("lock")
            if cb:
                cb(self._locked)

        def _reset_position(self):
            _, _, right, bottom = self._screen_bounds()
            x, y = self._clamp_position(
                right - self.PET_MAX_WIDTH - 36,
                bottom - self.PET_MAX_HEIGHT - 48,
            )
            self._pos = [x, y]
            self._idle_origin = (x, y)
            self.move(x, y)
            self._save_position()

        # --- 显示 / 隐藏 / 销毁 ---
        def show_pet(self) -> bool:
            if not os.path.exists(self.asset_path):
                return False
            self._state = self._base_state
            self._flip_current = False
            self._reload_asset(self.state_assets.get(self._base_state, self.asset_path))
            self.move(int(self._pos[0]), int(self._pos[1]))
            super().show()
            self.raise_()
            self._idle_origin = (int(self._pos[0]), int(self._pos[1]))
            self._resume_behavior()
            return True

        def hide(self):  # noqa: A003 - Qt API
            self._pause_behavior()
            self._cancel_action()
            self._cancel_walk()
            self._drag = None
            self._press_pos = None
            self._did_move = False
            self._state = self._base_state
            self.bubble.hide()
            super().hide()

        def destroy_pet(self):
            self.hide()
            self.bubble.close()
            self.close()


class _HeadlessPetFallback:
    """当运行在无 GUI / 无 PySide6 的纯命令行测试环境时使用的轻量状态机后备。"""

    PET_MAX_WIDTH = DesktopPet.PET_MAX_WIDTH
    PET_MAX_HEIGHT = DesktopPet.PET_MAX_HEIGHT

    def __init__(self, state_assets: dict[str, str], position_path: str):
        self.state_assets = dict(state_assets)
        self.position_path = position_path
        self.layout_data = load_pet_layout(LAYOUT_PATH)
        self._locked = False
        self._state = "idle"
        self._base_state = "idle"
        self._pos = [1200, 680]
        self._is_shown = False
        self.window = self

    def winfo_exists(self) -> bool:
        return self._is_shown

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def state(self) -> str:
        return "normal" if self._is_shown else "withdrawn"

    def _visible(self) -> bool:
        return self._is_shown

    def show_pet(self) -> bool:
        self._is_shown = True
        return True

    def hide(self):
        self._is_shown = False

    def set_state(self, state: str = "idle"):
        self._base_state = state if state in DesktopPet.STATE_PRESETS or state == "drink" else "idle"
        self._state = self._base_state

    def play_action(self, action: str, duration_ms: int = 1200):
        if action in self.state_assets:
            self._state = action

    def play_sequence(self, sequence):
        for name, _ in sequence:
            if name in self.state_assets:
                self._state = name
                break

    def start_walk(self, target_x: int, target_y: int):
        if not self._locked and self._is_shown:
            self._pos = [int(target_x), int(target_y)]

    def _reset_position(self):
        self._pos = [1920 - self.PET_MAX_WIDTH - 36, 1080 - self.PET_MAX_HEIGHT - 48]

    def say(self, text: str, duration_ms: int = 2800):
        pass

    def destroy_pet(self):
        self._is_shown = False


class PySideDesktopPet(QObject):
    """PySide6 桌宠控制器（直接管理原生 Qt 桌宠窗口，零 Tkinter 依赖与零轮询开销）。"""

    def __init__(
        self,
        chen=None,
        on_chat: Callable[[], None] | None = None,
        on_interact: Callable[[str], None] | None = None,
        parent=None,
    ):
        if PYSIDE6_AVAILABLE:
            super().__init__(parent)
        self.chen = chen
        self._on_chat = on_chat
        self._on_interact = on_interact
        assets = all_pet_asset_map()

        if PYSIDE6_AVAILABLE and QApplication.instance() is not None:
            self.pet = NativeQtPetWindow(
                assets["idle"],
                on_click=self._chat,
                state_assets=assets,
                context_actions={
                    "chat": self._chat,
                    "walk": self.walk,
                    "small_world": self.return_to_world,
                    "sleep": self.sleep,
                    "reset_position": self.reset_position,
                },
                position_path=str(POSITION_PATH),
                on_interact=self._interact,
                layout_path=str(LAYOUT_PATH),
            )
        else:
            self.pet = _HeadlessPetFallback(assets, str(POSITION_PATH))

    @property
    def window(self):
        """暴露底层窗口对象供 chen.runtime 状态诊断。"""
        return self.pet.window

    def _chat(self):
        if self._on_chat:
            self._on_chat()

    def _interact(self, state: str):
        """把桌宠轻互动交给上层，避免桌宠自己维护第二套关系状态。"""
        if self._on_interact:
            self._on_interact(str(state or "idle"))

    def show(self):
        try:
            if self.chen is not None and hasattr(self.chen, "get_current_world"):
                if self.chen.get_current_world() == "small_world":
                    self.pet.hide()
                    return False
        except Exception:
            pass
        return self.pet.show_pet()

    def hide(self):
        self.pet.hide()

    def return_to_world(self):
        try:
            if self.chen is not None and hasattr(self.chen, "enter_small_world"):
                self.chen.enter_small_world()
            runtime = getattr(self.chen, "runtime", None)
            if runtime and hasattr(runtime, "emit"):
                runtime.emit("desktop_pet_world_return", {
                    "source": "desktop_pet",
                    "world": "small_world",
                })
            relationship = getattr(self.chen, "relationship", None)
            if relationship and hasattr(relationship, "record_consciousness_link"):
                relationship.record_consciousness_link(
                    "world_visit", detail="从桌宠回到小世界"
                )
            day_log = getattr(self.chen, "day_log", None)
            if day_log and hasattr(day_log, "log"):
                day_log.log("world", "桌宠回到小世界", world="small_world",
                            extra={"source": "desktop_pet"})
        finally:
            self.hide()

    def toggle(self):
        if self.pet._visible():
            self.hide()
        else:
            self.show()

    def walk(self):
        if not self.pet._visible() or self.pet._locked:
            return
        x, y = self.pet._pos or (0, 0)
        sw = self.pet.window.winfo_screenwidth()
        target = max(
            20,
            min(
                sw - self.pet.PET_MAX_WIDTH - 20,
                x - 180 if x > sw // 2 else x + 180,
            ),
        )
        self.pet.set_state("idle")
        self.pet.start_walk(target, y)
        self.pet.say(random.choice(INTERACTION_LINES["walk"]), 2200)

    def sleep(self):
        if self.pet._visible():
            self.pet.set_state("sleep")

    def reset_position(self):
        if self.pet.window and self.pet.window.winfo_exists():
            self.pet._reset_position()

    def set_state(self, state: str = "idle"):
        self.pet.set_state(state)

    def play_action(self, action: str, duration_ms: int = 1200):
        self.pet.play_action(action, duration_ms)

    def play_sequence(self, sequence):
        self.pet.play_sequence(sequence)

    def say(self, text: str, duration_ms: int = 2800):
        """让桌宠在头顶弹出气泡说话。"""
        self.pet.say(text, duration_ms)

    def destroy(self):
        self.pet.destroy_pet()
