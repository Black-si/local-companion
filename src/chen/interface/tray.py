# -*- coding: utf-8 -*-
"""
系统托盘模块 - 让伴生体在聊天窗口关闭后仍在后台运行（感知/决策/记忆线程不断）。
依赖 pystray + Pillow（未安装时 UI 会自动降级为“关窗即退出”）。
所有回调在 pystray 自己的线程触发，UI 侧需用 root.after 切回 Tk 主线程。
"""

import pystray
from PIL import Image, ImageDraw, ImageFont


def _make_icon(size=64):
    """画一个暖棕色圆底 + 白色“沉”字的托盘图标（配色与聊天界面一致）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=(184, 142, 90, 255))  # #B88E5A

    font = None
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                 r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font = ImageFont.truetype(path, int(size * 0.62))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "沉"
    try:
        bbox = d.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size - w) / 2 - bbox[0]
        y = (size - h) / 2 - bbox[1] - size * 0.02
        d.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    except Exception:
        d.text((size * 0.28, size * 0.18), text, fill=(255, 255, 255, 255), font=font)
    return img


class TrayIcon:
    """系统托盘图标：左键/双击打开窗口，右键菜单可反思、退出。"""

    def __init__(self, on_show, on_quit, on_reflect=None, tooltip="伴生体（后台运行中）"):
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_reflect = on_reflect

        items = [pystray.MenuItem("打开伴生体", self._show, default=True)]
        if on_reflect:
            items.append(pystray.MenuItem("立即反思（整理今天）", self._reflect))
        items.append(pystray.MenuItem("退出伴生体", self._quit))

        self.icon = pystray.Icon("chen", _make_icon(), tooltip, pystray.Menu(*items))

    def _safe(self, fn):
        try:
            if fn:
                fn()
        except Exception:
            pass

    def _show(self, icon=None, item=None):
        self._safe(self.on_show)

    def _reflect(self, icon=None, item=None):
        self._safe(self.on_reflect)

    def _quit(self, icon=None, item=None):
        self._safe(self.on_quit)

    def notify(self, message, title="伴生体"):
        """弹一条托盘气泡通知（窗口隐藏时用来提示他主动说话）。"""
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def run(self):
        """在后台线程启动托盘（不阻塞）。"""
        try:
            self.icon.run_detached()
            return True
        except Exception:
            return False

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
