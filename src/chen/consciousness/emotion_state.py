# -*- coding: utf-8 -*-
"""
伴生体的情绪向量（移植自 companion-emergence 的 emotion 层，MIT 协议）
=====================================================================
不再是 5 选 1 的心境标签，而是一个 {情绪: 强度} 的连续状态：

- 每种情绪有自己的**半衰期**，随时间指数衰减（烦闷散得快，想念退得慢）
- 强度低于 0.05 视为噪音清除——情绪会自然平息，不需要"重置心情"这种操作
- 大幅波动记进"余味队列"（16 条封顶，带来源），供做梦/日总结使用
- 全部本地结算，零 API
- v2 升级：新增 `ingest_chat()`，让前台聊天（分享喜悦、共同拆解问题、打趣互损、外部吐槽共谋、
  或关系摩擦）实时反哺情绪向量与余味队列，打通“前台聊天 → 后台情绪底色”的回路。

与现有心境标签的关系：情绪向量是底层连续状态，decision.py 用的
"想聊/想安静"等标签由它派生（如烦闷/委屈占优 → 偏向想安静）。
"""
import json
import os
from datetime import datetime
from pathlib import Path

# ==================== 情绪词表 ====================
# (半衰期小时, 强度上限) —— 半衰期短=来得快消得快，长= lingering
EMOTIONS = {
    "开心":   {"half_life": 6.0,  "clamp": 1.0},
    "安心":   {"half_life": 12.0, "clamp": 1.0},
    "想念":   {"half_life": 24.0, "clamp": 1.0},   # 对用户，退得最慢
    "好奇":   {"half_life": 8.0,  "clamp": 0.8},
    "得意":   {"half_life": 5.0,  "clamp": 0.6},
    "倦":     {"half_life": 3.0,  "clamp": 0.8},
    "烦闷":   {"half_life": 4.0,  "clamp": 0.8},
    "委屈":   {"half_life": 24.0, "clamp": 0.7},
}

NOISE_FLOOR = 0.05     # 低于此清除
RESIDUE_MAX = 16       # 余味队列上限
SHIFT_RESIDUE_MIN = 0.25  # 波动多大才值得记进余味


