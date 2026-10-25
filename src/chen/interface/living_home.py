# -*- coding: utf-8 -*-
"""生活首页：把伴生体的当前状态呈现成一个可进入的空间。

这个组件只负责视觉呈现和入口，不承载聊天、关系、工具等业务逻辑。
"""
from datetime import datetime
from pathlib import Path
import colorsys
import json
import os
try:
    import tkinter as tk
    _TK_FRAME = tk.Frame
except ImportError:
    tk = None
    _TK_FRAME = object

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError:  # pragma: no cover - 主 UI 会在缺少 Pillow 时降级
    Image = ImageDraw = ImageFilter = ImageTk = None


class LivingHome(_TK_FRAME):
    BG = "#171916"
    TEXT = "#F3EEE5"
    MUTED = "#B8B0A5"
    PANEL = "#171916"

    def __init__(self, parent, ui, chen, on_chat, on_relationship, on_life,
                 on_world, on_workshop, on_background):
        super().__init__(parent, bg=self.BG)
        self.ui = ui
        self.chen = chen
        self.on_chat = on_chat
        self.on_relationship = on_relationship
        self.on_life = on_life
        self.on_world = on_world
        self.on_workshop = on_workshop
        self.on_background = on_background
        self.settings_path = Path(__file__).resolve().parents[3] / "workspace" / "living_home_settings.json"
        self.source = self._default_source()
        self.theme = self._safe_theme(self.source)
        self.photo = None
        self.hits = []
        self._draw_job = None
        self.canvas = tk.Canvas(self, bg=self.BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.schedule_draw())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.bind_all("<F2>", self._demo_toggle, add="+")
        self._alone = False

    def _default_source(self):
        root = Path(__file__).resolve().parents[3]
        path = self._saved_background() or (root / "assets" / "room_night.png")
        if Image is None or not path.exists():
            return None

    def _saved_background(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            path = Path(data.get("background", ""))
            return path if path.exists() else None
        except Exception:
            return None
        try:
            image = Image.open(path).convert("RGB")
            if max(image.size) > 2200:
                image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            return image
        except Exception:
            return None

    @staticmethod
    def _safe_theme(image):
        if image is None:
            return {"accent": (128, 148, 124), "text": (243, 238, 229),
                    "muted": (184, 176, 165), "panel": (23, 25, 22)}
        try:
            pixels = list(image.resize((48, 32)).getdata())
            mean = tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))
            vivid = []
            for r, g, b in pixels:
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                if s > 0.3 and v > 0.35:
                    vivid.append((s * v, h, s))
            if vivid:
                vivid.sort(reverse=True)
                _, hue, sat = vivid[len(vivid) // 3]
                accent = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, min(sat, 0.7), 0.9))
            else:
                accent = (128, 148, 124)
            light = (0.299 * mean[0] + 0.587 * mean[1] + 0.114 * mean[2]) > 158
            text = (38, 35, 31) if light else (243, 238, 229)
            muted = (105, 100, 93) if light else (184, 176, 165)
            panel = (248, 246, 241) if light else (23, 25, 22)
            return {"accent": accent, "text": text, "muted": muted, "panel": panel}
        except Exception:
            return {"accent": (128, 148, 124), "text": (243, 238, 229),
                    "muted": (184, 176, 165), "panel": (23, 25, 22)}

    @staticmethod
    def _hex(rgb):
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)

    def schedule_draw(self):
        if self._draw_job:
            try:
                self.after_cancel(self._draw_job)
            except Exception:
                pass
        self._draw_job = self.after(80, self.draw)

    def refresh(self):
        self.schedule_draw()

    def _presence(self):
        state = {}
        try:
            state = self.chen.get_presence_snapshot(persist=False) or {}
        except Exception:
            pass
        place = state.get("activity_place") or "房间"
        activity = state.get("activity") or "安静待着"
        awake = state.get("awake", True)
        try:
            life = getattr(self.chen, "life", None)
            if life:
                awake = life.state.get("awake", awake)
                activity = life.state.get("activity") or activity
        except Exception:
            pass
        if not awake:
            activity = "睡着了"
        if self._alone:
            return place, "想一个人待一会儿", "要敲门的话，他可能会应。"
        return place, activity, self._recent_sentence(activity)

    @staticmethod
    def _recent_sentence(activity):
        if not activity or activity == "安静待着":
            return "今天似乎没有什么特别的事情。"
        return f"{activity}。"

    def _recent(self):
        lines = []
        try:
            log = getattr(self.chen, "day_log", None)
            if log:
                for item in reversed(log.today_lines()[-3:]):
                    ts = str(item.get("ts", ""))[-5:] or ""
                    text = str(item.get("text", ""))[:34]
                    if text:
                        lines.append((ts, text, "今天"))
        except Exception:
            pass
        if not lines:
            now = datetime.now()
            lines = [(now.strftime("%H:%M"), "当前状态已同步", "现在")]
        return lines

    def _cover(self, width, height):
        if self.source is None:
            return None
        scale = max(width / self.source.width, height / self.source.height)
        image = self.source.resize((max(width, int(self.source.width * scale)),
                                    max(height, int(self.source.height * scale))),
                                   Image.Resampling.LANCZOS)
        left = max(0, (image.width - width) // 2)
        top = max(0, (image.height - height) // 2)
        return image.crop((left, top, left + width, top + height))

    def draw(self):
        self._draw_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 500 or height < 380:
            return
        self.hits = []
        if Image is not None and self.source is not None:
            image = self._cover(width, height)
            blur = image.filter(ImageFilter.GaussianBlur(14))
            overlay = Image.new("RGB", (width, height), self.theme["panel"])
            image = Image.blend(blur, overlay, 0.42)
            # 只压暗场景，不把文字和入口交给背景对比度决定。
            self.photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            self.canvas.create_rectangle(0, 0, width, height, fill="#0C0E0C", stipple="gray25", outline="")
        else:
            self.canvas.delete("all")
            self.canvas.create_rectangle(0, 0, width, height, fill=self.BG, outline="")

        accent = self._hex(self.theme["accent"])
        text = self._hex(self.theme["text"])
        muted = self._hex(self.theme["muted"])
        panel = self._hex(self.theme["panel"])
        margin = max(24, int(width * 0.035))
        place, activity, sentence = self._presence()

        def panel_box(x0, y0, x1, y1, radius=18):
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=panel, outline="#FFFFFF", width=1,
                                         stipple="gray50")

        # 左上身份，右上次级入口。
        panel_box(margin, margin, margin + 210, margin + 42, 18)
        self.canvas.create_text(margin + 18, margin + 21, text="伴生体", fill=text,
                                anchor="w", font=("Microsoft YaHei UI", 13, "bold"))
        self.canvas.create_text(margin + 68, margin + 21, text="住在这台电脑里", fill=muted,
                                anchor="w", font=("Microsoft YaHei UI", 9))
        x = width - margin
        for label, command in (("修理台", "workshop"), ("别处", "places"), ("我们", "us"), ("换背景", "bg")):
            w = max(62, 28 + len(label) * 14)
            x0 = x - w
            panel_box(x0, margin, x, margin + 42, 18)
            self.canvas.create_text((x0 + x) / 2, margin + 21, text=label, fill=text,
                                    font=("Microsoft YaHei UI", 9))
            self.hits.append((x0, margin, x, margin + 42, command))
            x = x0 - 8

        # 当前状态：左侧留出呼吸空间，按钮是唯一主动作。
        current_y = max(118, int(height * 0.57))
        panel_box(margin, current_y, min(width - margin, margin + 700), current_y + 92, 24)
        self.canvas.create_text(margin + 26, current_y + 25, text=f"{place} · {activity}", fill=text,
                                anchor="w", font=("KaiTi", 18, "bold"))
        self.canvas.create_text(margin + 26, current_y + 59, text=sentence, fill=muted,
                                anchor="w", font=("KaiTi", 11))
        button_w = 118
        bx1 = min(width - margin - 20, margin + 700 - 20)
        bx0 = bx1 - button_w
        self.canvas.create_rectangle(bx0, current_y + 25, bx1, current_y + 67,
                                     fill=accent if not self._alone else panel, outline=accent, width=2)
        self.canvas.create_text((bx0 + bx1) / 2, current_y + 46,
                                text="敲敲门" if self._alone else "找他",
                                fill=accent if self._alone else self._hex(self._on_accent()),
                                font=("Microsoft YaHei UI", 10, "bold"))
        self.hits.append((bx0, current_y + 25, bx1, current_y + 67, "chat"))

        # 他的今天：固定在底部的生活痕迹，不使用后台卡片。
        py1 = height - margin
        py0 = py1 - min(180, max(150, int(height * 0.22)))
        panel_box(margin, py0, width - margin, py1, 20)
        self.canvas.create_text(margin + 24, py0 + 24, text="他的今天", fill=text,
                                anchor="w", font=("Microsoft YaHei UI", 12, "bold"))
        self.canvas.create_text(width - margin - 24, py0 + 24, text="全部", fill=muted,
                                anchor="e", font=("Microsoft YaHei UI", 9))
        self.hits.append((width - margin - 90, py0 + 8, width - margin, py0 + 46, "days"))
        self.canvas.create_line(margin + 24, py0 + 52, width - margin - 24, py0 + 52, fill="#FFFFFF")
        for i, (stamp, line, tag) in enumerate(self._recent()):
            y = py0 + 78 + i * 30
            self.canvas.create_text(margin + 26, y, text="●" if i == 0 else "○", fill=accent if i == 0 else muted,
                                    anchor="w", font=("Segoe UI Symbol", 9))
            self.canvas.create_text(margin + 54, y, text=stamp, fill=muted,
                                    anchor="w", font=("Consolas", 9))
            self.canvas.create_text(margin + 124, y, text=line, fill=text,
                                    anchor="w", font=("KaiTi", 11))
            self.canvas.create_text(width - margin - 26, y, text=tag, fill=muted,
                                    anchor="e", font=("Microsoft YaHei UI", 8))

    def _on_accent(self):
        r, g, b = self.theme["accent"]
        return (40, 38, 34) if (0.299*r + 0.587*g + 0.114*b) > 150 else (250, 247, 240)

    def _on_motion(self, event):
        self.canvas.configure(cursor="hand2" if self._hit(event.x, event.y) else "")

    def _hit(self, x, y):
        for x0, y0, x1, y1, command in self.hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return command
        return None

    def _on_click(self, event):
        command = self._hit(event.x, event.y)
        actions = {"chat": self.on_chat, "us": self.on_relationship, "days": self.on_life,
                   "places": self.on_world, "workshop": self.on_workshop, "bg": self.on_background}
        callback = actions.get(command)
        if callback:
            callback()

    def _demo_toggle(self, _event=None):
        self._alone = not self._alone
        self.draw()
        return "break"

    def set_background(self, path):
        if Image is None:
            return False
        try:
            image = Image.open(path).convert("RGB")
            if max(image.size) > 2200:
                image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            self.source = image
            self.theme = self._safe_theme(image)
            try:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.settings_path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps({"background": str(path)}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, self.settings_path)
            except Exception:
                pass
            self.draw()
            return True
        except Exception:
            return False
