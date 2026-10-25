"""只读音乐状态桥接。

默认不控制任何播放器，也不写入 QQ 音乐数据。若安装了 Windows Runtime
媒体会话依赖，则尝试读取当前系统媒体会话；否则返回项目自己的空状态。
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path


class MusicBridge:
    def __init__(self, memory_dir):
        self.state_file = Path(memory_dir) / "music_state.json"
        self._cache = {}
        self._checked_at = 0.0

    def snapshot(self):
        """返回只读快照；读取失败时返回本地空状态。"""
        now = time.monotonic()
        if now - self._checked_at < 5:
            return dict(self._cache)
        self._checked_at = now

        state = self._read_internal()
        session = self._read_windows_session()
        if session and session.get("title"):
            state.update(session)
        state.setdefault("source", "internal")
        state.setdefault("title", "")
        state.setdefault("artist", "")
        state.setdefault("album", "")
        state.setdefault("playing", False)
        state.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        self._cache = state
        return dict(state)

    def _read_internal(self):
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _read_windows_session(self):
        """可选读取 Windows 当前媒体会话；没有 winrt/winsdk 时安静降级。"""
        try:
            try:
                from winrt.windows.media.control import (
                    GlobalSystemMediaTransportControlsSessionManager as GSMTC)
            except ImportError:
                from winsdk.windows.media.control import (
                    GlobalSystemMediaTransportControlsSessionManager as GSMTC)

            return asyncio.run(self._read_session_async(GSMTC))
        except Exception:
            return {}

    async def _read_session_async(self, manager_type):
        manager = await manager_type.request_async()
        sessions = manager.get_sessions()
        selected = None
        for session in sessions:
            app_id = str(getattr(session, "source_app_user_model_id", "") or "").lower()
            if "qqmusic" in app_id or "qq音乐" in app_id:
                selected = session
                break
            if selected is None:
                selected = session
        if selected is None:
            return {}

        props = await selected.try_get_media_properties_async()
        title = str(getattr(props, "title", "") or "")
        artist = str(getattr(props, "artist", "") or "")
        album = str(getattr(props, "album_title", "") or "")
        info = {"source": "windows_media", "title": title,
                "artist": artist, "album": album}
        try:
            playback = selected.get_playback_info()
            status = str(getattr(playback, "playback_status", "") or "").lower()
            info["playing"] = "playing" in status
        except Exception:
            info["playing"] = False
        return info


if __name__ == "__main__":
    print(MusicBridge("memory").snapshot())