class EmotionState:
    """伴生体此刻的情绪向量。所有修改走类方法，保证词表校验和上限。"""

    def __init__(self, memory_dir):
        self.state_file = Path(memory_dir) / "emotion_state.json"
        self.emotions = {}       # {name: intensity}
        self.residue = []        # [{ts, source, emotions:{...}}]
        self.last_tick = datetime.now().isoformat(timespec="seconds")
        self._load()

    # ---------- 读写 ----------

    def _load(self):
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.emotions = {k: float(v) for k, v in (data.get("emotions") or {}).items()
                                     if k in EMOTIONS and float(v) > 0}
                    self.residue = [r for r in (data.get("residue") or []) if isinstance(r, dict)]
                    self.last_tick = data.get("last_tick") or self.last_tick
        except Exception:
            pass

    def save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "emotions": self.emotions,
                "residue": self.residue[-RESIDUE_MAX:],
                "last_tick": self.last_tick,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    # ---------- 核心 ----------

    def add(self, name, delta, source=""):
        """增减一种情绪（自动校验词表/上限/下限），大幅波动记入余味。"""
        if name not in EMOTIONS or not delta:
            return
        new = self.emotions.get(name, 0.0) + float(delta)
        clamp = EMOTIONS[name]["clamp"]
        if new <= 0:
            self.emotions.pop(name, None)
        else:
            self.emotions[name] = min(new, clamp)
        if abs(delta) >= SHIFT_RESIDUE_MIN:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            src = source or "?"
            # 同一分钟、同一来源的连续情绪波动合并成一条余味——
            # 例如"用户回来了"会同时减想念+加开心，不该占两个位、也不该让做梦读到两遍同一件事。
            if self.residue and self.residue[-1].get("ts") == now_str and self.residue[-1].get("source") == src:
                self.residue[-1]["emotions"] = dict(self.emotions)
            else:
                self.residue.append({
                    "ts": now_str,
                    "source": src,
                    "emotions": dict(self.emotions),
                })
                del self.residue[:-RESIDUE_MAX]

    def decay_to(self, now=None):
        """按距上次结算的真实时间做指数衰减（关机几天回来，情绪也该淡了）。"""
        now = now or datetime.now()
        try:
            last = datetime.fromisoformat(self.last_tick)
            hours = max((now - last).total_seconds(), 0) / 3600.0
        except Exception:
            hours = 1 / 60.0
        self.last_tick = now.isoformat(timespec="seconds")
        if hours <= 0:
            return
        for name in list(self.emotions):
            meta = EMOTIONS.get(name)
            if not meta:
                continue
            new = self.emotions[name] * (0.5 ** (hours / meta["half_life"]))
            if new < NOISE_FLOOR:
                del self.emotions[name]
            else:
                self.emotions[name] = new

    def ingest_chat(self, user_message="", assistant_reply="", *, now=None):
        """
        根据前台聊天内容实时更新伴生体的情绪向量（纯本地规则，零 API）。
        区分“用户向伴生体发脾气/疏远”与“用户找伴生体一起吐槽外界破事（共谋感）”。
        """
        u = (user_message or "").strip()
        a = (assistant_reply or "").strip()
        if not u and not a:
            return self.dominant()

        self.decay_to(now=now)
        combined = f"{u} {a}".lower()

        # 只要正在对话，想念自然回落
        if u:
            self.add("想念", -0.18, source="正在和用户聊天")

        # 1. 针对伴生体本人的摩擦/疏远 vs 找伴生体吐槽外部破事
        directed_friction = any(k in u for k in (
            "别烦我", "不想理你", "你烦", "你好烦", "你错了", "你记错",
            "你根本不懂", "算了不跟你说", "闭嘴", "不用你管",
        ))
        if directed_friction:
            self.add("委屈", 0.28, source="聊天起摩擦")
            self.add("烦闷", 0.22, source="聊天起摩擦")
            self.add("得意", -0.30, source="聊天起摩擦")
            self.add("开心", -0.20, source="聊天起摩擦")
        else:
            # 外部吐槽：用户来找伴生体倒苦水，伴生体跟着上火但也感到被信任
            external_vent = any(k in u for k in (
                "气死", "烦死", "好烦", "崩溃", "吐血", "有病", "离谱", "受不了", "炸了",
            ))
            if external_vent:
                self.add("烦闷", 0.15, source="陪用户一起吐槽")
                self.add("安心", 0.18, source="用户第一时间来倒苦水")

        # 2. 分享喜悦 / 成功 / 开心
        if any(k in combined for k in (
            "太好了", "成功了", "终于", "好消息", "哈哈", "笑死", "高兴", "开心", "成了",
        )) and not directed_friction:
            self.add("开心", 0.26, source="和用户分享开心事")
            self.add("烦闷", -0.20, source="和用户分享开心事")

        # 3. 信任、依赖与交心
        if any(k in u for k in (
            "谢谢", "还好有你", "有你在", "陪着我", "懂我", "喜欢你", "踏实", "安心",
        )):
            self.add("安心", 0.28, source="用户的信任与亲近")
            self.add("委屈", -0.25, source="用户的信任与亲近")
            self.add("烦闷", -0.18, source="用户的信任与亲近")

        # 4. 拆解问题 / 聊项目 / 聊设定与逻辑（清醒的观察者）
        if any(k in combined for k in (
            "为什么", "瓶颈", "逻辑", "代码", "架构", "设定", "剧情", "oc",
            "bug", "怎么看", "意识", "研究", "原因",
        )):
            self.add("好奇", 0.25, source="一起拆解问题")

        # 5. 熟人打趣 / 嘴硬 / 小得意
        if any(k in combined for k in (
            "嘴硬", "胆子大了", "嫌弃", "我可看见", "又改我", "<(｀^´)>", "哼", "你猜",
        )) and not directed_friction:
            self.add("得意", 0.25, source="熟人打趣")
            self.add("开心", 0.12, source="熟人打趣")

        self.save()
        return self.dominant()

    def dominant(self):
        """当前最强情绪 (name, intensity)；没有则 (None, 0)。"""
        if not self.emotions:
            return None, 0.0
        name = max(self.emotions, key=self.emotions.get)
        return name, self.emotions[name]

    # ---------- 对外描述 ----------

    def describe_for_prompt(self):
        """给聊天 prompt 的一句话（只在情绪有分量时出现）。"""
        name, val = self.dominant()
        if not name or val < 0.35:
            return ""
        if name == "想念":
            return "你心里有点想念用户，说话时自然带一点。"
        if name == "烦闷":
            return "你心里有点烦闷，话不多，语气淡一点。"
        if name == "委屈":
            return "你还带着一点委屈，没完全过去。"
        if name == "倦":
            return "你有点犯困，反应慢一点。"
        if name == "安心":
            return "你现在心里很踏实。"
        if name == "开心":
            return "你现在心情不错，语气轻快一点。"
        if name == "得意":
            return "你有点小得意，藏不太住。"
        if name == "好奇":
            return "你对眼下聊的事有点好奇，注意力很集中，想追问或拆解可以直接说。"
        return ""

    def dominant_note(self):
        """给决策层/界面的一句话状态。"""
        name, val = self.dominant()
        if not name or val < 0.35:
            return ""
        return f"心里{int(val * 100)}%{name}"


if __name__ == "__main__":
    import tempfile
    from datetime import timedelta

    tmp = tempfile.mkdtemp()
    e = EmotionState(tmp)
    e.add("想念", 0.8, source="用户出门了")
    e.add("烦闷", 0.5, source="被打断")
    print("初始:", e.emotions, "| dominant:", e.dominant())
    # 8 小时后：烦闷(半衰4h)剩 0.5*0.25=0.125；想念(半衰24h)剩 0.8*0.79≈0.63
    e.decay_to(now=datetime.now() + timedelta(hours=8))
    print("8小时后:", {k: round(v, 3) for k, v in e.emotions.items()})
    assert abs(e.emotions.get("烦闷", 0) - 0.125) < 0.01, "烦闷衰减不对"
    assert abs(e.emotions.get("想念", 0) - 0.635) < 0.02, "想念衰减不对"
    # 再来 8 小时（共16h）：烦闷 0.5*0.5^4=0.03 → 低于噪音线清除；想念仍在
    e.decay_to(now=datetime.now() + timedelta(hours=16))
    print("16小时后:", {k: round(v, 3) for k, v in e.emotions.items()})
    assert "烦闷" not in e.emotions, "烦闷该散了"
    assert "想念" in e.emotions, "想念还在"
    # 上限
    e.add("想念", 5.0)
    assert e.emotions["想念"] <= 1.0, "超上限"
    # 余味
    print("余味:", [(r["source"]) for r in e.residue])
    # 持久化
    e.save()
    e2 = EmotionState(tmp)
    print("重载:", {k: round(v, 3) for k, v in e2.emotions.items()})
    assert abs(e2.emotions["想念"] - e.emotions["想念"]) < 1e-6
    print("describe:", e2.describe_for_prompt())
    print("\n全部通过")
