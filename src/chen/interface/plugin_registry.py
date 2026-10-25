# -*- coding: utf-8 -*-
"""伴生体插件中心的轻量注册表。

插件先描述能力和状态，具体实现仍由各模块负责，避免主界面继续堆按钮。
"""


def get_plugins(ui):
    remote = getattr(ui, "remote_server", None)
    config = getattr(getattr(ui, "chen", None), "config", {}) or {}
    mcp_enabled = bool((config.get("tools", {}) or {}).get("mcp_enabled", False))
    return [
        {
            "id": "phone_bridge",
            "icon": "⌁",
            "name": "手机连接电脑",
            "desc": "让 iPhone / iPad 通过局域网查看状态、发消息和同步活动。",
            "status": "已启动" if remote and getattr(remote, "_server", None) else "未启动",
            "action": ui._open_phone_plugin,
        },
        {
            "id": "mcp",
            "icon": "◇",
            "name": "MCP 工具接入",
            "desc": "为外部工具提供可控的上下文和动作入口，当前先保留配置位。",
            "status": "已启用" if mcp_enabled else "默认关闭",
            "action": ui._open_mcp_plugin,
        },
        {
            "id": "desktop_pet",
            "icon": "✦",
            "name": "桌宠",
            "desc": "让伴生体出现在桌面，支持动作、互动和主动出现。",
            "status": "可用",
            "action": ui._open_desktop_pet_plugin,
        },
        {
            "id": "dream",
            "icon": "☾",
            "name": "梦境循环",
            "desc": "在夜间从当天经历和记忆里生成梦境，并留下少量洞见。",
            "status": "已接入",
            "action": ui._open_dream_plugin,
        },
        {
            "id": "music",
            "icon": "♫",
            "name": "音乐状态",
            "desc": "只读同步当前音乐状态，不修改 QQ 音乐的歌单和播放记录。",
            "status": "已接入",
            "action": ui._open_music_plugin,
        },
        {
            "id": "browser",
            "icon": "◎",
            "name": "浏览器感知",
            "desc": "只记录允许的应用和活动摘要，不读取网页内容。",
            "status": "已接入",
            "action": ui._open_browser_plugin,
        },
        {
            "id": "weather",
            "icon": "☼",
            "name": "天气与互联网感知",
            "desc": "为生活状态和小世界提供天气及低频兴趣信息。",
            "status": "已接入",
            "action": ui._open_weather_plugin,
        },
    ]
