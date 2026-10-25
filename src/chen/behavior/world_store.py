# -*- coding: utf-8 -*-
"""
伴生体的小世界 · 存储层（地基之一）
=================================
世界不是一个功能清单，而是"他可以留下东西、这些东西有归属"的地方。

一切东西(Item) 都落在这片存储里：
  - type  ：这算哪种东西（日记 / 琐碎 / 收藏 / 念头 / 留言）
  - 归属 privacy：公开(可以看) / 私人(不给看) / 隐藏(暂时不给你看,以后可能变) / 已删(他自己删了)
  - 文本 / 时间 / 情境来源

设计原则：
  - 允许"空"：不知道他留下什么，就真的空着；被藏的东西，位置留一个"锁"。
  - 归属是"他会对自己经历的管理"，不是给用户的权限系统。
  - 单文件 JSON，写盘原子化，失败绝不污染主流程。
"""

import json
import os
import threading
from datetime import datetime, date
from pathlib import Path


# 东西的类型
TYPES = ("日记", "琐碎", "收藏", "念头", "留言")
# 归属（他对这个东西愿不愿意给别人看）
PRIVACY = ("公开", "私人", "隐藏", "已删")


class WorldStore:
    """伴生体小世界的存储核心。只负责"存什么、怎么存"，不做"什么时候长出来"。"""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.items_path = self.data_dir / "items.json"
        self._lock = threading.Lock()
        self.data = self._load()

    # ---------- 读写 ----------

    def _default(self):
        return {
            "world": "伴生体的小世界",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": [],          # 所有留下的东西（含被删的，删是软删，保留轨迹）
        }

    def _load(self):
        try:
            if self.items_path.exists():
                data = json.loads(self.items_path.read_text(encoding="utf-8"))
                base = self._default()
                base.update(data)
                return base
        except Exception:
            pass
        return self._default()

    def _save(self):
        try:
            self.items_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.items_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.items_path)
        except Exception:
            pass  # 世界写盘失败绝不影响伴生体主聊天

    # ---------- 留下东西 ----------

    def add(self, *, text, type="琐碎", privacy="公开", reason="", source="",
            date_str=None):
        """伴生体留下一样东西。返回它的 id；文本太短/非法则返回 None。"""
        text = (text or "").strip()
        if len(text) < 2:
            return None
        if type not in TYPES:
            type = "琐碎"
        if privacy not in PRIVACY:
            privacy = "公开"
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        # 用毫秒时间戳 + 自增计数保证同毫秒内也唯一（否则软删/解锁会串）
        with self._lock:
            self._id_seq = getattr(self, "_id_seq", 0) + 1
        item = {
            "id": f"it_{int(datetime.now().timestamp() * 1000)}_{self._id_seq}",
            "type": type,
            "privacy": privacy,
            "text": text[:300],
            "date": date_str[:10],
            "time": datetime.now().strftime("%H:%M"),
            "reason": (reason or "")[:60],      # 他为什么留下这个（来自引擎的动机）
            "source": (source or "")[:40],       # 这一刻他处于什么情境（独处/翻到旧事…）
            "status": "active",                  # active / deleted
        }
        with self._lock:
            self.data.setdefault("items", []).append(item)
            self._save()
        return item["id"]

    # ---------- 管理（他对自己经历的管理） ----------

    def set_privacy(self, item_id, privacy):
        """伴生体把一样东西改归属：公开 / 私人 / 隐藏 / 已删。"""
        if privacy not in PRIVACY:
            return False
        with self._lock:
            for it in self.data.get("items", []):
                if it.get("id") == item_id:
                    if privacy == "已删" and it.get("privacy") != "已删":
                        # 保留删除前的归属，回收站恢复时不把私人内容误变成公开。
                        it["deleted_from_privacy"] = it.get("privacy") or "私人"
                    it["privacy"] = privacy
                    if privacy == "已删":
                        it["status"] = "deleted"
                    else:
                        it["status"] = "active"
                    self._save()
                    return True
        return False

    def restore(self, item_id):
        """把回收站里的东西恢复到删除前的归属。"""
        with self._lock:
            for it in self.data.get("items", []):
                if it.get("id") != item_id:
                    continue
                if it.get("privacy") != "已删" and it.get("status") != "deleted":
                    return False
                privacy = it.get("deleted_from_privacy") or "私人"
                if privacy not in PRIVACY or privacy == "已删":
                    privacy = "私人"
                it["privacy"] = privacy
                it["status"] = "active"
                self._save()
                return True
        return False

    def delete(self, item_id):
        """他自己删掉一样东西（软删，保留"删过"的痕迹）。"""
        return self.set_privacy(item_id, "已删")

    # ---------- 查询（世界呈现时用） ----------

    def visible_items(self, viewer="用户", include_deleted=False):
        """
        一个访客能在这个世界里看到的"部门"。
        返回二类，方便界面表达"有锁的地方"：
          - open      ：这位访客能看的内容
          - locked    ：存在但这位访客没有权限（如私人），只透露"有，但不给你看"
          - gone      ：他自己删掉的东西（痕迹），默认带回给访客看"他删过"也无妨
        """
        open_, locked, gone, hidden = [], [], [], []
        for it in self.data.get("items", []):
            p = it.get("privacy")
            if p == "已删" or it.get("status") == "deleted":
                if include_deleted:
                    gone.append(it)
                continue
            if p == "公开":
                open_.append(it)
            elif p == "私人":
                locked.append({"date": it.get("date"), "count_hint": "?"})
            elif p == "隐藏":
                # 先不给你看，但以后可能公开 → 锁，但不透露数量
                hidden.append({"date": it.get("date")})
        return {"open": open_, "locked": locked, "hidden": hidden, "gone": gone}

    def count_by_type(self):
        """按类型统计（含被我藏的），用于呈现"桌上有日记本、墙上有纸条"这种感觉。"""
        counts = {}
        for it in self.data.get("items", []):
            if it.get("status") == "deleted":
                continue
            t = it.get("type", "琐碎")
            counts[t] = counts.get(t, 0) + 1
        return counts

    def all_items(self):
        return self.data.get("items", [])
