# -*- coding: utf-8 -*-
"""
remote_server.py — 手机通过家里WiFi给伴生体发消息与同步状态的本地HTTP接口。
只监听局域网，并用本地令牌做基础认证。
路由：
  GET  /health             测试连通
  GET  /mobile             新版 Ins 极简磨砂 / 旧书页双主题手机伴生界面（无灵动岛）
  GET  /api/status         读取伴生体此刻完整快照（含空间、情绪、精力、音乐、心绪、手机白名单状态）
  GET  /api/chat/history   读取最近聊天记录（同步电脑端对话与伴生体主动发言）
  GET  /api/day_log        读取今日经历日志（day_log）与小世界公开物品（world_store）
  GET  /api/world/snapshot 读取小世界快照
  GET  /api/world/runtime  读取小世界运行时状态
  POST /api/presence       同步手机白名单状态（activity/place/app/focus/wifi/battery/charging/music_*）
  POST /api/chat           JSON {"text": "你好"}，返回 {"reply": "..."}
  POST /api/poke           轻量互动（轻叩窗沿 / 拍立得红心回执），记录到经历日志并返回即时反馈
  POST /api/world/command  发送小世界空间切换或互动指令
"""
import json
import queue
import secrets
import threading
import socket
import ipaddress
import time
from datetime import datetime
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _json_default(value):
    """Keep an unexpected runtime object from aborting an HTTP response."""
    if isinstance(value, (datetime, Path)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    # API snapshots are best-effort views. An unsupported internal object
    # should become null instead of dropping the connection mid-response.
    return None


def _period_label(hour: int) -> str:
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 22:
        return "晚上"
    return "深夜"


class _BoundedHTTPServer(ThreadingHTTPServer):
    """在创建线程之前限制活动连接，包括尚未发送完整请求头的连接。"""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler, max_connections=16):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(0.25)
                pending = b""
                while len(pending) < 16384 and b"\r\n\r\n" not in pending:
                    try:
                        chunk = request.recv(min(4096, 16384 - len(pending)))
                    except (socket.timeout, BlockingIOError):
                        break
                    if not chunk:
                        break
                    pending += chunk
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                try:
                    request.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class RemoteServer:
    MAX_BODY_BYTES = 65536
    MAX_CHAT_CHARS = 12000
    REQUESTS_PER_MINUTE = 60

    def __init__(self, chen, ui_queue, port=8765, token=None):
        self.chen = chen
        self.ui_queue = ui_queue
        self.port = port
        self._server = None
        self._chat_lock = getattr(chen, "_chat_lock", None) or threading.RLock()
        self._rate_lock = threading.Lock()
        self._rate_windows = OrderedDict()
        self.token = token or self._load_token()
        try:
            from chen.behavior.world_bridge import WorldBridge
            self.world_bridge = WorldBridge(chen)
        except Exception as exc:
            self.world_bridge = None
            print(f"[remote] 小世界桥接初始化失败：{exc}")

    def _load_token(self):
        configured = str((getattr(self.chen, "config", {}).get("remote", {}) or {}).get("token", "") or "").strip()
        path = Path(getattr(self.chen, "base_dir", Path.cwd())) / "memory" / "remote_token.txt"
        if not configured:
            try:
                configured = path.read_text(encoding="utf-8").strip()
            except Exception:
                configured = ""
        if not configured:
            configured = "chen-" + secrets.token_urlsafe(18)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(configured, encoding="utf-8")
            except Exception:
                pass
        return configured

    def start(self):
        handler = self._make_handler()
        self._server = _BoundedHTTPServer(("0.0.0.0", self.port), handler)
        self.port = self._server.server_address[1]
        t = threading.Thread(target=self._server.serve_forever, daemon=True, name="chen-remote")
        t.start()
        ip = self._lan_ip()
        print(f"[remote] 手机服务：http://{ip}:{self.port}/mobile（连接凭据请在电脑界面查看）")
        print("[remote] iPhone/iPad 与电脑同一 WiFi；快捷指令使用 X-Chen-Token 请求头。")

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
            except Exception:
                pass

    def _lan_ip(self):
        """优先返回真实局域网地址，避免 VPN/WSL 虚拟网卡抢到入口。"""
        candidates = []
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                address = info[4][0]
                if address not in candidates:
                    candidates.append(address)
        except Exception:
            pass
        private = []
        for address in candidates:
            try:
                ip = ipaddress.ip_address(address)
                if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                    priority = 0 if address.startswith("192.168.") else (1 if address.startswith("10.") else 2)
                    private.append((priority, address))
            except ValueError:
                continue
        if private:
            private.sort()
            return private[0][1]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_chat_history(self, limit=40):
        """只读获取最近聊天记录，用于在手机端展示上下文与主动消息。"""
        limit = max(1, min(100, int(limit or 40)))
        lock = getattr(self.chen, "_chat_log_lock", None)
        try:
            if lock:
                with lock:
                    raw = list(getattr(self.chen, "chat_log", []) or [])[-limit:]
            else:
                raw = list(getattr(self.chen, "chat_log", []) or [])[-limit:]
            result = []
            for item in raw:
                if isinstance(item, dict) and item.get("text"):
                    result.append({
                        "role": str(item.get("role", "chen")),
                        "text": str(item.get("text", ""))[:2000],
                        "ts": str(item.get("ts", "")),
                    })
            return result
        except Exception:
            return []

    def _get_today_logs(self, limit=18):
        """只读获取今日经历日志（day_log）。"""
        limit = max(1, min(50, int(limit or 18)))
        try:
            day_log = getattr(self.chen, "day_log", None)
            if not day_log or not hasattr(day_log, "today_lines"):
                return []
            lines = list(day_log.today_lines() or [])[-limit:]
            out = []
            for rec in reversed(lines):
                if isinstance(rec, dict):
                    ts = str(rec.get("ts", ""))[-5:]
                    text = str(rec.get("text", "")).strip()
                    kind = str(rec.get("kind", "life"))
                    if text:
                        out.append({"time": ts or "--:--", "text": text[:160], "kind": kind})
            return out
        except Exception:
            return []

    def _get_world_items(self, limit=10):
        """只读获取小世界里对用户公开的物品与便签。"""
        limit = max(1, min(30, int(limit or 10)))
        try:
            ws = getattr(self.chen, "world_store", None)
            if not ws or not hasattr(ws, "visible_items"):
                return []
            view = ws.visible_items("用户") or {}
            open_items = list(view.get("open") or [])[-limit:]
            icon_map = {
                "note": "📄",
                "photo": "🖼️",
                "music": "🎵",
                "book": "📖",
                "tea": "🍵",
                "日记": "📖",
                "琐碎": "✦",
                "收藏": "🎵",
                "念头": "💭",
                "留言": "💬",
            }
            out = []
            for item in reversed(open_items):
                if isinstance(item, dict):
                    kind = str(item.get("type") or item.get("kind") or "note")
                    title = str(item.get("title") or item.get("name") or "留下的痕迹")[:60]
                    # WorldStore 使用 text 保存正文；兼容旧版 content/summary/desc 字段。
                    content = str(item.get("text") or item.get("content") or item.get("summary") or item.get("desc") or "")[:200]
                    place = str(item.get("place") or item.get("location") or "小世界")[:30]
                    out.append({
                        "id": str(item.get("id") or title),
                        "icon": icon_map.get(kind, "✦"),
                        "title": title,
                        "place": place,
                        "snippet": content or "安静放在那里的旧物件。",
                    })
            return out
        except Exception:
            return []

    def _get_enriched_status(self):
        """返回兼容原有字段并附带时段、天气、心绪独白与近期摘要的状态快照。"""
        try:
            snap = dict(self.chen.get_presence_snapshot(persist=False) or {})
        except Exception:
            snap = {}
        now = datetime.now()
        period = _period_label(now.hour)
        awake = bool(snap.get("awake", True))
        snap["period"] = f"{period} · {'清醒' if awake else '熟睡中'}"
        snap["is_night"] = bool(now.hour >= 20 or now.hour < 6 or not awake)

        # 读取缓存天气摘要（不触发阻塞网络请求）
        weather_text = "KUNMING"
        try:
            weather_obj = getattr(self.chen, "weather", None)
            cached = getattr(weather_obj, "cached", None) or getattr(weather_obj, "last_data", None)
            if isinstance(cached, dict):
                temp = cached.get("temperature") or cached.get("temp")
                desc = cached.get("weather") or cached.get("desc") or ""
                if temp is not None:
                    weather_text = f"KUNMING {desc} {temp}°C".strip()
        except Exception:
            pass
        snap["weather_summary"] = weather_text

        # 读取意识工作空间或根据当前状态生成轻心绪独白
        thought_text = ""
        try:
            cw = getattr(self.chen, "conscious_workspace", None)
            if cw and hasattr(cw, "current_thought"):
                t = cw.current_thought()
                if isinstance(t, str) and t.strip():
                    thought_text = t.strip()[:120]
                elif isinstance(t, dict) and t.get("text"):
                    thought_text = str(t.get("text")).strip()[:120]
        except Exception:
            pass
        if not thought_text:
            ext = snap.get("external_presence") or {}
            he_act = ext.get("activity")
            he_place = ext.get("place")
            if not awake:
                thought_text = "呼吸很轻，正在夜间的梦境循环里整理白天的记忆。"
            elif he_act:
                loc_prefix = f"在{he_place}" if he_place else ""
                thought_text = f"心里记着用户正{loc_prefix}{he_act}，打算等用户空下来再聊。"
            else:
                place = snap.get("activity_place") or "这边"
                thought_text = f"在{place}安静待着，留神听着手机这头的动静。"
        snap["thought"] = thought_text

        # 附带外部状态构建好的 prompt_block 预览，方便手机端白名单看板直接展示
        try:
            store = getattr(self.chen, "external_presence_store", None)
            block = store.prompt_block() if store else ""
            snap["prompt_block"] = block if isinstance(block, str) else ""
        except Exception:
            snap["prompt_block"] = ""
        return snap

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def end_headers(self):
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "X-Chen-Token, Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                super().end_headers()

            def log_message(self, *a):
                pass  # 静默，别刷控制台

            def _text(self, text, code=200):
                body = (text or "").encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, value, code=200):
                body = json.dumps(
                    value, ensure_ascii=False, allow_nan=False, default=_json_default
                ).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                supplied = self.headers.get("X-Chen-Token", "")
                if not supplied:
                    supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                return bool(supplied) and secrets.compare_digest(
                    supplied.encode("utf-8"), outer.token.encode("utf-8"))

            def _rate_allowed(self):
                now = time.monotonic()
                address = self.client_address[0] if self.client_address else "unknown"
                with outer._rate_lock:
                    while outer._rate_windows:
                        oldest = next(iter(outer._rate_windows))
                        if now - outer._rate_windows[oldest][-1] < 60:
                            break
                        outer._rate_windows.popitem(last=False)
                    if address not in outer._rate_windows:
                        if len(outer._rate_windows) >= 256:
                            return False
                        outer._rate_windows[address] = deque()
                    window = outer._rate_windows[address]
                    while window and now - window[0] >= 60:
                        window.popleft()
                    if len(window) >= outer.REQUESTS_PER_MINUTE:
                        return False
                    window.append(now)
                    outer._rate_windows.move_to_end(address)
                return True

            def _read_json(self):
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("transfer encoding not supported")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) > 1:
                    raise ValueError("ambiguous content length")
                raw_length = self.headers.get_all("Content-Length", [None])[0]
                if raw_length is None:
                    raise ValueError("missing content length")
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("invalid content length") from exc
                if not raw_length.isascii() or not raw_length.isdecimal():
                    raise ValueError("invalid content length")
                if length > outer.MAX_BODY_BYTES:
                    raise ValueError("request body too large")
                try:
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("incomplete body")
                    data = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
                except Exception as exc:
                    raise ValueError("invalid json") from exc
                if not isinstance(data, dict):
                    raise ValueError("json object required")
                return data

            def _mobile_page(self):
                return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#F7F6F2">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>伴生体 · 手机入口</title>
  <style>
    :root {
      --bg-outer: #EAE3D7;
      --bg-canvas: #F6F2EB;
      --bg-surface: rgba(252, 250, 245, 0.88);
      --bg-surface-solid: #FCFAF5;
      --bg-elevated: #EFE8DD;
      --bg-pill: #E6DDD0;
      --border-line: rgba(119, 94, 68, 0.14);
      --border-strong: rgba(138, 90, 56, 0.28);
      --text-main: #2B2620;
      --text-secondary: #6E6559;
      --text-muted: #9A8F80;
      --accent-coral: #C4693F;
      --accent-coral-hover: #B05A32;
      --accent-coral-soft: rgba(196, 105, 63, 0.12);
      --accent-brown: #8A5A38;
      --accent-sage: #66805F;
      --accent-sage-soft: rgba(102, 128, 95, 0.14);
      --bubble-mine: #E8D3BD;
      --bubble-mine-text: #2B2620;
      --bubble-chen: #EFECE6;
      --bubble-chen-text: #2B2620;
      --shadow-card: 0 10px 28px rgba(68, 48, 30, 0.06);
      --font-sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei UI", "Segoe UI", sans-serif;
      --font-serif: "Songti SC", "Noto Serif SC", "STSong", "SimSun", serif;
    }

    body.theme-night {
      --bg-outer: #181614;
      --bg-canvas: #23201D;
      --bg-surface: rgba(45, 41, 37, 0.88);
      --bg-surface-solid: #2D2925;
      --bg-elevated: #36312C;
      --bg-pill: #3E3832;
      --border-line: rgba(232, 226, 214, 0.1);
      --border-strong: rgba(212, 132, 90, 0.35);
      --text-main: #E8E2D6;
      --text-secondary: #B5ACA0;
      --text-muted: #857C70;
      --accent-coral: #D47A50;
      --accent-coral-hover: #E0885E;
      --accent-coral-soft: rgba(212, 122, 80, 0.16);
      --accent-brown: #D9A782;
      --accent-sage: #82A07A;
      --accent-sage-soft: rgba(130, 160, 122, 0.18);
      --bubble-mine: #523B2B;
      --bubble-mine-text: #F5EFE6;
      --bubble-chen: #332E2A;
      --bubble-chen-text: #E8E2D6;
      --shadow-card: 0 10px 28px rgba(0, 0, 0, 0.28);
    }

    /* Ins 极简奶油磨砂与液态玻璃风（默认激活） */
    body.style-ins {
      --bg-outer: #E8E6E1;
      --bg-canvas: #F7F6F2;
      --bg-surface: rgba(255, 255, 255, 0.78);
      --bg-surface-solid: #FFFFFF;
      --bg-elevated: rgba(240, 238, 233, 0.88);
      --bg-pill: #EAE8E3;
      --border-line: rgba(28, 27, 26, 0.07);
      --border-strong: rgba(28, 27, 26, 0.22);
      --text-main: #181716;
      --text-secondary: #5C5954;
      --text-muted: #969189;
      --accent-coral: #22201E;
      --accent-coral-hover: #3A3734;
      --accent-coral-soft: rgba(34, 32, 30, 0.08);
      --accent-brown: #3D3935;
      --accent-sage: #5E7158;
      --accent-sage-soft: rgba(94, 113, 88, 0.12);
      --bubble-mine: #1E1D1B;
      --bubble-mine-text: #FAF9F6;
      --bubble-chen: #FFFFFF;
      --bubble-chen-text: #181716;
      --shadow-card: 0 12px 32px rgba(20, 19, 18, 0.04);
    }

    body.style-ins.theme-night {
      --bg-outer: #0B0B0C;
      --bg-canvas: #121214;
      --bg-surface: rgba(30, 30, 34, 0.76);
      --bg-surface-solid: #1C1C20;
      --bg-elevated: rgba(42, 42, 48, 0.82);
      --bg-pill: #2A2A30;
      --border-line: rgba(255, 255, 255, 0.08);
      --border-strong: rgba(255, 255, 255, 0.24);
      --text-main: #F3F2EE;
      --text-secondary: #A6A39C;
      --text-muted: #6E6B65;
      --accent-coral: #EAE7E0;
      --accent-coral-hover: #FFFFFF;
      --accent-coral-soft: rgba(234, 231, 224, 0.12);
      --accent-brown: #D8D3C8;
      --accent-sage: #88A380;
      --accent-sage-soft: rgba(136, 163, 128, 0.16);
      --bubble-mine: #EAE7E0;
      --bubble-mine-text: #141416;
      --bubble-chen: rgba(36, 36, 40, 0.92);
      --bubble-chen-text: #F3F2EE;
      --shadow-card: 0 14px 36px rgba(0, 0, 0, 0.42);
    }

    * {
      box-sizing: border-box;
      -webkit-tap-highlight-color: transparent;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: var(--font-sans);
      color: var(--text-main);
      background: var(--bg-canvas);
      padding: env(safe-area-inset-top) 0 env(safe-area-inset-bottom);
      transition: background 0.3s ease, color 0.3s ease;
    }

    main.phone-stage {
      width: 100%;
      max-width: 560px;
      margin: 0 auto;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      position: relative;
      padding-bottom: 18px;
    }

    .app-header {
      padding: 16px 18px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .brand-group {
      display: flex;
      align-items: baseline;
      gap: 10px;
    }

    .brand-title {
      font-family: var(--font-serif);
      font-size: 24px;
      font-weight: 700;
      margin: 0;
      letter-spacing: 0.05em;
    }

    .conn-pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 11px;
      background: var(--accent-sage-soft);
      color: var(--accent-sage);
      font-weight: 500;
    }

    .conn-pill.err {
      background: rgba(196, 80, 63, 0.14);
      color: #C4503F;
    }

    .conn-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: currentColor;
      animation: breathe 2.8s infinite ease-in-out;
    }

    @keyframes breathe {
      0%, 100% { opacity: 0.45; transform: scale(0.92); }
      50% { opacity: 1; transform: scale(1.1); }
    }

    .header-tools {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .icon-btn {
      border: 1px solid var(--border-line);
      background: var(--bg-surface);
      color: var(--text-secondary);
      height: 31px;
      padding: 0 10px;
      border-radius: 999px;
      font-size: 11.5px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
    }

    .window-card {
      margin: 0 16px 10px;
      padding: 15px 16px;
      background: var(--bg-surface);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      border: 1px solid var(--border-line);
      border-radius: 24px;
      box-shadow: var(--shadow-card);
      position: relative;
      overflow: hidden;
    }

    .window-top-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }

    .badge-row {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    .world-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      font-weight: 600;
      color: var(--accent-brown);
      background: var(--bg-elevated);
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--border-line);
    }

    .period-badge {
      font-size: 11px;
      color: var(--text-muted);
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--bg-canvas);
    }

    .metrics-group {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 11px;
      color: var(--text-secondary);
    }

    .energy-wrap {
      display: flex;
      align-items: center;
      gap: 5px;
    }

    .energy-bar-bg {
      width: 42px;
      height: 6px;
      background: var(--bg-pill);
      border-radius: 999px;
      overflow: hidden;
    }

    .energy-bar-fill {
      height: 100%;
      background: var(--accent-sage);
      border-radius: 999px;
      transition: width 0.4s ease;
    }

    .ins-profile-row {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }

    .ins-avatar-wrap {
      position: relative;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      padding: 2px;
      background: linear-gradient(135deg, var(--text-main), var(--text-muted));
      flex-shrink: 0;
    }

    .ins-avatar-inner {
      width: 100%;
      height: 100%;
      border-radius: 50%;
      background: var(--bg-surface-solid);
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--font-serif);
      font-size: 17px;
      font-weight: 700;
      color: var(--text-main);
    }

    .ins-online-dot {
      position: absolute;
      right: 1px;
      bottom: 1px;
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: var(--accent-sage);
      border: 2px solid var(--bg-surface-solid);
    }

    .ins-profile-meta {
      flex: 1;
      min-width: 0;
    }

    .ins-handle-line {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 10.5px;
      color: var(--text-muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .chen-activity-main {
      font-size: 14.5px;
      line-height: 1.5;
      color: var(--text-main);
      margin: 3px 0 0;
      font-weight: 500;
    }

    .thought-pill {
      margin: 0 0 8px;
      padding: 6px 10px;
      border-radius: 10px;
      background: var(--bg-elevated);
      font-size: 11.5px;
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: 6px;
      border-left: 2px solid var(--border-strong);
    }

    .chen-subnote {
      font-size: 11.5px;
      color: var(--text-muted);
      margin: 0 0 9px;
      line-height: 1.45;
    }

    .window-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding-top: 9px;
      border-top: 1px dashed var(--border-line);
    }

    .music-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11.5px;
      color: var(--accent-brown);
      background: var(--accent-coral-soft);
      padding: 4px 10px;
      border-radius: 999px;
      max-width: 68%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .music-bars {
      display: inline-flex;
      align-items: flex-end;
      gap: 2px;
      height: 10px;
    }

    .music-bars span {
      width: 2px;
      background: var(--accent-coral);
      border-radius: 1px;
      animation: eq 1.1s infinite ease-in-out;
    }

    .music-bars span:nth-child(1) { height: 4px; animation-delay: 0s; }
    .music-bars span:nth-child(2) { height: 9px; animation-delay: 0.25s; }
    .music-bars span:nth-child(3) { height: 6px; animation-delay: 0.5s; }

    @keyframes eq {
      0%, 100% { transform: scaleY(0.45); }
      50% { transform: scaleY(1); }
    }

    .poke-btn {
      border: 1px solid var(--border-strong);
      background: var(--bg-surface-solid);
      color: var(--accent-brown);
      font-size: 11.5px;
      padding: 4px 10px;
      border-radius: 999px;
      cursor: pointer;
      white-space: nowrap;
    }

    .he-receipt-bar {
      margin: 0 16px 9px;
      padding: 8px 13px;
      border-radius: 14px;
      background: var(--bg-elevated);
      border: 1px solid var(--border-line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 12px;
      color: var(--text-secondary);
      cursor: pointer;
    }

    .he-receipt-bar strong {
      color: var(--text-main);
      font-weight: 600;
    }

    .nav-tabs {
      margin: 0 16px 10px;
      padding: 4px;
      background: var(--bg-elevated);
      border-radius: 14px;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 4px;
      border: 1px solid var(--border-line);
    }

    .nav-tab {
      border: none;
      background: transparent;
      color: var(--text-secondary);
      padding: 7px 6px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
    }

    .nav-tab.active {
      background: var(--bg-surface-solid);
      color: var(--text-main);
      box-shadow: 0 2px 8px rgba(20, 19, 18, 0.07);
      font-weight: 600;
    }

    .tab-panel {
      display: none;
      flex: 1;
      flex-direction: column;
      padding: 0 16px;
      min-height: 0;
    }

    .tab-panel.active {
      display: flex;
    }

    .chat-box {
      flex: 1;
      background: var(--bg-surface);
      border: 1px solid var(--border-line);
      border-radius: 22px;
      padding: 13px;
      display: flex;
      flex-direction: column;
      min-height: 340px;
      box-shadow: var(--shadow-card);
    }

    .messages-scroll {
      flex: 1;
      overflow-y: auto;
      padding-right: 4px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: 44vh;
    }

    .msg-row {
      display: flex;
      flex-direction: column;
      max-width: 85%;
    }

    .msg-row.mine {
      align-self: flex-end;
      align-items: flex-end;
    }

    .msg-row.chen {
      align-self: flex-start;
      align-items: flex-start;
    }

    .msg-sender {
      font-size: 10.5px;
      color: var(--text-muted);
      margin: 0 4px 3px;
    }

    .msg-bubble {
      padding: 10px 13px;
      border-radius: 18px;
      font-size: 14px;
      line-height: 1.58;
      word-break: break-word;
      white-space: pre-wrap;
    }

    .msg-row.mine .msg-bubble {
      background: var(--bubble-mine);
      color: var(--bubble-mine-text);
      border-bottom-right-radius: 5px;
    }

    .msg-row.chen .msg-bubble {
      background: var(--bubble-chen);
      color: var(--bubble-chen-text);
      border-bottom-left-radius: 5px;
      border: 1px solid var(--border-line);
    }

    .typing-bubble {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 10px 14px;
      border-radius: 16px;
      border-bottom-left-radius: 5px;
      background: var(--bubble-chen);
      border: 1px solid var(--border-line);
    }

    .typing-dots span {
      display: inline-block;
      width: 5px;
      height: 5px;
      margin: 0 1px;
      border-radius: 50%;
      background: var(--text-muted);
      animation: bounce 1.2s infinite ease-in-out;
    }

    .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
    .typing-dots span:nth-child(3) { animation-delay: 0.4s; }

    @keyframes bounce {
      0%, 80%, 100% { opacity: 0.3; transform: translateY(0); }
      40% { opacity: 1; transform: translateY(-3px); }
    }

    .quick-prompts {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 9px 0 7px;
      scrollbar-width: none;
    }

    .quick-prompts::-webkit-scrollbar { display: none; }

    .prompt-chip {
      border: 1px solid var(--border-line);
      background: var(--bg-canvas);
      color: var(--text-secondary);
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      white-space: nowrap;
      cursor: pointer;
    }

    .composer-bar {
      display: flex;
      gap: 8px;
      margin-top: 2px;
    }

    .composer-input {
      flex: 1;
      min-width: 0;
      border: 1px solid var(--border-line);
      border-radius: 14px;
      padding: 11px 13px;
      font-size: 15px;
      background: var(--bg-surface-solid);
      color: var(--text-main);
      outline: none;
    }

    .composer-send {
      border: none;
      border-radius: 14px;
      padding: 0 18px;
      background: var(--accent-coral);
      color: var(--bubble-mine-text);
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
    }

    .composer-send:disabled {
      opacity: 0.5;
    }

    .panel-scroll {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .sub-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-line);
      border-radius: 20px;
      padding: 14px 15px;
      box-shadow: var(--shadow-card);
    }

    .sub-card-title {
      font-size: 12px;
      color: var(--text-muted);
      letter-spacing: 0.05em;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .presence-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }

    .tile-btn {
      border: 1px solid var(--border-line);
      background: var(--bg-canvas);
      color: var(--text-main);
      border-radius: 14px;
      padding: 10px 4px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      cursor: pointer;
    }

    .tile-btn .tile-emoji { font-size: 18px; }
    .tile-btn .tile-label { font-size: 13px; font-weight: 500; }
    .tile-btn .tile-place { font-size: 10px; color: var(--text-muted); }

    .tile-btn.active {
      background: var(--accent-coral-soft);
      border-color: var(--border-strong);
      font-weight: 600;
    }

    .custom-presence-row {
      display: flex;
      gap: 6px;
      margin-top: 10px;
    }

    .mini-input {
      border: 1px solid var(--border-line);
      border-radius: 11px;
      padding: 8px 10px;
      font-size: 13px;
      background: var(--bg-surface-solid);
      color: var(--text-main);
      outline: none;
      min-width: 0;
    }

    .mini-btn {
      border: none;
      border-radius: 11px;
      padding: 8px 12px;
      background: var(--accent-coral);
      color: var(--bubble-mine-text);
      font-size: 12px;
      cursor: pointer;
      white-space: nowrap;
    }

    .telemetry-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
    }

    .telemetry-item {
      background: var(--bg-canvas);
      border: 1px solid var(--border-line);
      border-radius: 12px;
      padding: 8px 10px;
    }

    .telemetry-label {
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 3px;
    }

    .telemetry-val {
      font-size: 12.5px;
      font-weight: 500;
      color: var(--text-main);
      word-break: break-word;
    }

    .prompt-preview-box {
      margin-top: 10px;
      padding: 9px 11px;
      border-radius: 11px;
      background: var(--bg-elevated);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 11px;
      line-height: 1.5;
      color: var(--text-secondary);
      white-space: pre-wrap;
      word-break: break-all;
    }

    .world-places {
      display: flex;
      gap: 7px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }

    .place-pill {
      border: 1px solid var(--border-line);
      background: var(--bg-canvas);
      color: var(--text-secondary);
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 12px;
      cursor: pointer;
    }

    .place-pill.active {
      background: var(--accent-coral);
      color: var(--bubble-mine-text);
      border-color: var(--accent-coral);
    }

    .polaroid-card {
      background: var(--bg-surface-solid);
      border: 1px solid var(--border-line);
      border-radius: 18px;
      padding: 11px;
      margin-bottom: 10px;
      box-shadow: var(--shadow-card);
    }

    .polaroid-visual {
      height: 88px;
      border-radius: 12px;
      background: linear-gradient(135deg, #DFD9CE 0%, #C7BFAEE8 55%, #9E9587 100%);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 10px 12px;
      color: #2B2620;
      margin-bottom: 8px;
    }

    body.theme-night .polaroid-visual {
      background: linear-gradient(135deg, #2B2927 0%, #1F1E24 60%, #151519 100%);
      color: #E8E2D6;
    }

    .polaroid-tag {
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      opacity: 0.75;
      font-family: ui-monospace, SFMono-Regular, monospace;
    }

    .polaroid-quote {
      font-family: var(--font-serif);
      font-size: 13px;
      line-height: 1.45;
      font-weight: 600;
    }

    .polaroid-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 11.5px;
      color: var(--text-secondary);
    }

    .like-btn {
      border: 1px solid var(--border-line);
      background: var(--bg-canvas);
      color: var(--text-secondary);
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 11px;
      cursor: pointer;
    }

    .like-btn.liked {
      background: rgba(210, 75, 75, 0.12);
      color: #C94A4A;
      border-color: rgba(210, 75, 75, 0.3);
    }

    .world-items-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .world-item-card {
      background: var(--bg-canvas);
      border: 1px solid var(--border-line);
      border-radius: 13px;
      padding: 10px 12px;
    }

    .world-item-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 4px;
    }

    .world-item-snippet {
      font-size: 12px;
      color: var(--text-secondary);
      line-height: 1.45;
    }

    .timeline-list {
      display: flex;
      flex-direction: column;
      gap: 9px;
      position: relative;
      padding-left: 14px;
    }

    .timeline-list::before {
      content: "";
      position: absolute;
      left: 4px;
      top: 5px;
      bottom: 5px;
      width: 1px;
      background: var(--border-strong);
    }

    .timeline-item {
      position: relative;
      font-size: 12px;
      line-height: 1.5;
    }

    .timeline-item::before {
      content: "";
      position: absolute;
      left: -13px;
      top: 6px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent-coral);
    }

    .timeline-time {
      color: var(--text-muted);
      font-family: ui-monospace, SFMono-Regular, monospace;
      margin-right: 6px;
    }

    .toast-banner {
      position: fixed;
      bottom: 22px;
      left: 50%;
      transform: translateX(-50%) translateY(20px);
      background: rgba(28, 27, 26, 0.92);
      color: #FCFAF5;
      padding: 8px 16px;
      border-radius: 999px;
      font-size: 12px;
      pointer-events: none;
      opacity: 0;
      transition: all 0.25s ease;
      z-index: 40;
      white-space: nowrap;
    }

    .toast-banner.show {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }

    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(20, 18, 16, 0.52);
      backdrop-filter: blur(4px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 16px;
      z-index: 50;
    }

    .modal-backdrop.open { display: flex; }

    .modal-card {
      width: 100%;
      max-width: 380px;
      background: var(--bg-surface-solid);
      border: 1px solid var(--border-line);
      border-radius: 22px;
      padding: 18px;
      color: var(--text-main);
    }
  </style>
