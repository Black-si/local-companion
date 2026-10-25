"""轻量桌宠核心状态机与渲染实现：支持 layout.json 基准对齐、平滑行走与安全回退。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import random

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - 允许无 Tk 环境导入模块
    tk = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LAYOUT_PATH = ROOT / "assets" / "pet" / "layout.json"


def load_pet_layout(layout_path: str | os.PathLike | None = None) -> dict:
    """读取 assets/pet/layout.json 的画布基准、头宽与各帧包围盒信息。"""
    target = Path(layout_path) if layout_path else DEFAULT_LAYOUT_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {
        "schema_version": 2,
        "canvas_size": [1008, 1920],
        "display_size": [168, 320],
        "head_width": 666,
        "ground": 1896,
        "frames": {},
    }


class DesktopPet:
    # 统一画布保留伸展/坐姿的空间；待机人物仍约 113x277。
    PET_MAX_WIDTH = 168
    PET_MAX_HEIGHT = 320
    PET_SOURCE_SIZE = (1008, 1920)

    # 状态内循环帧配置：frames=循环的状态名，interval=切换间隔，
    # duration=整个循环持续多久（到时切到 then 指定的状态）。
    STATE_LOOPS = {
        "music": {"frames": ["music", "music_2"], "interval": [1800, 450]},
        "world_return": {
            "frames": ["world_return", "world_return_2"],
            "interval": 460,
            "duration": 3400,
            "then": "idle",
        },
    }

    STATE_PRESETS = {
        "music": (1100, 2),
        "sit": (1600, 1),
        "stretch": (1400, 2),
        "tired": (2800, 1),
        # 睡觉也保留很轻的呼吸起伏，避免像卡死；频率最低。
        "sleep": (5000, 1),
        "world_return": (1400, 2),
        "idle": (1800, 1),
        "blink": (1800, 1),
        "surprised": (1200, 2),
        "happy": (1200, 2),
    }

    def __init__(
        self,
        root,
        asset_path,
        on_click=None,
        state_assets=None,
        context_actions=None,
        position_path=None,
        on_interact=None,
        layout_path=None,
    ):
        self.root = root
        self.asset_path = asset_path
        self.on_click = on_click
        self.on_interact = on_interact
        self.state_assets = dict(state_assets or {})
        self.context_actions = dict(context_actions or {})
        self.position_path = position_path
        self.layout_data = load_pet_layout(layout_path)

        display_size = self.layout_data.get("display_size")
        if isinstance(display_size, (list, tuple)) and len(display_size) == 2:
            self.PET_MAX_WIDTH = int(display_size[0])
            self.PET_MAX_HEIGHT = int(display_size[1])

        canvas_size = self.layout_data.get("canvas_size")
        if isinstance(canvas_size, (list, tuple)) and len(canvas_size) == 2:
            self.PET_SOURCE_SIZE = (int(canvas_size[0]), int(canvas_size[1]))

        self._locked = False
        self._context_menu = None
        self.window = None
        self._photo = None
        self._drag = None
        self._idle_job = None
        self._idle_phase = 0
        self._idle_origin = None
        self._native_layered = False
        self._canvas = None
        self._canvas_image_id = None
        self._interaction_widget = None
        self._current_asset = None
        self._image_cache = {}
        self._state = "idle"
        self._base_state = "idle"
        self._idle_interval = 1800
        self._idle_amplitude = 1
        self._action_job = None
        self._action_sequence = None
        self._action_sequence_index = 0
        self._blink_job = None
        self._walk_job = None
        self._walk_target = None
        self._walk_frame = 0
        self._flip_current = False  # 侧身走路朝向：False=朝右原图，True=朝左水平翻转
        self._walk_pos = None       # 走路权威位置（不依赖可能滞后的 winfo）
        self._walk_image = None     # 当前走路帧图像（含朝向翻转）
        self._pos = None            # 窗口权威位置 [x,y]，layered 窗口 winfo 不可靠
        self._auto_walk_job = None
        self._walking = False
        self._walk_step_ms = 80
        self._walk_pixels = 6
        self._idle_action_job = None
        self._idle_action_active = False
        self._press_pos = None
        self._did_move = False
        # 状态内循环帧（听音乐律动、挥手等）
        self._state_loop_job = None
        self._state_loop_end_job = None
        self._state_loop_index = 0

    def frame_bounds(self, state_name: str | None = None) -> tuple[int, int, int, int]:
        """根据 layout.json 返回指定状态在显示尺寸下的可见像素边界 (left, top, right, bottom)。"""
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

    def _visible(self):
        return bool(
            self.window
            and self.window.winfo_exists()
            and self.window.state() != "withdrawn"
        )

    def _cancel_job(self, attribute):
        job = getattr(self, attribute, None)
        if job and self.window and self.window.winfo_exists():
            try:
                self.window.after_cancel(job)
            except Exception:
                pass
        setattr(self, attribute, None)

    def _pause_behavior(self):
        self._cancel_idle()
        self._cancel_job("_blink_job")
        self._cancel_auto_walk()
        self._cancel_idle_action()
        self._cancel_state_loop()

    def _resume_behavior(self):
        if not self._visible() or self._drag or self._walking or self._action_sequence:
            return
        self._schedule_idle()
        self._schedule_blink()
        self._schedule_auto_walk()
        self._schedule_idle_action()
        self._start_state_loop(self._base_state)

    def _start_state_loop(self, state, timed=True):
        self._cancel_state_loop()
        loop = self.STATE_LOOPS.get(state)
        if not loop or not self._visible():
            return
        frames = [f for f in loop["frames"] if f in self.state_assets]
        if len(frames) < 2:
            return
        self._state_loop_index = 0
        self._state_loop_tick(frames, loop["interval"])
        duration = loop.get("duration")
        if duration and timed:
            self._state_loop_end_job = self.window.after(
                duration, lambda: self.set_state(loop.get("then", "idle"))
            )

    def _state_loop_tick(self, frames, interval):
        if not self._visible() or self._drag or self._walking:
            return
        index = self._state_loop_index % len(frames)
        name = frames[index]
        self._state_loop_index += 1
        asset = self.state_assets.get(name)
        if asset and self._current_asset != asset and not self._drag:
            self._reload_asset(asset)
        delay = (
            interval[index % len(interval)]
            if isinstance(interval, (list, tuple))
            else interval
        )
        self._state_loop_job = self.window.after(
            delay, lambda: self._state_loop_tick(frames, interval)
        )

    def _cancel_state_loop(self):
        for job_attr in ("_state_loop_job", "_state_loop_end_job"):
            self._cancel_job(job_attr)
        self._state_loop_index = 0

    def _load_position(self, default_x, default_y, width, height):
        if self.position_path:
            try:
                with open(self.position_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return self._clamp_position(default_x, default_y, width, height)
                x, y = int(data.get("x", default_x)), int(data.get("y", default_y))
                self._locked = data.get("locked") is True
                return self._clamp_position(x, y, width, height)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return self._clamp_position(default_x, default_y, width, height)

    def _screen_bounds(self):
        if os.name == "nt":
            try:
                rect = wintypes.RECT()
                get_area = ctypes.windll.user32.SystemParametersInfoW
                get_area.argtypes = [
                    wintypes.UINT,
                    wintypes.UINT,
                    ctypes.c_void_p,
                    wintypes.UINT,
                ]
                get_area.restype = wintypes.BOOL
                if get_area(0x30, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right, rect.bottom
            except (AttributeError, OSError):
                pass
        return 0, 0, self.window.winfo_screenwidth(), self.window.winfo_screenheight()

    def _clamp_position(self, x, y, width=None, height=None):
        left, top, right, bottom = self._screen_bounds()
        width = width or self.window.winfo_width() or self.PET_MAX_WIDTH
        height = height or self.window.winfo_height() or self.PET_MAX_HEIGHT
        return (
            round(max(left, min(x, max(left, right - width)))),
            round(max(top, min(y, max(top, bottom - height)))),
        )

    def _save_position(self):
        if not self.position_path or not self.window or not self.window.winfo_exists():
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

    def set_state(self, state="idle"):
        """设置低成本的桌宠状态，影响待机节奏，不启动额外模型。"""
        state = state if state in self.STATE_PRESETS else "idle"
        # 周期性状态刷新不能打断正在进行的点击、眨眼或坐下动作。
        if state == self._base_state:
            return
        self._base_state = state
        self._idle_interval, self._idle_amplitude = self.STATE_PRESETS[state]
        if self._drag:
            return  # 拖动结束后恢复最新状态。
        self._cancel_action()
        self._cancel_walk()
        self._pause_behavior()
        self._state = self._base_state
        selected = self.state_assets.get(self._state, self.asset_path)
        if self.window and self.window.winfo_exists() and selected != self._current_asset:
            self._reload_asset(selected)
        if self.window and self.window.winfo_exists() and self.window.state() != "withdrawn":
            self._resume_behavior()

    def play_action(self, action, duration_ms=1200):
        """播放一次短动作，结束后回到当前基础状态。"""
        self.play_sequence([(action, duration_ms)])

    def play_sequence(self, actions):
        """按顺序播放动作列表，列表元素为 (动作名, 持续毫秒)。"""
        sequence = [
            (name, int(duration))
            for name, duration in actions
            if name in self.state_assets and int(duration) > 0
        ]
        if not sequence:
            return
        if not self._visible() or self._drag:
            return
        self._cancel_action()
        self._cancel_walk()
        self._pause_behavior()
        self._action_sequence = sequence
        self._action_sequence_index = 0
        self._run_action_sequence()

    def _run_action_sequence(self):
        self._action_job = None
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
        if self.window and self.window.winfo_exists():
            self._reload_asset(self.state_assets[action])
            self._start_state_loop(action, timed=False)
            if self.window.state() != "withdrawn":
                self._action_job = self.window.after(duration, self._run_action_sequence)

    def _restore_base_state(self):
        self._cancel_action()
        self._idle_action_active = False
        self._cancel_state_loop()
        self._state = self._base_state
        selected = self.state_assets.get(self._base_state, self.asset_path)
        if self.window and self.window.winfo_exists():
            self._reload_asset(selected)
        self._resume_behavior()

    def _cancel_action(self):
        self._cancel_job("_action_job")
        self._action_sequence = None
        self._action_sequence_index = 0

    # ===== 走动 =====
    def start_walk(self, target_x, target_y):
        """走到屏幕内的目标位置；睡眠、锁定、拖动时不走动。"""
        if not self._visible() or self._locked or self._drag or self._base_state == "sleep":
            return
        self._cancel_action()
        self._cancel_walk()
        self._pause_behavior()
        self._walking = True
        self._walk_target = self._clamp_position(target_x, target_y)
        self._walk_pos = tuple(self._pos)
        # 侧身帧朝右；目标在当前位置左侧时水平翻转得到朝左
        self._flip_current = target_x < self._walk_pos[0]
        self._walk_frame = 0
        self._walk_image = None
        self._walk_step()

    def _load_walk_image(self):
        asset = self.state_assets.get(f"side_walk_{self._walk_frame + 1}") or self.asset_path
        image = self._load_image(asset)
        if self._flip_current and image is not None:
            image = image.transpose(Image.FLIP_LEFT_RIGHT)
        self._current_asset = asset
        return image

    def _place_walk(self, x, y):
        asset = self.state_assets.get(f"side_walk_{self._walk_frame + 1}")
        # 修复原版空指针 Bug：当 _walk_image 为 None 时也必须加载
        if self._walk_image is None or (asset and asset != self._current_asset):
            self._walk_image = self._load_walk_image()
        if self._native_layered and self._walk_image is not None:
            # 位置统一交给 UpdateLayeredWindow，不再 geometry，避免两者冲突漂移
            self._native_layered = self._win32_layered_window(
                self.window, self._walk_image, at_pos=(x, y)
            )
        if not self._native_layered:
            # 修复原版回退模式不更新画布帧的 Bug
            if self._walk_image is not None:
                self._render_fallback(self._walk_image)
            self.window.geometry(f"+{x}+{y}")

    def _walk_step(self):
        self._walk_job = None
        if not self._visible() or not self._walking:
            return
        cx, cy = self._walk_pos
        tx, ty = self._walk_target
        dx, dy = tx - cx, ty - cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= self._walk_pixels:
            self._walk_pos = (tx, ty)
            self._place_walk(tx, ty)
            self._finish_walk()
            return
        nx = cx + round(dx / dist * self._walk_pixels)
        ny = cy + round(dy / dist * self._walk_pixels)
        self._walk_pos = (nx, ny)
        self._pos = [nx, ny]
        self._state = f"side_walk_{self._walk_frame + 1}"
        self._place_walk(nx, ny)
        self._walk_frame = (self._walk_frame + 1) % 4
        self._walk_job = self.window.after(self._walk_step_ms, self._walk_step)

    def _finish_walk(self):
        self._walking = False
        self._walk_job = None
        self._walk_target = None
        self._flip_current = False
        self._walk_image = None
        tx, ty = self._walk_pos
        # 同步权威位置与窗口位置到终点
        self._pos = [tx, ty]
        self.window.geometry(f"+{tx}+{ty}")
        self.window.update_idletasks()
        self._idle_origin = (tx, ty)
        self._save_position()
        self._restore_base_state()

    def _cancel_walk(self):
        self._cancel_job("_walk_job")
        if self._walking and self.window and self.window.winfo_exists():
            self._idle_origin = tuple(self._pos)
        self._walking = False
        self._walk_target = None
        self._flip_current = False
        self._walk_image = None

    def _schedule_auto_walk(self):
        """随机延迟后自动走一段；只在idle且非拖动时触发。"""
        self._cancel_auto_walk()
        if not self._visible() or self._locked or self._base_state != "idle":
            return
        delay = random.randint(20000, 45000)
        self._auto_walk_job = self.window.after(delay, self._auto_walk_step)

    def _auto_walk_step(self):
        self._auto_walk_job = None
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
        # 只在当前位置附近散步，避免每次横穿整个屏幕。
        tx = self._pos[0] + random.choice((-1, 1)) * random.randint(60, 160)
        ty = self._pos[1]
        self.start_walk(tx, ty)

    def _cancel_auto_walk(self):
        self._cancel_job("_auto_walk_job")

    # ===== 随机空闲动作（坐下/伸懒腰）=====
    def _schedule_idle_action(self):
        self._cancel_idle_action()
        if not self._visible() or self._base_state not in ("idle", "music", "tired"):
            return
        delay = random.randint(18000, 35000)
        self._idle_action_job = self.window.after(delay, self._idle_action_step)

    def _idle_action_step(self):
        self._idle_action_job = None
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
        # 疲惫时：抬手揉一揉眼睛，再恢复困倦站立
        if self._base_state == "tired":
            if "tired_rub" in self.state_assets:
                self.play_sequence([("tired_rub", 2600)])
                self._idle_action_active = True
            else:
                self._schedule_idle_action()
            return
        action = random.choice(["sit", "stretch"])
        if action == "sit" and "sit" in self.state_assets:
            duration = random.randint(8000, 14000)
            sequence = [("sit", duration)]
            if "sit_stretch" in self.state_assets and random.random() < 0.5:
                sequence = [("sit", duration - 2500), ("sit_stretch", 2500)]
            self.play_sequence(sequence)
            self._idle_action_active = True
        elif action == "stretch" and "stretch" in self.state_assets:
            # 完整伸懒腰：手举一半 → 完全伸展打哈欠 → 手收一半 → 回位
            self.play_sequence(
                [
                    ("stretch_up", 750),
                    ("stretch", 2300),
                    ("stretch_up", 750),
                ]
            )
            self._idle_action_active = True
        else:
            self._schedule_idle_action()

    def _cancel_idle_action(self):
        self._cancel_job("_idle_action_job")
        self._idle_action_active = False

    def _schedule_blink(self):
        self._cancel_job("_blink_job")
        if self._visible() and self._base_state == "idle" and "blink" in self.state_assets:
            # 待机时随机 12–22 秒眨一次，避免固定节奏显得机械。
            self._blink_job = self.window.after(
                random.randint(12000, 22000), self._blink_step
            )

    def _blink_step(self):
        self._blink_job = None
        if not self.window or not self.window.winfo_exists() or self.window.state() == "withdrawn":
            return
        if self._state == self._base_state == "idle" and not self._drag and not self._walking:
            self._blink_animation()
        self._schedule_blink()

    def _blink_animation(self):
        """三帧眨眼：半闭→全闭→半闭→恢复，共约350ms。"""
        if "blink_half" not in self.state_assets:
            self.play_action("blink", 450)
            return
        self.play_sequence([("blink_half", 100), ("blink", 150), ("blink_half", 100)])

    def _load_image(self, path):
        if Image is None:
            return None
        path = os.fspath(path)
        stat = os.stat(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._image_cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1].copy()
        with Image.open(path) as source:
            image = source.convert("RGBA")
            # 新素材已经统一人物尺度和基线。再次按各帧 alpha 裁边会破坏注册。
            if image.size == self.PET_SOURCE_SIZE:
                canvas = image.resize(
                    (self.PET_MAX_WIDTH, self.PET_MAX_HEIGHT),
                    Image.Resampling.LANCZOS,
                )
                self._image_cache[path] = (signature, canvas.copy())
                return canvas
            # 保留自定义/旧素材的兼容路径，避免把备用立绘放大。
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
            image.thumbnail((115, 280), Image.Resampling.LANCZOS)
            canvas = Image.new(
                "RGBA", (self.PET_MAX_WIDTH, self.PET_MAX_HEIGHT), (0, 0, 0, 0)
            )
            x = (self.PET_MAX_WIDTH - image.width) // 2
            y = self.PET_MAX_HEIGHT - image.height
            canvas.alpha_composite(image, (x, y))
            self._image_cache[path] = (signature, canvas.copy())
            return canvas

    @staticmethod
    def _apply_colorkey(rgba_image, key_hex="#010101"):
        """色键方案：把RGBA合成到纯色底上，透明区域变成key色供Tk抠除。"""
        key_rgb = tuple(int(key_hex[i : i + 2], 16) for i in (1, 3, 5))
        base = Image.new("RGB", rgba_image.size, key_rgb)
        _, _, _, a = rgba_image.split()
        base.paste(rgba_image.convert("RGB"), mask=a)
        return base

    def _reload_asset(self, path):
        if Image is None or not os.path.exists(path):
            return
        try:
            image = self._load_image(path)
            if self._flip_current:
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            if self._native_layered:
                self._native_layered = self._win32_layered_window(
                    self.window, image, at_pos=tuple(self._pos)
                )
            if not self._native_layered:
                self._render_fallback(image)
            self._current_asset = path
        except Exception as exc:
            print(f"[桌宠] 状态立绘读取失败：{exc}")

    def _render_fallback(self, image):
        # 允许无窗口的资源/状态机测试只驱动位置与定时器逻辑。
        if self.root is None or tk is None or ImageTk is None:
            return
        key = "#010101"
        try:
            self.window.attributes("-transparentcolor", key)
        except Exception:
            pass
        self._photo = ImageTk.PhotoImage(
            self._apply_colorkey(image, key), master=self.root
        )
        if self._canvas is None:
            self._canvas = tk.Canvas(
                self.window,
                width=image.width,
                height=image.height,
                bg=key,
                highlightthickness=0,
            )
            self._canvas.pack()
            self._canvas_image_id = self._canvas.create_image(
                0, 0, image=self._photo, anchor="nw"
            )
            self._bind_interaction(self._canvas)
        else:
            self._canvas.configure(width=image.width, height=image.height)
            self._canvas.itemconfigure(self._canvas_image_id, image=self._photo)
            self._canvas.coords(self._canvas_image_id, 0, 0)

    @staticmethod
    def _win32_layered_window(window, image, at_pos=None):
        """用 PNG alpha 更新窗口；失败时返回 False 让调用方走回退方案。"""
        if os.name != "nt" or image is None:
            return False
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            user32.GetDC.restype = ctypes.c_void_p
            user32.ReleaseDC.restype = ctypes.c_int
            user32.UpdateLayeredWindow.restype = wintypes.BOOL
            gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
            gdi32.CreateDIBSection.restype = ctypes.c_void_p
            gdi32.SelectObject.restype = ctypes.c_void_p
            gdi32.DeleteObject.restype = wintypes.BOOL
            gdi32.DeleteDC.restype = wintypes.BOOL
            hwnd = wintypes.HWND(window.winfo_id())
            width, height = image.size

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD),
                    ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [
                    ("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3),
                ]

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            class SIZE(ctypes.Structure):
                _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

            class BLENDFUNCTION(ctypes.Structure):
                _fields_ = [
                    ("BlendOp", wintypes.BYTE),
                    ("BlendFlags", wintypes.BYTE),
                    ("SourceConstantAlpha", wintypes.BYTE),
                    ("AlphaFormat", wintypes.BYTE),
                ]

            user32.GetDC.argtypes = [wintypes.HWND]
            user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
            gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            gdi32.CreateDIBSection.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.HANDLE,
                wintypes.DWORD,
            ]
            gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
            gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetWindowLongPtrW.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_ssize_t,
            ]
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetWindowLongW.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.LONG,
            ]
            user32.UpdateLayeredWindow.argtypes = [
                wintypes.HWND,
                ctypes.c_void_p,
                ctypes.POINTER(POINT),
                ctypes.POINTER(SIZE),
                ctypes.c_void_p,
                ctypes.POINTER(POINT),
                wintypes.COLORREF,
                ctypes.POINTER(BLENDFUNCTION),
                wintypes.DWORD,
            ]

            bgra = image.convert("RGBa").tobytes("raw", "BGRa")

            screen_dc = user32.GetDC(None)
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not screen_dc or not mem_dc:
                if screen_dc:
                    user32.ReleaseDC(None, screen_dc)
                return False

            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down DIB
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0  # BI_RGB
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(
                screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
            )
            if not bitmap or not bits:
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(None, screen_dc)
                return False

            old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
            ctypes.memmove(bits, bytes(bgra), len(bgra))

            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_long.restype = ctypes.c_ssize_t
            set_long.restype = ctypes.c_ssize_t
            ex_style = get_long(hwnd, -20)  # GWL_EXSTYLE
            set_long(hwnd, -20, ex_style | 0x00080000 | 0x00000080)

            _px, _py = (
                at_pos
                if at_pos is not None
                else (window.winfo_x(), window.winfo_y())
            )
            dst = POINT(int(_px), int(_py))
            size = SIZE(width, height)
            src = POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_ALPHA
            updated = user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(dst),
                ctypes.byref(size),
                mem_dc,
                ctypes.byref(src),
                0,
                ctypes.byref(blend),
                2,
            )

            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            return bool(updated)
        except Exception as exc:
            print(f"[桌宠] 原生透明窗口不可用，使用回退方案：{exc}")
            return False

    def _bind_interaction(self, widget):
        if self._interaction_widget is not None and self._interaction_widget is not widget:
            for sequence in (
                "<Button-1>",
                "<B1-Motion>",
                "<ButtonRelease-1>",
                "<Double-Button-1>",
                "<Button-3>",
            ):
                self._interaction_widget.unbind(sequence)
        self._interaction_widget = widget
        widget.bind("<Button-1>", self._on_press)
        widget.bind("<B1-Motion>", self._on_drag)
        widget.bind("<ButtonRelease-1>", self._on_release)
        widget.bind("<Double-Button-1>", self._on_double_click)
        widget.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        if tk is None:
            return
        if self._context_menu:
            try:
                self._context_menu.destroy()
            except Exception:
                pass
        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg="#2b2926",
            fg="#f0ebe0",
            activebackground="#5a4a3b",
            activeforeground="#ffffff",
            bd=0,
            relief="flat",
        )
        self._context_menu = menu
        menu.add_command(label="和伴生体说话", command=self._run_context("chat"))
        menu.add_command(label="让他走一走", command=self._run_context("walk"))
        menu.add_command(label="让他回小世界", command=self._run_context("small_world"))
        menu.add_command(label="让他休息", command=self._run_context("sleep"))
        menu.add_separator()
        self._lock_var = tk.BooleanVar(master=self.root, value=self._locked)
        menu.add_checkbutton(
            label="锁定当前位置", variable=self._lock_var, command=self._toggle_lock
        )
        menu.add_command(label="恢复默认位置", command=self._reset_position)
        menu.add_command(label="隐藏桌宠", command=self.hide)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _run_context(self, name):
        def command():
            callback = self.context_actions.get(name)
            if callback:
                callback()

        return command

    def _toggle_lock(self):
        self._locked = not self._locked
        self._cancel_walk()
        self._restore_base_state()
        self._save_position()
        callback = self.context_actions.get("lock")
        if callback:
            callback(self._locked)

    def _reset_position(self):
        callback = self.context_actions.get("reset_position")
        if callback:
            callback()
            return
        if self.window and self.window.winfo_exists():
            _, _, right, bottom = self._screen_bounds()
            x, y = self._clamp_position(
                right - self.window.winfo_width() - 30,
                bottom - self.window.winfo_height() - 20,
            )
            self._pos = [x, y]
            self.window.geometry(f"+{x}+{y}")
            self._idle_origin = (x, y)
            self._save_position()

    def _on_double_click(self, _event):
        if self._drag:
            return
        if "happy" in self.state_assets and self.window and self.window.winfo_exists():
            self.play_action("happy", 900)
        if self.on_click:
            self.on_click()

    def show(self):
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self._state = self._base_state
            self._reload_asset(self.state_assets.get(self._base_state, self.asset_path))
            self._idle_origin = tuple(self._pos)
            self._resume_behavior()
            return True
        if Image is None or tk is None or not os.path.exists(self.asset_path):
            return

        # 先读取并缩放资源，读取失败时不创建空白窗口。
        selected_asset = self.state_assets.get(self._state, self.asset_path)
        try:
            image = self._load_image(selected_asset)
        except (OSError, ValueError) as exc:
            print(f"[桌宠] 立绘读取失败：{exc}")
            return

        # 先确定权威位置，再创建窗口和绘制（layered 窗口 winfo 不可靠）
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pw, ph = image.width, image.height
        x, y = self._load_position(sw - pw - 30, sh - ph - 80, pw, ph)
        self._pos = [x, y]

        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#010101")
        self.window.geometry(f"{pw}x{ph}+{x}+{y}")
        self.window.update_idletasks()

        self._current_asset = selected_asset
        self._native_layered = self._win32_layered_window(
            self.window, image, at_pos=(x, y)
        )
        if self._native_layered:
            self._bind_interaction(self.window)
        else:
            self._render_fallback(image)
        self.window.bind("<Escape>", lambda e: self.hide())
        self.window.update_idletasks()
        self._idle_origin = (x, y)
        self._resume_behavior()
        return True

    def hide(self):
        self._pause_behavior()
        self._cancel_action()
        self._cancel_walk()
        self._drag = self._press_pos = None
        self._did_move = False
        self._state = self._base_state
        if self._context_menu:
            try:
                self._context_menu.unpost()
            except Exception:
                pass
        if self.window and self.window.winfo_exists():
            self.window.withdraw()

    def destroy(self):
        self.hide()
        if self.window and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        self._native_layered = False
        self._canvas = None
        self._canvas_image_id = None
        self._interaction_widget = None

    def _schedule_idle(self):
        self._cancel_idle()
        if self._visible() and not self._drag and not self._walking and not self._action_sequence:
            self._idle_job = self.window.after(self._idle_interval, self._idle_step)

    def _cancel_idle(self):
        was_running = self._idle_job is not None
        self._cancel_job("_idle_job")
        if was_running and self.window and self.window.winfo_exists() and self._idle_origin:
            x, y = self._idle_origin
            self.window.geometry(f"+{x}+{y}")

    def _idle_step(self):
        self._idle_job = None
        if not self._visible() or self._drag or self._walking or self._action_sequence:
            return
        if self._idle_origin is None:
            self._idle_origin = tuple(self._pos)
        self._idle_phase = (self._idle_phase + 1) % 4
        offset = (0, -self._idle_amplitude, 0, self._idle_amplitude)[self._idle_phase]
        x, y = self._idle_origin
        self.window.geometry(f"+{x}+{y + offset}")
        self._schedule_idle()

    def _on_press(self, event):
        self._pause_behavior()
        self._cancel_action()
        self._cancel_walk()
        self._drag = (event.x_root, event.y_root, self._pos[0], self._pos[1])
        self._press_pos = (event.x_root, event.y_root)
        self._did_move = False

    def _on_drag(self, event):
        if not self._drag or self._locked:
            return
        sx, sy, wx, wy = self._drag
        if self._press_pos:
            self._did_move = self._did_move or (
                abs(event.x_root - self._press_pos[0]) > 4
                or abs(event.y_root - self._press_pos[1]) > 4
            )
        if not self._did_move:
            return
        if "drag" in self.state_assets and self._base_state != "sleep" and self._state != "drag":
            self._state = "drag"
            self._reload_asset(self.state_assets["drag"])
        x, y = self._clamp_position(wx + event.x_root - sx, wy + event.y_root - sy)
        self._pos = [x, y]
        self.window.geometry(f"+{x}+{y}")

    def _on_release(self, _event):
        if self._press_pos is None:
            return
        was_click = not self._did_move
        self._drag = None
        self._press_pos = None
        if self.window and self.window.winfo_exists():
            self._idle_origin = tuple(self._pos)
            if was_click and self._base_state != "sleep" and "surprised" in self.state_assets:
                self.play_sequence([("surprised", 650), ("happy", 450)])
            else:
                self._restore_base_state()
            if was_click and self.on_interact:
                self.on_interact(self._base_state)
            self._save_position()
        self._did_move = False
