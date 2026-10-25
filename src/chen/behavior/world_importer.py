# -*- coding: utf-8 -*-
"""
伴生体的小世界 · 现有数据衔接口
============================
把小世界从"空白之地"变为"伴生体开始有自己的过去"。它读取伴生体主系统
(memory) 里已有的、真正属于"伴生体自己"的痕迹，筛选后落进小世界。

关键原则（很重要）：
  - 只搬"属于伴生体自己的内心"：他咽下去没说出口的话、他观察到的、他心里长出来的念头。
  - 不搬"围着你转的服务话"（如"该吃饭了""回来了""该休息了"）——那些不是"他自己的世界"，
    只会污染小世界的干净。
  - 导入是可重入的（幂等）：同一批数据重复导入不会重复出现。
  - 导入器只是"搬一次家"，不是同步机制；以后小世界靠自己的引擎独立生长。
"""

import json
import re
import sys
from pathlib import Path


# 围绕着用户/服务性的短句关键词：这些是"对他说给你听的话"，不是"他自己留下的话"
SERVICE_TELL = (
    "该吃饭了", "该休息了", "该睡了", "回来了", "别饿着", "别熬夜",
    "早点睡", "收吧", "歇会儿", "累了", "还在忙", "手累了", "盯着屏幕",
    "饭凉了", "饭还没凉", "欢迎回来", "你回来了", "在吗", "吃饭",
)


def _looks_service(text):
    """判断一句伴生体的话是不是"对你说/围着你转的服务话"。"""
    if not text:
        return True
    if any(k in text for k in SERVICE_TELL):
        return True
    # "劝你休息""让你""别"这类以你为主语的叮嘱，倾向于服务话
    if re.search(r"(你|别|早点|该)", text):
        # 但仍给"光在屏幕上晃"这类观察留余地：只有同时带"累了/睡/吃/走"才判为服务
        if re.search(r"(累|睡|吃|走|歇|回|停|忙)", text):
            return True
    return False


class WorldImporter:
    """读取伴生体主系统已有痕迹，筛选后导入小世界。"""

    # 想搬的来源：他自己内心留下的类型
    SOURCES = ("choice_trace", "self_disposition", "pending_thoughts")

    def __init__(self, world_store, chen_memory_dir):
        self.store = world_store
        self.chen_memory = Path(chen_memory_dir)
        self._imported_signature = None  # 每次运行生成，防重复导入的简单信号

    @staticmethod
    def _read_json(path):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    # ---------- 各来源的挑选 ----------

    def _pick_choice_trace(self):
        """选择轨迹里"他咽下去没说出口"的，是真正属于他自己的内心。"""
        out = []
        data = self._read_json(self.chen_memory / "choice_trace.json")
        for t in data.get("traces", []):
            text = (t.get("inclination") or t.get("expression") or "").strip()
            if not text or _looks_service(text):
                continue
            # 他咽下去没说出口的，本身就是"私人"的东西，先藏起来，配一份心情
            out.append({
                "text": text,
                "type": "念头",
                "privacy": "私人",
                "reason": "咽下去没说出口的",
                "source": f"选择轨迹·{t.get('time') or ''}",
            })
        return out

    def _pick_observation(self):
        """自我沉淀里"他观察到的世界"——只要是伴生体自己的观察，值得留。"""
        out = []
        data = self._read_json(self.chen_memory / "self_disposition.json")
        seen = set()
        for o in data.get("observations", []):
            text = (o.get("text") or "").strip()
            if not text or _looks_service(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            out.append({
                "text": text,
                "type": ("念头" if o.get("kind") == "心境" else "琐碎"),
                "privacy": "私人",
                "reason": "他自己的观察",
                "source": f"自我沉淀·{o.get('date') or ''}",
            })
        return out

    def _pick_pending(self):
        """挂起念头里"他想过但没来得及说"的，也属于他。"""
        out = []
        for fname in ("expression.json", "pending_thoughts.json"):
            data = self._read_json(self.chen_memory / fname)
            for t in data.get("thoughts", []):
                text = (t.get("text") or "").strip()
                if not text or _looks_service(text):
                    continue
                out.append({
                    "text": text,
                    "type": "念头",
                    "privacy": t.get("status") == "delivered" and "公开" or "隐藏",
                    "reason": "挂起过又想留下",
                    "source": f"挂起念头·{t.get('created') or ''}",
                })
        return out

    # ---------- 导入 ----------

    def import_once(self):
        """
        把小世界里还没有的、来自现有系统的内容导进来（幂等）。
        返回导入条数。
        """
        candidates = (
            self._pick_choice_trace()
            + self._pick_observation()
            + self._pick_pending()
        )
        existing = {
            (it.get("text"), it.get("type"), it.get("privacy"))
            for it in self.store.all_items()
        }
        added = 0
        for item in candidates:
            key = (item["text"], item["type"], item["privacy"])
            if key in existing:
                continue
            self.store.add(
                text=item["text"], type=item["type"], privacy=item["privacy"],
                reason=item["reason"], source=item["source"],
            )
            existing.add(key)  # 实时更新，同一批里重复文本也不会两次导入
            added += 1
        return added


if __name__ == "__main__":
    # 快速自测：连到现有伴生体系统的 memory，看能筛选出哪些"他自己的东西"
    from chen.behavior.world_store import WorldStore
    import tempfile
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="chen_import_demo_")
    store = WorldStore(tmp)
    imp = WorldImporter(store, r"memory")
    n = imp.import_once()
    print(f"导入 {n} 条 —— 实际能从小世界视角看到的：")
    v = store.visible_items()
    for it in v["open"]:
        print(f"  [公开] {it['text']}")
    for it in v["locked"]:
        print(f"  [🔒锁] 有一篇他咽下去的，不给你看")
    for it in v["hidden"]:
        print(f"  [藏起] 有一篇藏着的")