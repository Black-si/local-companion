# -*- coding: utf-8 -*-
"""
phone_push.py —— 伴生体 → iPhone 的通知桥（V0.1）
================================================
伴生体本体继续住在电脑里；这只是"给他接的一根线"：

  伴生体主动开口 ──→ PhonePush.push() ──→ 本地 ntfy 服务器 ──→ iPhone 弹通知

设计约束（继承项目底线）：
- 默认全本地：ntfy 服务端是项目目录里的单文件二进制（tools/ntfy/ntfy.exe），
  由伴生体进程以子进程方式拉起，随伴生体一起生一起死；消息不经过任何第三方。
  代价：手机须和电脑连同一 WiFi（出门在外收不到，那是"服务器时代"的事）。
- 可选模式 mode="ntfy_sh"：走 ntfy.sh 公共服务器，出门也能收，
  但消息文本会经过第三方——默认关闭，由用户显式开启。
- 推送是"尽力而为"：失败只记一行日志，绝不影响伴生体的主循环。
- 线程：push 在后台线程发，发HTTP的几秒不阻塞决策循环。
"""
import json
import os
import secrets
import socket
import subprocess
import threading
import urllib.request
from pathlib import Path

DEFAULT_LOCAL_PORT = 8080
PUSH_TIMEOUT = 5     # 秒：发不出去就拉倒，别堵着
TITLE = "伴生体"


class PhonePush:
    """伴生体 → 手机 的单向通知桥。"""

    def __init__(self, memory_dir, config=None):
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        self.mode = cfg.get("mode", "local")          # local | ntfy_sh
        self.local_url = cfg.get("local_url", f"http://127.0.0.1:{DEFAULT_LOCAL_PORT}")
        self.remote_url = cfg.get("remote_url", "https://ntfy.sh")
        # 本地服务器开了认证：凭据放 config（局域网内简单密码即可）
        self.username = os.getenv("CHEN_NTFY_USERNAME") or cfg.get("username", "")
        self.password = os.getenv("CHEN_NTFY_PASSWORD") or cfg.get("password", "")
        # topic 是"收件箱的名字"：生成一次就固定，持久化在 memory/phone_push.json
        state_file = Path(memory_dir) / "phone_push.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if not state.get("topic"):
            state["topic"] = "chen-" + secrets.token_hex(4)
            try:
                state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        self.topic = cfg.get("topic") or state["topic"]
        self._state_file = state_file
        self._proc = None
        self._lock = threading.Lock()

    # ---------- 本地 ntfy 服务端（随伴生体拉起） ----------

    def start_local_server(self, tools_dir):
        """local 模式下拉起 ntfy serve（子进程，伴生体死它死，守护进程负责一起重启）。"""
        with self._lock:
            if not self.enabled or self.mode != "local":
                return
            if self._proc is not None and self._proc.poll() is None:
                return
            self._proc = None
        ntfy_dir = Path(tools_dir) / "ntfy"
        exe = ntfy_dir / "ntfy.exe"
        if not exe.exists():
            print("[phone_push] 未找到 ntfy.exe，本地推送不可用（不影响其他功能）")
            return
        if self._port_busy():
            return  # 已经有一个 ntfy 在跑（比如上次没退干净），直接用
        log_fp = None
        try:
            log_file = ntfy_dir / "ntfy_runtime.log"
            log_fp = open(log_file, "a", encoding="utf-8")
            kwargs = {
                "cwd": str(ntfy_dir),
                "stdout": log_fp,
                "stderr": log_fp,
            }
            # ntfy 是后台通知服务，不应额外弹出一个黑色控制台窗口。
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                [str(exe), "serve", "-c", str(ntfy_dir / "server.yml")], **kwargs
            )
            with self._lock:
                self._proc = process
            # 启动失败时尽早给出诊断；正常运行不阻塞主线程。
            threading.Thread(target=self._check_started, args=(log_fp,), daemon=True,
                             name="chen-ntfy-check").start()
            print(f"[phone_push] 本地通知服务已启动（topic: {self.topic}，仅局域网）")
        except Exception as e:
            if log_fp:
                try:
                    log_fp.close()
                except Exception:
                    pass
            print(f"[phone_push] 启动 ntfy 失败：{e}")

    def _check_started(self, log_fp=None):
        threading.Event().wait(1.0)
        try:
            if self._proc and self._proc.poll() is not None:
                print(f"[phone_push] ntfy 启动后退出，详见 tools/ntfy/ntfy_runtime.log（code={self._proc.returncode}）")
        finally:
            try:
                if log_fp:
                    log_fp.close()
            except Exception:
                pass

    def _port_busy(self):
        try:
            s = socket.create_connection(("127.0.0.1", DEFAULT_LOCAL_PORT), timeout=1)
            s.close()
            return True
        except Exception:
            return False

    def stop(self):
        with self._lock:
            process = self._proc
            self._proc = None
        if process:
            try:
                process.terminate()
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except Exception:
                    pass
            except Exception:
                pass

    # ---------- 推送 ----------

    def push(self, text, title=TITLE):
        """发一条通知到手机。只在后台线程里调用；失败静默（记日志）。"""
        if not self.enabled or not (text or "").strip():
            return
        body = {
            "topic": self.topic,
            "title": title,
            "message": text.strip()[:200],
            "priority": 3,
        }
        url = self.local_url if self.mode == "local" else self.remote_url
        t = threading.Thread(target=self._send, args=(url, body), daemon=True,
                             name="chen-phone-push")
        t.start()

    def _send(self, url, body):
        try:
            headers = {"Content-Type": "application/json"}
            if self.username:
                import base64
                _cred = base64.b64encode(
                    f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {_cred}"
            req = urllib.request.Request(
                url + "/",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=PUSH_TIMEOUT) as resp:
                if resp.status >= 400:
                    print(f"[phone_push] 推送被拒：HTTP {resp.status}")
        except Exception as e:
            print(f"[phone_push] 推送失败（手机可能不在同一WiFi）：{e}")


if __name__ == "__main__":
    # 独立自测：不启动服务器，只验证 JSON 组装与发送路径（对空端口应静默失败）
    import tempfile
    pp = PhonePush(tempfile.mkdtemp(), {"enabled": True, "mode": "local"})
    print("topic:", pp.topic)
    pp.push("测试：看到和看见不一样")
    print("push 已异步发出（无服务器时会打印一行失败日志，属预期）")
