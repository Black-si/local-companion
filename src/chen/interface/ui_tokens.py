# -*- coding: utf-8 -*-
"""伴生体界面的视觉令牌与五时段动态光影系统。

同时供主窗口导航、PySide6 生活首页、沉浸式聊天抽屉/小窗以及“我们/他的生活”面板共用，
让毛玻璃底色、强调色与气泡质感随昼夜五个时段自然呼吸。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

PALETTE = {
    "canvas": "#F6F2EB",       # 宣纸暖白
    "sidebar": "#F6F2EB",      # 导航与画布同色，靠留白分区
    "surface": "#FCFAF5",      # 亮半度的纸面
    "line": "#E4DDCF",         # 发丝线
    "line_soft": "#EDE7DB",    # 弱分隔线
    "text": "#2B2620",         # 暖墨
    "muted": "#7E7466",        # 次级文字
    "faded": "#ADA392",        # 弱辅助
    "ink": "#2E2924",          # 此刻主卡
    "ink_sub": "#B7AB99",      # 主卡次级文字
    "ink_paper": "#F7F1E6",    # 主卡主文字
    "sage": "#7D9070",         # 在线 / 生态
    "sage_bg": "#ECEFE3",      # 生态浅底
    "coral": "#C4693F",        # 陶土珊瑚强调色
    "coral_bg": "#F4E8DF",     # 强调色浅底
    "clay": "#A85B40",         # 异常
}

SPACE = {"xxs": 4, "xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 36, "xxl": 52}

FONT = {
    "hero": ("Georgia", 24, "bold"),
    "h1": ("Microsoft YaHei UI", 18, "bold"),
    "h2": ("Microsoft YaHei UI", 12, "bold"),
    "body": ("Microsoft YaHei UI", 10),
    "secondary": ("Microsoft YaHei UI", 9),
    "caption": ("Microsoft YaHei UI", 8),
    "eyebrow": ("Georgia", 9),
}

STATUS = {
    "online": (PALETTE["sage"], "运行中"),
    "paused": (PALETTE["faded"], "已暂停"),
    "warning": (PALETTE["clay"], "需要检查"),
    "planned": (PALETTE["muted"], "规划中"),
}

# 五时段动态伴侣光影主题（与 16 支房间视频的窗外天色/台灯光影严格对应）
PERIOD_THEMES: dict[str, dict] = {
    "day": {
        "key": "day",
        "label": "晨光",
        "tint": (22, 26, 22),
        "border": (176, 194, 168),
        "accent": (150, 176, 142),
        "accent_hex": "#96B08E",
        "text": (244, 239, 230),
        "text_hex": "#F4EFE6",
        "muted": (188, 181, 170),
        "muted_hex": "#BCB5AA",
        "on_accent": (34, 36, 31),
        "on_accent_hex": "#22241F",
        "card_alpha": 176,
    },
    "noon": {
        "key": "noon",
        "label": "正午",
        "tint": (25, 27, 22),
        "border": (190, 198, 168),
        "accent": (163, 184, 140),
        "accent_hex": "#A3B88C",
        "text": (246, 241, 232),
        "text_hex": "#F6F1E8",
        "muted": (194, 186, 173),
        "muted_hex": "#C2BAAD",
        "on_accent": (36, 35, 30),
        "on_accent_hex": "#24231E",
        "card_alpha": 172,
    },
    "afternoon": {
        "key": "afternoon",
        "label": "午后",
        "tint": (28, 25, 20),
        "border": (208, 190, 158),
        "accent": (200, 166, 114),
        "accent_hex": "#C8A672",
        "text": (247, 240, 228),
        "text_hex": "#F7F0E4",
        "muted": (196, 184, 168),
        "muted_hex": "#C4B8A8",
        "on_accent": (38, 33, 26),
        "on_accent_hex": "#26211A",
        "card_alpha": 175,
    },
    "dusk": {
        "key": "dusk",
        "label": "夕照",
        "tint": (31, 22, 20),
        "border": (218, 168, 142),
        "accent": (216, 139, 98),
        "accent_hex": "#D88B62",
        "text": (248, 238, 228),
        "text_hex": "#F8EEE4",
        "muted": (200, 178, 165),
        "muted_hex": "#C8B2A5",
        "on_accent": (40, 28, 24),
        "on_accent_hex": "#281C18",
        "card_alpha": 180,
    },
    "night": {
        "key": "night",
        "label": "夜灯",
        "tint": (19, 21, 25),
        "border": (210, 178, 138),
        "accent": (224, 169, 109),
        "accent_hex": "#E0A96D",
        "text": (244, 237, 226),
        "text_hex": "#F4EDE2",
        "muted": (184, 176, 165),
        "muted_hex": "#B8B0A5",
        "on_accent": (32, 28, 22),
        "on_accent_hex": "#201C16",
        "card_alpha": 186,
    },
}


def period_key_from_hour(hour: Optional[int] = None) -> str:
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


def get_period_theme(period: Optional[str] = None, hour: Optional[int] = None) -> dict:
    """返回指定时段或当前小时对应的光影主题字典（返回浅拷贝以防原地修改）。"""
    key = period if period in PERIOD_THEMES else period_key_from_hour(hour)
    return dict(PERIOD_THEMES.get(key, PERIOD_THEMES["night"]))
