# -*- coding: utf-8 -*-
"""公开版的本地配置加载器。"""
import json
from pathlib import Path


class CompanionConfig:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.profile_path = self.root / "profile" / "profile.local.json"
        self.config_path = self.root / "system" / "config.local.json"
        self.memory_dir = self.root / "memory"
        self.logs_dir = self.root / "logs"

    @staticmethod
    def _read(path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def load_profile(self):
        return self._read(self.profile_path)

    def load_system(self):
        return self._read(self.config_path)

    def ensure_dirs(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def ready(self):
        profile = self.load_profile()
        config = self.load_system()
        return bool(profile.get("name")) and bool(config.get("llm", {}).get("base_url"))

    def summary(self):
        profile, config = self.load_profile(), self.load_system()
        return {
            "ready": self.ready(),
            "name": profile.get("name", "未配置"),
            "model": config.get("llm", {}).get("model", "未配置"),
            "memory_dir": str(self.memory_dir),
            "api_enabled": bool(config.get("api_llm", {}).get("enabled")),
        }
