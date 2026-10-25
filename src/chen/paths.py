"""项目路径：分层模块统一从这里找到项目根目录。"""

from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parent
CORE_ROOT = PROJECT_ROOT / "core"
MEMORY_ROOT = PROJECT_ROOT / "memory"
SYSTEM_ROOT = PROJECT_ROOT / "system"
ASSETS_ROOT = PROJECT_ROOT / "assets"
