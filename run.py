# -*- coding: utf-8 -*-
"""公开版最小启动检查。

真正的聊天、记忆和桌宠模块会在后续逐步接入；这里先确保用户配置有效。
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from config_loader import CompanionConfig
from chat_runtime import CompanionRuntime


def main():
    config = CompanionConfig(ROOT)
    config.ensure_dirs()
    info = config.summary()
    print("本地部署伴生体")
    print(f"角色：{info['name']}")
    print(f"模型：{info['model']}")
    print(f"云端 API：{'已启用' if info['api_enabled'] else '未启用'}")
    if not info["ready"]:
        print("尚未完成配置，请先运行：python setup/setup_wizard.py")
        return 1
    print("配置检查通过，运行层已准备好。")
    print("聊天模块已加载；后续 UI 会调用 CompanionRuntime.chat()。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
