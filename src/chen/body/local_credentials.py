"""仅在本机读取的凭据文件；环境变量由调用方优先处理。"""
import json
from pathlib import Path


def load_credentials(base_dir):
    path = Path(base_dir) / "system" / "credentials.local.json"
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("凭据文件必须是 JSON 对象")
        return {key: value.strip() for key, value in values.items()
                if key in {"api_key", "ntfy_username", "ntfy_password"}
                and isinstance(value, str)}
    except (OSError, ValueError, TypeError):
        # 凭据是可选能力；损坏时安全降级到无凭据，不能阻断桌面主程序启动。
        # 不能把凭据正文带入日志。
        return {}