</head>
<body class="style-ins">
  <main class="phone-stage">
    <header class="app-header">
      <div class="brand-group">
        <h1 class="brand-title">伴生体</h1>
        <span class="conn-pill" id="connBadge">
          <span class="conn-dot"></span>
          <span id="connText">连接中…</span>
        </span>
      </div>
      <div class="header-tools">
        <button type="button" class="icon-btn" id="skinToggleBtn" title="切换 Ins 玻璃风 / 旧书页风">✨ Ins风</button>
        <button type="button" class="icon-btn" id="themeToggleBtn" title="切换昼夜主题">
          <span id="themeIcon">☀</span>
          <span id="themeLabel">日间</span>
        </button>
        <button type="button" class="icon-btn" id="tokenModalBtn" title="管理连接令牌">⌁ 凭据</button>
      </div>
    </header>

    <section class="window-card">
      <div class="window-top-row">
        <div class="badge-row">
          <span class="world-badge" id="chenWorldBadge">🏡 空间读取中…</span>
          <span class="period-badge" id="chenPeriodBadge">此刻</span>
        </div>
        <div class="metrics-group">
          <span id="chenEmotionBadge">平静</span>
          <div class="energy-wrap">
            <span id="chenEnergyText">--%</span>
            <div class="energy-bar-bg">
              <div class="energy-bar-fill" id="chenEnergyFill" style="width: 75%;"></div>
            </div>
          </div>
        </div>
      </div>

      <div class="ins-profile-row">
        <div class="ins-avatar-wrap">
          <div class="ins-avatar-inner">沉</div>
          <span class="ins-online-dot" id="insOnlineDot"></span>
        </div>
        <div class="ins-profile-meta">
          <div class="ins-handle-line">
            <span>@chen.03</span>
            <span>·</span>
            <span id="chenWeatherMini">KUNMING</span>
          </div>
          <p class="chen-activity-main" id="chenActivityText">正在读取伴生体此刻的状态…</p>
        </div>
      </div>

      <div class="thought-pill" id="chenThoughtPill">
        <span>💭</span>
        <span id="chenThoughtText">正在同步他的心绪…</span>
      </div>

      <p class="chen-subnote" id="chenVisitNote"></p>

      <div class="window-footer">
        <div class="music-pill" id="chenMusicPill">
          <span class="music-bars" id="chenMusicBars" style="display:none;">
            <span></span><span></span><span></span>
          </span>
          <span id="chenMusicText">此刻很安静，没有放歌</span>
        </div>
        <button type="button" class="poke-btn" id="pokeChenBtn">👋 轻叩窗沿</button>
      </div>
    </section>

    <div class="he-receipt-bar" id="heReceiptBar">
      <span id="heReceiptText">📍 他知道你在：<strong>尚未同步手机状态</strong></span>
      <span>状态 ›</span>
    </div>

    <nav class="nav-tabs">
      <button type="button" class="nav-tab active" data-tab="chat">💬 对话</button>
      <button type="button" class="nav-tab" data-tab="presence">📡 我的状态</button>
      <button type="button" class="nav-tab" data-tab="world">🌿 小世界与痕迹</button>
    </nav>

    <section class="tab-panel active" id="panel-chat">
      <div class="chat-box">
        <div class="messages-scroll" id="messagesList"></div>
        <div class="quick-prompts">
          <button type="button" class="prompt-chip" data-prompt="你在干嘛呢？">你在干嘛呢？</button>
          <button type="button" class="prompt-chip" data-prompt="我刚忙完手头的事，歇会儿">我刚忙完，歇会儿</button>
          <button type="button" class="prompt-chip" data-prompt="刚才在听什么歌？">刚才在听什么歌？</button>
          <button type="button" class="prompt-chip" data-prompt="今天这边一切顺利">今天一切顺利</button>
        </div>
        <div class="composer-bar">
          <input type="text" id="chatInput" class="composer-input" autocomplete="off" placeholder="和伴生体说点什么…">
          <button type="button" id="chatSendBtn" class="composer-send">发送</button>
        </div>
      </div>
    </section>

    <section class="tab-panel" id="panel-presence">
      <div class="panel-scroll">
        <div class="sub-card">
          <div class="sub-card-title">
            <span>告诉他你正在做什么（点击即同步）</span>
            <span id="lastSyncTimeText">未同步</span>
          </div>
          <div class="presence-grid" id="presenceTiles">
            <button type="button" class="tile-btn" data-act="学习" data-place="学校">
              <span class="tile-emoji">📖</span>
              <span class="tile-label">学习</span>
              <span class="tile-place">学校/书桌</span>
            </button>
            <button type="button" class="tile-btn" data-act="听歌" data-place="路上">
              <span class="tile-emoji">🎧</span>
              <span class="tile-label">听歌</span>
              <span class="tile-place">耳机里</span>
            </button>
            <button type="button" class="tile-btn" data-act="休息" data-place="家里">
              <span class="tile-emoji">☕</span>
              <span class="tile-label">休息</span>
              <span class="tile-place">放空一下</span>
            </button>
            <button type="button" class="tile-btn" data-act="回家" data-place="家里">
              <span class="tile-emoji">🏠</span>
              <span class="tile-label">回家</span>
              <span class="tile-place">刚到家</span>
            </button>
            <button type="button" class="tile-btn" data-act="通勤路上" data-place="路上">
              <span class="tile-emoji">🚇</span>
              <span class="tile-label">在路上</span>
              <span class="tile-place">通勤出行</span>
            </button>
            <button type="button" class="tile-btn" data-act="吃饭" data-place="餐厅">
              <span class="tile-emoji">🍜</span>
              <span class="tile-label">吃饭</span>
              <span class="tile-place">补充能量</span>
            </button>
            <button type="button" class="tile-btn" data-act="刷视频" data-place="家里">
              <span class="tile-emoji">📱</span>
              <span class="tile-label">刷视频</span>
              <span class="tile-place">随便看看</span>
            </button>
            <button type="button" class="tile-btn" data-act="准备睡了" data-place="卧室">
              <span class="tile-emoji">🌙</span>
              <span class="tile-label">准备睡</span>
              <span class="tile-place">晚安</span>
            </button>
          </div>
          <div class="custom-presence-row">
            <input type="text" id="customActInput" class="mini-input" style="flex:1.3" placeholder="自定义活动（如：写论文）">
            <input type="text" id="customPlaceInput" class="mini-input" style="flex:1" placeholder="地点（如：图书馆）">
            <button type="button" id="customPresenceBtn" class="mini-btn">同步</button>
          </div>
        </div>

        <div class="sub-card">
          <div class="sub-card-title">
            <span>iOS 快捷指令 · 白名单状态摘要</span>
            <span>受限隐私保护</span>
          </div>
          <div class="telemetry-grid">
            <div class="telemetry-item">
              <div class="telemetry-label">设备 / Wi‑Fi</div>
              <div class="telemetry-val" id="telDeviceWifi">未上报</div>
            </div>
            <div class="telemetry-item">
              <div class="telemetry-label">当前应用 / 专注</div>
              <div class="telemetry-val" id="telAppFocus">未上报</div>
            </div>
            <div class="telemetry-item">
              <div class="telemetry-label">电量与充电</div>
              <div class="telemetry-val" id="telBattery">未上报</div>
            </div>
            <div class="telemetry-item">
              <div class="telemetry-label">手机音乐摘要</div>
              <div class="telemetry-val" id="telMusic">未上报</div>
            </div>
          </div>
          <div class="prompt-preview-box" id="promptBlockPreview">等待手机或快捷指令同步状态…</div>
        </div>
      </div>
    </section>

    <section class="tab-panel" id="panel-world">
      <div class="panel-scroll">
        <div class="sub-card">
          <div class="sub-card-title">
            <span>小世界空间与拍立得瞬间</span>
            <span id="worldCurrentStatus">当前空间</span>
          </div>
          <div class="world-places" id="worldPlaceButtons">
            <button type="button" class="place-pill" data-world-cmd="enter_small_world">🏡 去小世界找他</button>
            <button type="button" class="place-pill" data-world-cmd="leave_small_world">🖥️ 回电脑桌面</button>
          </div>
          <div class="polaroid-card">
            <div class="polaroid-visual">
              <div class="polaroid-tag" id="polaroidTagText">POLAROID · SMALL WORLD</div>
              <div class="polaroid-quote" id="polaroidQuoteText">“安静待着，把今天遇到的小事记下来留给你看。”</div>
            </div>
            <div class="polaroid-foot">
              <span id="polaroidLocText">📍 伴生体的空间记录</span>
              <button type="button" class="like-btn" id="polaroidLikeBtn">♡ 喜欢这张瞬间</button>
            </div>
          </div>
          <div class="world-items-list" id="worldItemsList"></div>
        </div>

        <div class="sub-card">
          <div class="sub-card-title">
            <span>他今天留下的痕迹（day_log）</span>
            <span>实时只读</span>
          </div>
          <div class="timeline-list" id="dayLogTimeline"></div>
        </div>
      </div>
    </section>
  </main>

  <div class="toast-banner" id="toastBanner"></div>

  <div class="modal-backdrop" id="tokenModal">
    <div class="modal-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <strong style="font-size:16px;">连接凭据（X-Chen-Token）</strong>
        <button type="button" class="icon-btn" id="closeTokenModalBtn">✕</button>
      </div>
      <p style="font-size:12px;color:var(--text-secondary);line-height:1.5;margin:0 0 10px;">
        将本页添加到 iOS 主屏幕后，如遇重连提示，可在此粘贴电脑端「手机连接电脑」窗口中的 Token。
      </p>
      <input type="text" id="tokenInputBox" class="composer-input" style="width:100%;margin-bottom:10px;font-size:13px;" placeholder="粘贴 chen-... 令牌">
      <div style="display:flex;justify-content:flex-end;gap:8px;">
        <button type="button" class="mini-btn" id="clearTokenBtn">清除本机令牌</button>
        <button type="button" class="mini-btn" id="saveTokenBtn">保存并重连</button>
      </div>
    </div>
  </div>

  <script>
    (function () {
      let token = "";
      try {
        token = new URLSearchParams(location.hash.slice(1)).get("token")
          || new URLSearchParams(location.search).get("token")
          || sessionStorage.getItem("chen-token")
          || localStorage.getItem("chen-token")
          || "";
        if (token) {
          sessionStorage.setItem("chen-token", token);
          localStorage.setItem("chen-token", token);
        }
      } catch (e) {}
      try { history.replaceState(null, "", "/mobile"); } catch (e) {}

      const toastEl = document.getElementById("toastBanner");
      let toastTimer = null;
      function toast(text) {
        if (!text) return;
        toastEl.textContent = text;
        toastEl.classList.add("show");
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2400);
      }

      async function api(url, opts = {}) {
        opts.headers = Object.assign({ "X-Chen-Token": token }, opts.headers || {});
        const r = await fetch(url, opts);
        let data = {};
        try { data = await r.json(); } catch (e) {}
        if (!r.ok) {
          throw new Error(data.error || ("请求失败（" + r.status + "）"));
        }
        return data;
      }

      let manualTheme = null;
      function applyTheme(isNight) {
        document.body.classList.toggle("theme-night", !!isNight);
        document.getElementById("themeIcon").textContent = isNight ? "☾" : "☀";
        document.getElementById("themeLabel").textContent = isNight ? "夜色" : "日间";
      }

      document.getElementById("themeToggleBtn").addEventListener("click", () => {
        const nextNight = !document.body.classList.contains("theme-night");
        manualTheme = nextNight ? "night" : "day";
        applyTheme(nextNight);
      });

      const skinToggleBtn = document.getElementById("skinToggleBtn");
      skinToggleBtn.addEventListener("click", () => {
        const isIns = document.body.classList.toggle("style-ins");
        skinToggleBtn.textContent = isIns ? "✨ Ins风" : "📜 宣纸";
        toast(isIns ? "已切换：Ins 极简磨砂玻璃风" : "已切换：旧书页雪松木暖棕风");
      });

      function switchTab(tabName) {
        document.querySelectorAll(".nav-tab").forEach(t => {
          t.classList.toggle("active", t.getAttribute("data-tab") === tabName);
        });
        document.querySelectorAll(".tab-panel").forEach(p => {
          p.classList.toggle("active", p.id === "panel-" + tabName);
        });
      }
      document.querySelectorAll(".nav-tab").forEach(btn => {
        btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab")));
      });
      document.getElementById("heReceiptBar").addEventListener("click", () => switchTab("presence"));

      const messagesList = document.getElementById("messagesList");
      let lastHistorySignature = "";

      function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/\r\n?/g, '\n').replace(/[&<>\"']/g, ch => ({
          "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
        }[ch]));
      }

      function renderMessagesList(items) {
        const sig = JSON.stringify(items || []);
        if (sig === lastHistorySignature) return;
        lastHistorySignature = sig;
        messagesList.innerHTML = "";
        if (!items || !items.length) {
          const empty = document.createElement("div");
          empty.style.cssText = "text-align:center;color:var(--text-muted);font-size:13px;padding:28px 0;";
          empty.textContent = "还没有新的对话，跟伴生体打个招呼吧";
          messagesList.appendChild(empty);
          return;
        }
        items.forEach(m => {
          const row = document.createElement("div");
          row.className = "msg-row " + (m.role === "he" ? "mine" : "chen");
          const sender = document.createElement("div");
          sender.className = "msg-sender";
          const tsShort = m.ts ? String(m.ts).slice(-5) : "";
          sender.textContent = (m.role === "he" ? "你" : "伴生体") + (tsShort ? " · " + tsShort : "");
          const bubble = document.createElement("div");
          bubble.className = "msg-bubble";
          bubble.textContent = m.text || "";
          row.appendChild(sender);
          row.appendChild(bubble);
          messagesList.appendChild(row);
        });
        messagesList.scrollTop = messagesList.scrollHeight;
      }

      function appendLocalBubble(role, text) {
        const row = document.createElement("div");
        row.className = "msg-row " + (role === "he" ? "mine" : "chen");
        const sender = document.createElement("div");
        sender.className = "msg-sender";
        sender.textContent = (role === "he" ? "你" : "伴生体") + " · 刚刚";
        const bubble = document.createElement("div");
        bubble.className = "msg-bubble";
        bubble.textContent = text;
        row.appendChild(sender);
        row.appendChild(bubble);
        messagesList.appendChild(row);
        messagesList.scrollTop = messagesList.scrollHeight;
      }

      async function loadAll() {
        const connBadge = document.getElementById("connBadge");
        const connText = document.getElementById("connText");
        try {
          const s = await api("/api/status");
          connBadge.classList.remove("err");
          connText.textContent = "已连接";

          if (manualTheme === null && typeof s.is_night === "boolean") {
            applyTheme(s.is_night);
          }

          const isSmall = s.current_world === "small_world";
          const worldName = isSmall ? "🏡 小世界" : "🖥️ 桌面空间";
          const place = s.activity_place ? (" · " + s.activity_place) : "";
          document.getElementById("chenWorldBadge").textContent = worldName + place;
          document.getElementById("chenPeriodBadge").textContent = s.period || (s.awake === false ? "熟睡中" : "清醒");

          const emo = s.emotion || {};
          document.getElementById("chenEmotionBadge").textContent =
            (emo.name || "平静") + (emo.value ? (" · " + emo.value) : "");

          const energy = typeof s.energy === "number" ? Math.round(s.energy) : 80;
          document.getElementById("chenEnergyText").textContent = energy + "%";
          const fill = document.getElementById("chenEnergyFill");
          fill.style.width = Math.max(5, Math.min(100, energy)) + "%";
          fill.style.background = energy <= 30 ? "var(--accent-coral)" : "var(--accent-sage)";

          document.getElementById("chenWeatherMini").textContent = s.weather_summary || "KUNMING";
          document.getElementById("chenActivityText").textContent = s.activity || "安静待着";
          document.getElementById("chenThoughtText").textContent = s.thought || "安静待着，留神听着手机这头的动静。";
          document.getElementById("chenVisitNote").textContent = s.last_visit_summary || "";
          document.getElementById("insOnlineDot").style.background =
            s.awake === false ? "var(--text-muted)" : "var(--accent-sage)";

          const music = s.music || {};
          const musicBars = document.getElementById("chenMusicBars");
          const musicText = document.getElementById("chenMusicText");
          if (music.title) {
            musicBars.style.display = music.playing ? "inline-flex" : "none";
            musicText.textContent = (music.playing ? "正在听" : "暂停了") +
              "《" + music.title + "》" + (music.artist ? " · " + music.artist : "");
          } else {
            musicBars.style.display = "none";
            musicText.textContent = "此刻很安静，没有放歌";
          }

          // 渲染手机外部存在感状态
          const e = s.external_presence || {};
          const loc = e.place ? (e.place + " · ") : "";
          const extras = [];
          if (e.app) extras.push(e.app);
          if (e.focus) extras.push(e.focus);
          if (typeof e.battery === "number") extras.push("电量 " + e.battery + "%" + (e.charging ? "⚡" : ""));
          const suffix = extras.length ? ("（" + extras.slice(0, 2).join(" · ") + "）") : "";
          document.getElementById("heReceiptText").innerHTML = e.activity
            ? ("📍 他知道你在：<strong>" + escapeHtml(loc + e.activity) + "</strong>" + escapeHtml(suffix))
            : "📍 他知道你在：<strong>尚未同步手机状态</strong>";

          document.getElementById("lastSyncTimeText").textContent =
            e.updated_at ? ("更新于 " + String(e.updated_at).slice(-8, -3)) : "未同步";
          document.getElementById("telDeviceWifi").textContent =
            (e.device || "ios") + " · " + (e.wifi || "未上报");
          document.getElementById("telAppFocus").textContent =
            (e.app || "未上报") + " · " + (e.focus || "无专注");
          document.getElementById("telBattery").textContent =
            (typeof e.battery === "number" ? (e.battery + "%") : "未上报") +
            (e.charging ? "（充电中）" : "");
          document.getElementById("telMusic").textContent =
            e.music_title ? (e.music_title + (e.music_artist ? " / " + e.music_artist : "")) : "无播放记录";
          document.getElementById("promptBlockPreview").textContent =
            s.prompt_block || "等待手机或快捷指令同步状态…";

          document.querySelectorAll("#presenceTiles .tile-btn").forEach(btn => {
            btn.classList.toggle("active", btn.getAttribute("data-act") === e.activity);
          });

          document.getElementById("worldCurrentStatus").textContent =
            "当前在：" + worldName + place;
          document.getElementById("polaroidLocText").textContent =
            "📍 " + (s.activity_place || (isSmall ? "小世界" : "桌面空间"));
          if (s.last_visit_summary) {
            document.getElementById("polaroidQuoteText").textContent = "“" + s.last_visit_summary + "”";
          }
        } catch (err) {
          connBadge.classList.add("err");
          connText.textContent = "未认证/离线";
        }

        try {
          const h = await api("/api/chat/history");
          if (h && Array.isArray(h.messages)) {
            renderMessagesList(h.messages);
          }
        } catch (e) {}

        try {
          const d = await api("/api/day_log");
          const itemBox = document.getElementById("worldItemsList");
          itemBox.innerHTML = "";
          (d.world_items || []).forEach(item => {
            const card = document.createElement("div");
            card.className = "world-item-card";
            card.innerHTML =
              "<div class='world-item-head'><span>" + escapeHtml(item.icon) + " " + escapeHtml(item.title) +
              "</span><span style='font-size:11px;color:var(--text-muted);font-weight:400;'>" +
              escapeHtml(item.place) + "</span></div><div class='world-item-snippet'>" + escapeHtml(item.snippet) + "</div>";
            itemBox.appendChild(card);
          });

          const tlBox = document.getElementById("dayLogTimeline");
          tlBox.innerHTML = "";
          const logs = d.logs || [];
          if (!logs.length) {
            tlBox.innerHTML = "<div style='font-size:12px;color:var(--text-muted);'>今天还没有新的经历记录。</div>";
          } else {
            logs.forEach(log => {
              const row = document.createElement("div");
              row.className = "timeline-item";
              row.innerHTML = "<span class='timeline-time'>" + escapeHtml(log.time) + "</span><span>" + escapeHtml(log.text) + "</span>";
              tlBox.appendChild(row);
            });
          }
        } catch (e) {}
      }

      async function syncPresence(activity, place) {
        toast("正在同步状态…");
        try {
          await api("/api/presence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device: "ios", activity, place: place || "" })
          });
          toast("✓ 已告诉伴生体：" + (place ? place + " · " : "") + activity);
          loadAll();
        } catch (err) {
          toast(err.message);
        }
      }

      document.querySelectorAll("#presenceTiles .tile-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          syncPresence(btn.getAttribute("data-act"), btn.getAttribute("data-place"));
        });
      });

      document.getElementById("customPresenceBtn").addEventListener("click", () => {
        const actEl = document.getElementById("customActInput");
        const placeEl = document.getElementById("customPlaceInput");
        const act = actEl.value.trim();
        const place = placeEl.value.trim();
        if (!act) {
          toast("请先填写你正在做的事");
          return;
        }
        actEl.value = "";
        placeEl.value = "";
        syncPresence(act, place);
      });

      const chatInput = document.getElementById("chatInput");
      const chatSendBtn = document.getElementById("chatSendBtn");

      async function sendChat(text) {
        const value = (text || "").trim();
        if (!value) return;
        chatInput.value = "";
        appendLocalBubble("he", value);
        chatSendBtn.disabled = true;

        const typingRow = document.createElement("div");
        typingRow.className = "msg-row chen";
        typingRow.innerHTML =
          "<div class='msg-sender'>伴生体 · 正在想…</div>" +
          "<div class='typing-bubble'><span class='typing-dots'><span></span><span></span><span></span></span></div>";
        messagesList.appendChild(typingRow);
        messagesList.scrollTop = messagesList.scrollHeight;

        try {
          const r = await api("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: value, device: "ios" })
          });
          typingRow.remove();
          if (r.reply) appendLocalBubble("chen", r.reply);
          loadAll();
        } catch (err) {
          typingRow.remove();
          toast(err.message);
        } finally {
          chatSendBtn.disabled = false;
          chatInput.focus();
        }
      }

      chatSendBtn.addEventListener("click", () => sendChat(chatInput.value));
      chatInput.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.isComposing) {
          e.preventDefault();
          sendChat(chatInput.value);
        }
      });
      document.querySelectorAll(".prompt-chip").forEach(chip => {
        chip.addEventListener("click", () => sendChat(chip.getAttribute("data-prompt")));
      });

      document.getElementById("pokeChenBtn").addEventListener("click", async () => {
        try {
          const r = await api("/api/poke", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind: "window_tap", device: "ios" })
          });
          toast(r.message || "轻轻叩了下他的窗沿");
          loadAll();
        } catch (err) {
          toast(err.message);
        }
      });

      const polaroidLikeBtn = document.getElementById("polaroidLikeBtn");
      polaroidLikeBtn.addEventListener("click", async () => {
        const liked = polaroidLikeBtn.classList.toggle("liked");
        polaroidLikeBtn.textContent = liked ? "♥ 已回应他的瞬间" : "♡ 喜欢这张瞬间";
        if (liked) {
          try {
            await api("/api/poke", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ kind: "polaroid_like", device: "ios" })
            });
            toast("伴生体收到了你在小世界拍立得上的红心 ♥");
            loadAll();
          } catch (e) {}
        }
      });

      document.querySelectorAll("#worldPlaceButtons .place-pill").forEach(btn => {
        btn.addEventListener("click", async () => {
          const cmd = btn.getAttribute("data-world-cmd");
          try {
            await api("/api/world/command", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ action: cmd })
            });
            toast(cmd === "enter_small_world" ? "已唤伴生体前往小世界" : "已唤伴生体回到桌面空间");
            loadAll();
          } catch (err) {
            toast(err.message);
          }
        });
      });

      // 凭据弹窗
      const tokenModal = document.getElementById("tokenModal");
      const tokenInputBox = document.getElementById("tokenInputBox");
      document.getElementById("tokenModalBtn").addEventListener("click", () => {
        tokenInputBox.value = token;
        tokenModal.classList.add("open");
      });
      document.getElementById("closeTokenModalBtn").addEventListener("click", () => {
        tokenModal.classList.remove("open");
      });
      document.getElementById("clearTokenBtn").addEventListener("click", () => {
        token = "";
        try {
          sessionStorage.removeItem("chen-token");
          localStorage.removeItem("chen-token");
        } catch (e) {}
        tokenInputBox.value = "";
        tokenModal.classList.remove("open");
        toast("已清除本机令牌");
        loadAll();
      });
      document.getElementById("saveTokenBtn").addEventListener("click", () => {
        token = tokenInputBox.value.trim();
        try {
          sessionStorage.setItem("chen-token", token);
          localStorage.setItem("chen-token", token);
        } catch (e) {}
        tokenModal.classList.remove("open");
        toast("令牌已更新，正在重新连接…");
        loadAll();
      });

      loadAll();
      setInterval(loadAll, 20000);
    })();
  </script>
