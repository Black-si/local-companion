"""
模型服务模块 - 可选的无界面推理后端。
公开版不携带模型或 llama-server 路径；用户可以在 system/config.json 中自行配置。

设计：
- ensure_running(): 若目标端口已有健康后端则直接复用；否则后台启动 llama-server 并等待就绪。
- stop(): 仅关闭由本模块启动的进程；复用的外部后端不动。
- 模型走 mmap（不锁内存），系统内存紧张时可被 Windows 自动回收。
"""

import os
import subprocess
import threading
import time
from pathlib import Path

import requests


# Windows 下隐藏子进程控制台窗口的标志
_CREATE_NO_WINDOW = 0x08000000


class ModelServer:
    """无界面 llama-server 生命周期管理"""

    def __init__(self, config=None, log_dir=None):
        config = config or {}
        self.server_path = str(config.get("server_path", "") or "").strip()
        self.model_path = str(config.get("model_path", "") or "").strip()
        self.host = config.get("host", "127.0.0.1")
        self.port = int(config.get("port", 1234))
        self.alias = config.get("alias", "qwen")
        self.ctx_size = int(config.get("ctx_size", 8192))
        self.gpu_layers = int(config.get("gpu_layers", 33))
        self.threads = int(config.get("threads", 6))
        self.parallel = int(config.get("parallel", 1))
        self.kv_cache_type = config.get("kv_cache_type", "q8_0")
        self.flash_attn = config.get("flash_attn", True)
        self.load_mode = config.get("load_mode", "mmap")
        self.batch_size = int(config.get("batch_size", 2048))
        self.ubatch_size = int(config.get("ubatch_size", 512))
        self.startup_timeout = float(config.get("startup_timeout", 120))
        self.backend_dir = str(Path(self.server_path).parent)
        self.cuda_vendor_dir = str(config.get("cuda_vendor_dir", "") or "").strip()

        self.base_url = f"http://{self.host}:{self.port}"
        self._process = None
        self._owns_process = False
        self._log_file = None
        self._log_dir = Path(log_dir) if log_dir else Path("logs")
        self._lifecycle_lock = threading.RLock()

    # ---------- 健康探测 ----------

    def is_healthy(self):
        """后端是否就绪（/health 返回 200）"""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=3)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ---------- 启动 ----------

    def _build_args(self):
        args = [
            self.server_path,
            "--model", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "--alias", self.alias,
            "--ctx-size", str(self.ctx_size),
            "--n-gpu-layers", str(self.gpu_layers),
            "--batch-size", str(self.batch_size),
            "--ubatch-size", str(self.ubatch_size),
            "--threads", str(self.threads),
            "--parallel", str(self.parallel),
            "--cache-type-k", self.kv_cache_type,
            "--cache-type-v", self.kv_cache_type,
            "--load-mode", self.load_mode,
            "--jinja",          # 用模型内嵌的聊天模板
            "--no-webui",       # 不挂网页界面
        ]
        if self.flash_attn:
            args += ["--flash-attn", "on"]
        return args

    def _spawn(self):
        if not self.server_path or not self.model_path:
            raise FileNotFoundError(
                "未配置 server_path/model_path；请在 system/config.json 中配置后再启用模型服务"
            )
        if not Path(self.server_path).is_file():
            raise FileNotFoundError(f"找不到 llama-server：{self.server_path}")
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"找不到模型文件：{self.model_path}")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / "llama_server.log"
        log_file = open(log_path, "ab", buffering=0)
        env = dict(os.environ)
        extra = [self.backend_dir, self.cuda_vendor_dir]
        env["PATH"] = os.pathsep.join([p for p in extra if p] + [env.get("PATH", "")])
        kwargs = dict(
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
            cwd=self.backend_dir,
            env=env,
        )
        try:
            self._process = subprocess.Popen(self._build_args(), **kwargs)
            self._log_file = log_file
            self._owns_process = True
        except Exception:
            log_file.close()
            raise

    def ensure_running(self):
        """确保后端可用，返回 True；超时失败返回 False"""
        with self._lifecycle_lock:
            # 已有健康后端（可能是外部已启动的）→ 直接复用
            if self.is_healthy():
                return True

            if self._process is not None and self._process.poll() is None:
                return False
            if self._process is not None:
                self.stop()

            # 端口被占用但不健康，多半是残留进程在加载/卡死；交给启动流程覆盖
            try:
                self._spawn()
            except OSError as exc:
                print(f"[模型服务] 启动失败：{exc}")
                self.stop()
                return False

            deadline = time.time() + self.startup_timeout
            while time.time() < deadline:
                # 子进程提前退出 → 启动失败
                if self._process.poll() is not None:
                    self.stop()
                    return False
                if self.is_healthy():
                    return True
                time.sleep(1)
            self.stop()
            return False

    # ---------- 停止 ----------

    def stop(self):
        """关闭由本模块启动的后端"""
        with self._lifecycle_lock:
            if self._owns_process and self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=8)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
            self._owns_process = False
            self._process = None
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                finally:
                    self._log_file = None