</body>
</html>"""

            def do_GET(self):
                if not self._rate_allowed():
                    self._text("请求过于频繁，请稍后再试。", 429)
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._text("chen-remote OK")
                    return
                if parsed.path in ("/mobile", "/"):
                    body = self._mobile_page().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path in ("/api/presence", "/send"):
                    self._json({"ok": False, "error": "请使用 POST 请求"}, 405)
                    return
                if not self._authorized():
                    self._json({"ok": False, "error": "unauthorized"}, 401)
                    return
                if parsed.path == "/api/status":
                    self._json(outer._get_enriched_status())
                    return
                if parsed.path == "/api/chat/history":
                    qs = parse_qs(parsed.query)
                    limit = 40
                    try:
                        if "limit" in qs:
                            limit = int(qs["limit"][0])
                    except Exception:
                        limit = 40
                    self._json({"ok": True, "messages": outer._get_chat_history(limit)})
                    return
                if parsed.path == "/api/day_log":
                    self._json({
                        "ok": True,
                        "logs": outer._get_today_logs(18),
                        "world_items": outer._get_world_items(10),
                    })
                    return
                if parsed.path == "/api/world/snapshot":
                    if outer.world_bridge is None:
                        self._json({"ok": False, "error": "小世界桥接不可用"}, 503)
                        return
                    self._json(outer.world_bridge.snapshot())
                    return
                if parsed.path == "/api/world/runtime":
                    if outer.world_bridge is None:
                        self._json({"ok": False, "error": "小世界桥接不可用"}, 503)
                        return
                    self._json({"ok": True, "runtime": outer.world_bridge.runtime()})
                    return
                self._text("not found", 404)

            def do_OPTIONS(self):
                if not self._rate_allowed():
                    self._text("请求过于频繁，请稍后再试。", 429)
                    return
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                if not self._rate_allowed():
                    self._json({"ok": False, "error": "请求过于频繁，请稍后再试"}, 429)
                    return
                if not self._authorized():
                    self._json({"ok": False, "error": "unauthorized"}, 401)
                    return
                parsed = urlparse(self.path)
                try:
                    data = self._read_json()
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
                if parsed.path == "/api/presence":
                    details = {key: data.get(key) for key in
                               ("app", "focus", "wifi", "music_title", "music_artist", "battery", "charging")}
                    state = outer.chen.update_external_presence(
                        device=data.get("device", "ios"),
                        activity=data.get("activity", ""),
                        place=data.get("place", ""),
                        **details,
                    )
                    self._json({"ok": True, "external_presence": state})
                    return
                if parsed.path == "/api/poke":
                    kind = str(data.get("kind", "window_tap") or "window_tap")
                    snap = outer._get_enriched_status()
                    awake = bool(snap.get("awake", True))
                    place = snap.get("activity_place") or "这边"
                    try:
                        day_log = getattr(outer.chen, "day_log", None)
                        if day_log and hasattr(day_log, "log"):
                            log_text = "用户在手机上给小世界拍立得点了个红心" if kind == "polaroid_like" else "用户从手机端轻轻叩了下窗沿"
                            day_log.log("phone", log_text, world=snap.get("current_world", "desktop"))
                    except Exception:
                        pass
                    if not awake:
                        msg = "他睡得很熟，呼吸平稳，没有被吵醒。"
                    elif kind == "polaroid_like":
                        msg = f"伴生体在「{place}」收到了你的红心 ♥"
                    else:
                        msg = f"伴生体从「{place}」抬起头朝你这边看了一眼。"
                    self._json({"ok": True, "message": msg})
                    return
                if parsed.path == "/api/world/command":
                    action = data.get("action")
                    if action == "enter_small_world" and hasattr(outer.chen, "enter_small_world"):
                        try:
                            outer.chen.enter_small_world()
                            self._json({"ok": True, "current_world": "small_world"})
                            return
                        except Exception:
                            pass
                    elif action == "leave_small_world" and hasattr(outer.chen, "leave_small_world"):
                        try:
                            outer.chen.leave_small_world()
                            self._json({"ok": True, "current_world": "desktop"})
                            return
                        except Exception:
                            pass
                    if outer.world_bridge is None:
                        self._json({"ok": False, "error": "小世界桥接不可用"}, 503)
                        return
                    code, result = outer.world_bridge.command(data)
                    self._json(result, code)
                    return
                if parsed.path in ("/api/chat", "/send"):
                    text = data.get("text", "")
                    if not isinstance(text, str):
                        self._json({"ok": False, "error": "text 必须是字符串"}, 400)
                        return
                    text = text.strip()
                    if not text:
                        self._json({"ok": False, "error": "empty text"}, 400)
                        return
                    if len(text) > outer.MAX_CHAT_CHARS:
                        self._json({"ok": False, "error": "消息过长"}, 413)
                        return
                    if not outer._chat_lock.acquire(timeout=0.5):
                        self._json({"ok": False, "error": "伴生体正在处理上一条消息，请稍后再试"}, 429)
                        return
                    try:
                        try:
                            outer.ui_queue.put_nowait(("remote_he", text))
                        except queue.Full:
                            self._json({"ok": False, "error": "桌面消息队列已满，请稍后再试"}, 503)
                            return
                        reply = outer.chen.chat(text)
                    except Exception as e:
                        print(f"[remote] 聊天出错：{e}")
                        try:
                            outer.ui_queue.put_nowait(("remote_reply", "暂时没能回应。"))
                        except queue.Full:
                            pass
                        self._json({"ok": False, "error": "暂时没能回应"}, 503)
                        return
                    finally:
                        outer._chat_lock.release()
                    if not reply:
                        try:
                            outer.ui_queue.put_nowait(("remote_reply", ""))
                        except queue.Full:
                            pass
                        self._json({"ok": False, "error": "模型暂时无法回应，请稍后重试"}, 503)
                        return
                    try:
                        outer.ui_queue.put_nowait(("remote_reply", reply or ""))
                    except queue.Full:
                        pass
                    self._json({"ok": True, "reply": reply or ""})
                    return
                self._json({"ok": False, "error": "not found"}, 404)

        return Handler
