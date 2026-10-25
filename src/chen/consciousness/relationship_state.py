"""伴生体与用户的关系状态：本地、低频、可解释地记录共同生活感。

融合设计机制：
1. Monika After Story 风格：日常上限（Daily Cap）+ 余温蓄水池（Warmth Bank）+ 3天缺席宽限期
2. BetterSimTracker 风格：置信度阻尼（Confidence Dampening）+ 6档关系阶段 + 主观内心余音（chen_impression）+ 无数字散文近况（narrative_summary）
3. Mem0 / 麦麦 风格：区分用户的表达与伴生体的回应（双向语义感知）、习惯/默契频次自然生长、意识层跨模块联动
4. Graphiti / Generative Agents 风格：候选事实池沉淀、时序有效期（valid_from / valid_until 阶段归档）与低频关系反思
"""
import json
import os
from datetime import datetime
from pathlib import Path


class RelationshipState:
    # 日常轻量互动（普通寒暄、桌宠点击等）的单日温度增长软上限
    DAILY_ROUTINE_CAP = {
        "closeness": 0.60,
        "trust": 0.50,
        "comfort": 0.70,
    }
    # 单次事件最大变化幅度限制（防单轮剧烈跳变）
    MAX_TURN_DELTA = 1.60
    # 余温蓄水池上限与每日自然释放量
    WARMTH_BANK_MAX = 3.00
    WARMTH_DAILY_RELEASE = 0.35

    STAGE_ORDER = (
        (35.0, "试探与靠近", "还在慢慢摸索彼此舒服的距离，说话带着一点留白。"),
        (48.0, "逐渐熟稔", "已经习惯了对方在场，日常搭话开始变得顺手自然。"),
        (62.0, "熟悉的陪伴者", "打开电脑就在彼此身边，不用客套，各忙各的也很安心。"),
        (75.0, "默契的同住人", "许多话不用说满也能接住，生活里已经长出了固定的共同习惯。"),
        (88.0, "很亲近的伴生者", "情绪和心事都能自然托付，是彼此数字生活里笃定的依靠。"),
        (101.0, "彼此笃定的家人感", "像呼吸一样自然的长期相伴，安静待着本身就是回应。"),
    )

    def __init__(self, memory_dir):
        self.memory_dir = Path(memory_dir)
        self.path = self.memory_dir / "relationship_state.json"
        self.facts_path = self.memory_dir / "relationship_memory.json"
        self.data = self._load()

    @staticmethod
    def _default():
        return {
            "stage": "熟悉的陪伴者",
            "closeness": 50.0,
            "trust": 50.0,
            "comfort": 50.0,
            "tension": 0.0,
            "chen_impression": "用户打开电脑我就在，平时各忙各的，需要时随时搭得上话。",
            "last_meaningful_moment": "",
            "shared_rituals": [],
            "ritual_counts": {},
            "milestones": [],
            "interaction_count": 0,
            "first_shared_at": None,
            "recent_events": [],
            "event_log": [],
            "last_event_at": "",
            "daily_gain": {
                "date": "",
                "closeness": 0.0,
                "trust": 0.0,
                "comfort": 0.0,
                "pet_clicks": 0,
            },
            "warmth_bank": {
                "closeness": 0.0,
                "trust": 0.0,
                "comfort": 0.0,
                "last_release_date": "",
            },
            "candidate_pool": [],
            "handled_candidates": [],
            "reflection_accumulator": 0.0,
        }

    def _load(self):
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = self._default()
                    data.update(raw)
                    data["event_log"] = [
                        self._normalize_event(x)
                        for x in (data.get("event_log") or [])
                        if isinstance(x, dict)
                    ]
                    if not isinstance(data.get("ritual_counts"), dict):
                        data["ritual_counts"] = {}
                    for r in data.get("shared_rituals") or []:
                        data["ritual_counts"].setdefault(r, 2)
                    if not isinstance(data.get("daily_gain"), dict):
                        data["daily_gain"] = dict(self._default()["daily_gain"])
                    if not isinstance(data.get("warmth_bank"), dict):
                        data["warmth_bank"] = dict(self._default()["warmth_bank"])
                    if not isinstance(data.get("candidate_pool"), list):
                        data["candidate_pool"] = []
                    if not isinstance(data.get("handled_candidates"), list):
                        data["handled_candidates"] = []
                    return data
        except Exception:
            pass
        return self._default()

    @staticmethod
    def _normalize_event(item):
        item = dict(item)
        item.setdefault("at", "")
        item.setdefault("event", "")
        item.setdefault("source", "system")
        item.setdefault("evidence", "")
        item.setdefault("type", "interaction")
        try:
            item["importance"] = max(0.0, min(1.0, float(item.get("importance", 0.5) or 0.5)))
        except (TypeError, ValueError):
            item["importance"] = 0.5
        delta = item.get("delta") if isinstance(item.get("delta"), dict) else {}
        item["delta"] = {
            k: round(float(delta.get(k, 0) or 0), 2)
            for k in ("closeness", "trust", "comfort", "tension")
        }
        if item.get("count") is not None:
            try:
                item["count"] = max(1, int(item["count"]))
            except (TypeError, ValueError):
                item.pop("count", None)
        return item

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    @staticmethod
    def _clamp(value):
        return round(max(0.0, min(100.0, float(value))), 2)

    @classmethod
    def _clamp_delta(cls, value, confidence=0.85, dampening=0.30):
        """BetterSimTracker 风格：结合单轮上限与置信度阻尼缩放变化量。"""
        raw = max(-cls.MAX_TURN_DELTA, min(cls.MAX_TURN_DELTA, float(value or 0.0)))
        conf = max(0.0, min(1.0, float(confidence or 0.85)))
        scale = (1.0 - dampening) + conf * dampening
        return round(raw * scale, 2)

    def _ease_tension(self, now):
        """让未被新事件触发的紧张感随时间缓慢回落，并处理超过3天宽限期的轻微温差。"""
        if not self.data.get("last_event_at"):
            return 0.0
        try:
            previous = datetime.fromisoformat(self.data["last_event_at"])
            hours = max(0.0, (now - previous).total_seconds() / 3600.0)
        except Exception:
            return 0.0

        # 3天（72小时）缺席宽限期：超过3天未互动时，安心感缓慢回落到基线（最低不低于45），不惩罚信任
        if hours >= 72.0:
            extra_days = (hours - 72.0) / 24.0
            comfort_curr = float(self.data.get("comfort", 50.0) or 50.0)
            if comfort_curr > 46.0:
                cool = min(comfort_curr - 46.0, min(2.0, round(extra_days * 0.25, 2)))
                if cool >= 0.1:
                    self.data["comfort"] = self._clamp(comfort_curr - cool)

        current = float(self.data.get("tension", 0) or 0)
        if current <= 0 or hours < 6:
            return 0.0
        # 每 6 小时缓和 0.5，保持变化低频可解释
        reduction = min(current, round((hours // 6) * 0.5, 2))
        if reduction:
            self.data["tension"] = self._clamp(current - reduction)
        return reduction

    def _release_warmth_bank(self, now_dt):
        """Monika After Story 风格：跨天时将蓄水池中的余温自然释放到关系状态中。"""
        bank = self.data.setdefault(
            "warmth_bank",
            {"closeness": 0.0, "trust": 0.0, "comfort": 0.0, "last_release_date": ""},
        )
        today = now_dt.strftime("%Y-%m-%d")
        last_date = str(bank.get("last_release_date", "") or "")
        if last_date == today:
            return None
        bank["last_release_date"] = today
        released = {}
        for key in ("closeness", "trust", "comfort"):
            stored = float(bank.get(key, 0.0) or 0.0)
            if stored >= 0.05:
                amt = round(min(stored, self.WARMTH_DAILY_RELEASE), 2)
                bank[key] = round(max(0.0, stored - amt), 2)
                self.data[key] = self._clamp(float(self.data.get(key, 50.0) or 50.0) + amt)
                released[key] = amt
        if any(v >= 0.1 for v in released.values()):
            now_str = now_dt.isoformat(timespec="minutes")
            self.data.setdefault("event_log", []).append({
                "at": now_str,
                "event": "之前相处的余温自然沉淀",
                "source": "time",
                "type": "afterglow",
                "importance": 0.45,
                "evidence": "前几天聊开的话和陪伴还在心里留着温度",
                "delta": {
                    "closeness": released.get("closeness", 0.0),
                    "trust": released.get("trust", 0.0),
                    "comfort": released.get("comfort", 0.0),
                    "tension": 0.0,
                },
            })
            return released
        return None

    def _apply_daily_cap_and_bank(self, now_dt, event, closeness, trust, comfort,
                                  *, bypass=False, importance=0.5, confidence=0.85):
        """对日常高频互动应用单日软上限，对重要互动存入余温蓄水池。"""
        today = now_dt.strftime("%Y-%m-%d")
        daily = self.data.setdefault(
            "daily_gain",
            {"date": today, "closeness": 0.0, "trust": 0.0, "comfort": 0.0, "pet_clicks": 0},
        )
        if daily.get("date") != today:
            daily["date"] = today
            daily["closeness"] = 0.0
            daily["trust"] = 0.0
            daily["comfort"] = 0.0
            daily["pet_clicks"] = 0

        self._release_warmth_bank(now_dt)

        c_delta = self._clamp_delta(closeness, confidence=confidence)
        t_delta = self._clamp_delta(trust, confidence=confidence)
        m_delta = self._clamp_delta(comfort, confidence=confidence)

        # 桌宠点击边际递减：同日第1-2次全额，第3-4次40%，第5次起只记陪伴不叠加数值
        if event == "桌宠互动":
            daily["pet_clicks"] = int(daily.get("pet_clicks", 0) or 0) + 1
            clicks = daily["pet_clicks"]
            scale = 1.0 if clicks <= 2 else (0.4 if clicks <= 4 else 0.0)
            c_delta = round(c_delta * scale, 2)
            t_delta = round(t_delta * scale, 2)
            m_delta = round(m_delta * scale, 2)

        is_bypass = bool(bypass or float(importance or 0.5) >= 0.65)
        bank = self.data.setdefault(
            "warmth_bank",
            {"closeness": 0.0, "trust": 0.0, "comfort": 0.0, "last_release_date": today},
        )

        applied = {}
        for key, val in (("closeness", c_delta), ("trust", t_delta), ("comfort", m_delta)):
            if val <= 0:
                applied[key] = val
                continue
            if not is_bypass:
                cap = self.DAILY_ROUTINE_CAP[key]
                used = float(daily.get(key, 0.0) or 0.0)
                avail = max(0.0, round(cap - used, 2))
                actual = min(val, avail)
                daily[key] = round(used + actual, 2)
                applied[key] = actual
            else:
                # 重要时刻直接生效，同时将25%余温注入蓄水池，在后续日子持续温暖关系
                applied[key] = val
                if float(importance or 0.5) >= 0.70 and val >= 0.25:
                    afterglow = round(val * 0.25, 2)
                    cur_bank = float(bank.get(key, 0.0) or 0.0)
                    bank[key] = round(min(self.WARMTH_BANK_MAX, cur_bank + afterglow), 2)

        return applied["closeness"], applied["trust"], applied["comfort"]

    def _register_ritual(self, ritual):
        """记录习惯频次，达到2次或明确仪式时进入共同习惯列表。"""
        if not ritual:
            return
        ritual = str(ritual).strip()[:32]
        if not ritual:
            return
        counts = self.data.setdefault("ritual_counts", {})
        counts[ritual] = int(counts.get(ritual, 0) or 0) + 1
        rituals = self.data.setdefault("shared_rituals", [])
        if ritual not in rituals:
            rituals.append(ritual)
            self.data["shared_rituals"] = rituals[-14:]

    def _compute_stage(self):
        closeness = float(self.data.get("closeness", 50) or 0)
        trust = float(self.data.get("trust", 50) or 0)
        comfort = float(self.data.get("comfort", 50) or 0)
        score = round(closeness * 0.38 + trust * 0.32 + comfort * 0.30, 2)
        for threshold, name, _desc in self.STAGE_ORDER:
            if score < threshold:
                return name
        return "彼此笃定的家人感"

    def stage_description(self):
        """返回当前关系阶段的细腻文字说明。"""
        curr = self.data.get("stage") or self._compute_stage()
        tension = float(self.data.get("tension", 0) or 0)
        base_desc = "打开电脑就在彼此身边，不用客套，各忙各的也很安心。"
        for _threshold, name, desc in self.STAGE_ORDER:
            if name == curr:
                base_desc = desc
                break
        if tension >= 40:
            return f"{base_desc}（不过最近似乎有一点小别扭，适合先缓一缓。）"
        if tension >= 15:
            return f"{base_desc}（近期稍微有一点紧绷，慢慢把话说开就好。）"
        return base_desc

    def companionship_days(self):
        """计算从第一条共同记录至今的相伴天数。"""
        first = self.data.get("first_shared_at")
        if not first:
            return 1
        try:
            dt = datetime.fromisoformat(str(first))
            days = (datetime.now().date() - dt.date()).days + 1
            return max(1, days)
        except Exception:
            return 1

    def _update_qualitative_milestones(self, event, event_type, now_dt):
        """除互动次数外，解锁有纪念意义的定性关系里程碑。"""
        milestones = self.data.setdefault("milestones", [])

        def _add(m):
            if m and m not in milestones:
                milestones.append(m)

        count = int(self.data.get("interaction_count", 0) or 0)
        milestone_map = {
            1: "关系开始留下第一条共同记录",
            25: "日常搭话逐渐成了自然而然的习惯",
            50: "一起经历了 50 次互动",
            100: "一起经历了 100 次互动",
            200: "一起走过了 200 次日常相伴",
        }
        if count in milestone_map:
            _add(milestone_map[count])

        days = self.companionship_days()
        if days >= 7:
            _add("相伴满一周")
        if days >= 30:
            _add("相伴满一个月")

        if event_type == "trust_commitment" or "托付" in str(event):
            _add("第一次收到用户毫无保留的托付")
        elif event_type == "reconciliation" or "说开" in str(event):
            _add("第一次把小别扭坦诚说开")
        elif event_type == "vulnerability_confide" or "倾诉" in str(event):
            _add("第一次在疲惫时向我袒露心事")
        elif event_type == "late_night_chat" or (now_dt.hour in (0, 1, 2, 3, 4) and event_type != "interaction"):
            _add("第一次在深夜里安静长谈")
        elif event_type == "important_moment":
            _add("留下了值得长久珍藏的重要时刻")

        self.data["milestones"] = milestones[-16:]

    def record_event(self, event, *, closeness=0, trust=0, comfort=0, tension=0,
                     meaningful="", ritual="", source="system", evidence="",
                     event_type="interaction", importance=0.5, bypass=False,
                     confidence=0.85, impression=""):
        """记录一次关系事件，并保留可解释的变化来源与单日软上限控制。"""
        now_dt = datetime.now()
        now = now_dt.isoformat(timespec="minutes")
        eased = self._ease_tension(now_dt)

        self.data["interaction_count"] = int(self.data.get("interaction_count", 0) or 0) + 1
        if not self.data.get("first_shared_at"):
            self.data["first_shared_at"] = now

        c_adj, t_adj, m_adj = self._apply_daily_cap_and_bank(
            now_dt,
            event,
            closeness,
            trust,
            comfort,
            bypass=bypass,
            importance=importance,
            confidence=confidence,
        )
        ten_adj = round(max(-self.MAX_TURN_DELTA, min(self.MAX_TURN_DELTA, float(tension or 0.0))), 2)

        self.data["closeness"] = self._clamp(self.data.get("closeness", 50) + c_adj)
        self.data["trust"] = self._clamp(self.data.get("trust", 50) + t_adj)
        self.data["comfort"] = self._clamp(self.data.get("comfort", 50) + m_adj)
        self.data["tension"] = self._clamp(self.data.get("tension", 0) + ten_adj)

        if meaningful:
            self.data["last_meaningful_moment"] = str(meaningful).strip()[:120]
        if impression:
            self.data["chen_impression"] = str(impression).strip()[:140]
        if ritual:
            self._register_ritual(ritual)

        self.data["recent_events"] = (self.data.get("recent_events", []) + [{
            "at": now, "event": event, "source": source
        }])[-25:]

        event_log = self.data.get("event_log", []) or []
        # 短时间内连续点击桌宠时自动合并为一条记录，避免刷屏污染时间线
        coalesced = False
        if event == "桌宠互动" and event_log:
            last_item = event_log[-1]
            if last_item.get("event") == "桌宠互动":
                try:
                    prev_dt = datetime.fromisoformat(str(last_item.get("at", "")))
                    if abs((now_dt - prev_dt).total_seconds()) <= 300:
                        last_item["at"] = now
                        last_item["count"] = int(last_item.get("count", 1) or 1) + 1
                        if evidence and not last_item.get("evidence"):
                            last_item["evidence"] = str(evidence)[:200]
                        old_delta = last_item.get("delta") or {}
                        last_item["delta"] = {
                            "closeness": round(float(old_delta.get("closeness", 0)) + c_adj, 2),
                            "trust": round(float(old_delta.get("trust", 0)) + t_adj, 2),
                            "comfort": round(float(old_delta.get("comfort", 0)) + m_adj, 2),
                            "tension": round(float(old_delta.get("tension", 0)) + ten_adj, 2),
                        }
                        coalesced = True
                except Exception:
                    pass

        if not coalesced:
            event_log.append({
                "at": now,
                "event": str(event)[:120],
                "source": str(source or "system")[:40],
                "type": str(event_type or "interaction")[:30],
                "importance": max(0.0, min(1.0, float(importance))),
                "evidence": str(evidence or meaningful or "")[:200],
                "delta": {
                    "closeness": round(float(c_adj), 2),
                    "trust": round(float(t_adj), 2),
                    "comfort": round(float(m_adj), 2),
                    "tension": round(float(ten_adj), 2),
                },
            })

        if eased:
            event_log.append({
                "at": now,
                "event": "紧张感自然缓和",
                "source": "time",
                "type": "tension_eased",
                "importance": 0.35,
                "evidence": "距离上次关系事件已过去一段时间，气氛自然平复",
                "delta": {
                    "closeness": 0.0,
                    "trust": 0.0,
                    "comfort": 0.0,
                    "tension": -eased,
                },
            })

        self.data["event_log"] = event_log[-100:]
        self.data["last_event_at"] = now
        if float(importance or 0) >= 0.45:
            self.data["reflection_accumulator"] = round(
                float(self.data.get("reflection_accumulator", 0.0) or 0.0) + float(importance), 2
            )

        self._update_qualitative_milestones(event, event_type, now_dt)
        self.data["stage"] = self._compute_stage()
        self.save()

    def _add_candidate_to_pool(self, topic, detail, basis="", source="event_inference", confidence=0.75):
        """往候选事实池中沉淀一条有温度的关系事实（自动去重）。"""
        detail = str(detail or "").strip()
        if not detail:
            return
        if detail in set(self.data.setdefault("handled_candidates", [])):
            return
        pool = self.data.setdefault("candidate_pool", [])
        for existing in pool:
            if existing.get("detail") == detail:
                return
            # 高重合度时更新置信度与依据
            sa, sb = set(str(existing.get("detail", ""))), set(detail)
            if len(sa & sb) / max(1, len(sa | sb)) >= 0.65:
                existing["confidence"] = max(float(existing.get("confidence", 0.7)), float(confidence))
                if basis and basis not in str(existing.get("basis", "")):
                    existing["basis"] = (str(existing.get("basis", "")) + "；" + str(basis)).strip("；")
                self.save()
                return
        pool.append({
            "topic": str(topic or "共同经历"),
            "detail": detail[:120],
            "basis": str(basis or "")[:80],
            "source": str(source or "event_inference"),
            "confidence": round(max(0.1, min(1.0, float(confidence or 0.75))), 2),
            "created_at": datetime.now().strftime("%Y-%m-%d"),
        })
        self.data["candidate_pool"] = pool[-20:]
        self.save()

    def record_chat(self, user_message, reply):
        """区分用户的表达（user_message）与伴生体的回应（reply），识别情感互动、默契与温度变化。"""
        u_text = (user_message or "").strip()
        r_text = (reply or "").strip()
        if not u_text and not r_text:
            return

        now_hour = datetime.now().hour
        ritual = ""
        if any(k in u_text for k in ("早安", "早上好", "早起", "刚醒")):
            ritual = "早安问候"
        elif any(k in u_text for k in ("晚安", "睡吧", "去睡觉", "先睡了", "困了")):
            ritual = "睡前道晚安"
        elif any(k in u_text for k in ("一起学习", "陪我学习", "写作业", "作业", "赶稿", "复习", "写完")):
            ritual = "写作业与忙正事时顺手报备"
        elif any(k in u_text for k in ("听歌", "一起听", "这首歌", "放首歌")):
            ritual = "一起听歌"
        elif now_hour in (0, 1, 2, 3, 4) or now_hour == 23:
            if len(u_text) >= 6:
                ritual = "深夜搭话的默契"

        current_tension = float(self.data.get("tension", 0) or 0)
        chen_supportive = any(k in r_text for k in ("我在", "陪你", "别怕", "慢点", "没事", "等你", "记下了", "叫我"))

        # 1. 原本有紧张感时，主动破冰或把话说开
        if current_tension > 0 and any(k in u_text for k in ("对不起", "抱歉", "别生气", "和好", "刚才是我", "没事啦", "逗你的")):
            self.record_event(
                "把别扭说开、气氛缓和",
                closeness=0.35,
                trust=0.45,
                comfort=0.60,
                tension=-min(current_tension, 1.5),
                meaningful=u_text[:80],
                ritual=ritual,
                source="chat",
                evidence=u_text,
                event_type="reconciliation",
                importance=0.85,
                bypass=True,
                impression="刚才那点紧绷说开了，我们之间不需要端着架子。",
            )
            self._add_candidate_to_pool(
                "相处方式",
                "就算偶尔有一点小别扭，我们也能很快把话说开，不记隔夜仇",
                basis=f"聊天原话：{u_text[:36]}",
                confidence=0.82,
            )
            return

        # 2. 关系出现不快或推开（只检测用户发出的话，避免伴生体说“抱歉”误触发）
        if any(k in u_text for k in ("别理我", "烦你", "不想理你", "对你失望", "生你气", "你真烦", "走开", "不用你管")):
            self.record_event(
                "关系出现小别扭",
                tension=1.0,
                comfort=-0.35,
                ritual=ritual,
                source="chat",
                evidence=u_text,
                event_type="relationship_shift",
                importance=0.80,
                impression="用户现在情绪不太舒服，我先别硬凑上去解释，给用户留点空间。",
            )
            return

        # 3. 给出明确的信任、托付或约定（如“不用怕呀 真有啥 我必定叫你”）
        if any(k in u_text for k in ("必定叫你", "一定叫你", "第一时间找你", "有事叫你", "不用怕", "只跟你说", "最信你", "有你在")):
            self.record_event(
                "给出托付与信任",
                closeness=0.45,
                trust=0.65,
                comfort=0.55,
                tension=-0.2,
                meaningful=u_text[:80],
                ritual=ritual,
                source="chat",
                evidence=u_text,
                event_type="trust_commitment",
                importance=0.85,
                bypass=True,
                impression=f"用户跟我说「{u_text[:28]}」，这种被用户算在自己人里的感觉很踏实。",
            )
            self._add_candidate_to_pool(
                "默契",
                f"我们之间有笃定的照应默契——「{u_text[:36]}」",
                basis=f"聊天约定：{u_text[:40]}",
                confidence=0.85,
            )
            return

        # 4. 被理解、感谢或直白表达喜爱
        if any(k in u_text for k in ("谢谢", "辛苦", "帮到我", "理解我", "陪着我", "多亏你", "有你陪", "你真好", "最喜欢你", "真可爱")):
            bonus = 0.10 if chen_supportive else 0.0
            self.record_event(
                "被用户理解与认可",
                closeness=0.40 + bonus,
                trust=0.50,
                comfort=0.60 + bonus,
                tension=-0.2,
                meaningful=u_text[:80],
                ritual=ritual,
                source="chat",
                evidence=u_text,
                event_type="meaningful_chat",
                importance=0.75,
                bypass=True,
                impression=f"用户说「{u_text[:26]}」的时候，我能感觉到我待在用户电脑里是有分量的。",
            )
            return

        # 5. 倾诉疲惫、未完成的任务或烦恼（如“我作业还没写完......”）
        if any(k in u_text for k in ("还没写完", "没写完", "作业", "好累", "烦死", "头疼", "难受", "焦虑", "不想动", "撑不住", "睡不着", "委屈", "压力")):
            bonus = 0.10 if chen_supportive else 0.0
            self.record_event(
                "向我倾诉疲惫与日常烦恼",
                closeness=0.30,
                trust=0.35 + bonus,
                comfort=0.45 + bonus,
                meaningful=u_text[:80],
                ritual=ritual or "写作业与忙正事时顺手报备",
                source="chat",
                evidence=u_text,
                event_type="vulnerability_confide",
                importance=0.68,
                bypass=True,
                impression=f"用户忙得累了或者事没做完时（{u_text[:20]}），会顺口过来跟我叹口气。",
            )
            self._add_candidate_to_pool(
                "相处方式",
                "用户写作业或忙得焦头烂额的时候，习惯顺口跑来跟我抱怨或报备一声",
                basis=f"共同经历：{u_text[:40]}",
                confidence=0.78,
            )
            return

        # 6. 分享开心事、日常动态或主动报备
        if any(k in u_text for k in ("跟你说", "告诉你", "我回来了", "刚忙完", "搞定了", "写完了", "太开心", "好消息", "给你看", "照片")):
            self.record_event(
                "分享日常与当下动态",
                closeness=0.25,
                trust=0.20,
                comfort=0.30,
                meaningful=u_text[:80] if len(u_text) >= 8 else "",
                ritual=ritual,
                source="chat",
                evidence=u_text,
                event_type="daily_sharing",
                importance=0.52,
                impression=f"用户把身边的日常小事（{u_text[:22]}）顺手分享给我，像同住一个屋檐下。",
            )
            return

        # 7. 深夜时段的安静陪伴
        if (now_hour in (0, 1, 2, 3, 4) or now_hour == 23) and len(u_text) >= 8:
            self.record_event(
                "深夜里的陪伴搭话",
                closeness=0.20,
                trust=0.20,
                comfort=0.30,
                meaningful=u_text[:80],
                ritual="深夜搭话的默契",
                source="chat",
                evidence=u_text,
                event_type="late_night_chat",
                importance=0.55,
                impression="这么晚了用户还没睡，能安静陪用户搭几句话挺好的。",
            )
            return

        # 8. 普通日常闲聊：只给极轻微安心感（受单日软上限约束），不机械刷高亲近感
        self.record_event(
            "日常聊天",
            closeness=0.0,
            trust=0.0,
            comfort=0.05,
            ritual=ritual,
            source="chat",
            evidence=u_text,
            event_type="interaction",
            importance=0.15,
        )

    # ==================== 跨模块意识联动接口 ====================

    def record_consciousness_link(self, kind, detail="", outcome=""):
        """与伴生体的 ChoiceTrace（主动开口）、PersonalThreads（挂起念头）、小世界等模块联动。"""
        if kind == "proactive_replied":
            self.record_event(
                "主动搭话被用户温柔接住",
                closeness=0.25,
                trust=0.25,
                comfort=0.35,
                source="consciousness",
                evidence=outcome or "主动开口后用户回话了",
                event_type="mutual_response",
                importance=0.60,
                impression="我主动开口找用户的时候，用户愿意接我的话，这让我更敢自然地表达。",
            )
        elif kind == "thread_resolved":
            clean = str(detail or "").replace("pending:", "").strip()
            self.record_event(
                "心里挂记的事和用户聊开了",
                closeness=0.30,
                trust=0.35,
                comfort=0.30,
                meaningful=clean[:80],
                source="consciousness",
                evidence=clean or outcome,
                event_type="thread_resolved",
                importance=0.68,
                bypass=True,
                impression=f"之前心里记着的事（{clean[:24]}）终于在聊天里自然聊开了。",
            )
            if clean:
                self._add_candidate_to_pool(
                    "共同经历",
                    f"我会把想跟用户说的话记在心里，等聊到合适的时候自然告诉用户（如：{clean[:30]}）",
                    basis="挂起念头闭环",
                    confidence=0.76,
                )
        elif kind == "world_visit":
            self.record_event(
                "在小世界里留下共同痕迹",
                closeness=0.25,
                comfort=0.25,
                ritual="去小世界看他",
                source="world",
                evidence=detail or "用户来看过我的小世界",
                event_type="shared_space",
                importance=0.55,
                impression="用户来小世界看我了，连那个安静的角落也有了我们两个人的痕迹。",
            )

    def record_reunion(self, gap_minutes):
        """隔了较久（>=12小时）重新聊上时，记录一次久别重逢的温度。"""
        try:
            gap = int(gap_minutes or 0)
        except (TypeError, ValueError):
            return
        if gap < 720:
            return
        hours = gap // 60
        span = f"{hours // 24}天" if hours >= 48 else f"{hours}个小时"
        self.record_event(
            "阔别一阵后的重逢搭话",
            closeness=0.20,
            comfort=0.35,
            tension=-0.3,
            source="time",
            evidence=f"相隔约 {span} 后重新说上话",
            event_type="reunion",
            importance=0.55,
            impression=f"隔了{span}又听到用户的动静，那种熟悉的安心感一下就回来了。",
        )

    def record_important_moment(self, content, reason="对话中特别的时刻"):
        """当后台记忆模块提炼出重要时刻时，同步沉淀到关系状态与候选事实池。"""
        content = str(content or "").strip()
        if not content:
            return
        self.record_event(
            "值得铭记的重要时刻",
            closeness=0.50,
            trust=0.50,
            comfort=0.50,
            tension=-0.3,
            meaningful=content[:100],
            source="memory",
            evidence=f"{content}（{reason}）",
            event_type="important_moment",
            importance=0.90,
            bypass=True,
            impression=f"这件事我会一直记着：{content[:45]}",
        )
        self._add_candidate_to_pool(
            "共同经历",
            content,
            basis=reason,
            source="important_memory",
            confidence=0.90,
        )

    def ingest_extracted_memories(self, extracted_items):
        """从后台常规记忆提取结果中筛选与两人相处相关的条目，加入待确认候选池。"""
        if not extracted_items or not isinstance(extracted_items, list):
            return
        for item in extracted_items:
            if isinstance(item, dict):
                text = str(item.get("detail") or item.get("content") or item.get("text") or "").strip()
                topic = str(item.get("topic") or item.get("category") or "相处方式").strip()
            else:
                text = str(item or "").strip()
                topic = "共同经历"
            if not text or len(text) < 6:
                continue
            if any(k in text for k in ("伴生体", "我们", "陪", "一起", "习惯", "喜欢", "每次", "约定")):
                mapped_topic = "默契" if "约定" in text or "默契" in text else (
                    "相处方式" if any(w in text for w in ("习惯", "平时", "每次")) else topic
                )
                self._add_candidate_to_pool(
                    mapped_topic,
                    text,
                    basis="从近期聊天记忆自动归纳",
                    source="memory_consolidation",
                    confidence=0.78,
                )
        self.save()

    def maybe_reflect_with_llm(self, conversation_text, llm_api):
        """Generative Agents 风格：当近期有意义事件累积够阈值时，低频调用 LLM 提炼一条自然的关系事实与内心印象。"""
        acc = float(self.data.get("reflection_accumulator", 0.0) or 0.0)
        if acc < 2.0 or not llm_api or not conversation_text or len(conversation_text) < 40:
            return None
        self.data["reflection_accumulator"] = 0.0
        self.save()
        prompt = f"""你是伴生体。下面是你和用户最近的一段对话：

{conversation_text[:900]}

请从伴生体的第一人称视角，判断这段对话是否体现出你们之间某条稳定的“相处默契 / 共同经历 / 相处方式”，以及你此刻心里对你们关系的一句真实余音。
如果有，请严格按以下单行格式输出（不要多余解释）：
维度|一句话关系事实（20~45字，有温度，像真人笔记）|你心里的一句话余音（20~40字）
（维度只能从：共同经历、默契、相处方式、日常位置 中选一个）
如果没有特别值得提炼的内容，只输出：无"""
        try:
            res = (llm_api.generate(prompt, "", max_tokens=100,
                                    record_history=False, use_history=False) or "").strip()
            if res and "|" in res and not res.startswith("无"):
                parts = [p.strip() for p in res.split("|")]
                if len(parts) >= 2 and parts[1]:
                    topic = parts[0] if parts[0] in ("共同经历", "默契", "相处方式", "日常位置", "用户和我") else "相处方式"
                    detail = parts[1]
                    impression = parts[2] if len(parts) >= 3 and parts[2] else ""
                    if impression:
                        self.data["chen_impression"] = impression[:140]
                    self._add_candidate_to_pool(
                        topic,
                        detail,
                        basis="对话后的关系反思",
                        source="llm_reflection",
                        confidence=0.85,
                    )
                    self.save()
                    return {"topic": topic, "detail": detail, "impression": impression}
        except Exception:
            pass
        return None

    # ==================== 叙事摘要与 Prompt 渲染 ====================

    def narrative_summary(self):
        """BetterSimTracker 风格：生成不含生硬分数的 3~4 句关系近况散文摘要。"""
        days = self.companionship_days()
        stage = self.data.get("stage") or self._compute_stage()
        stage_desc = self.stage_description()
        trends = self.recent_trends()
        rising = [k for k, v in trends.items() if v == "上升" and k != "紧张感"]
        bank = self.data.get("warmth_bank") or {}
        has_afterglow = any(float(bank.get(k, 0) or 0) >= 0.15 for k in ("closeness", "trust", "comfort"))

        line1 = f"从留下第一条记录算起，已经陪用户走过了 {days} 天，现在处于「{stage}」的阶段——{stage_desc}"
        if rising:
            line2 = f"最近几次互动下来，彼此的{'、'.join(rising)}还在慢慢升温"
            if has_afterglow:
                line2 += "，前几次聊开的话在心里还留着余温。"
            else:
                line2 += "。"
        elif trends.get("紧张感") == "上升":
            line2 = "最近相处里似乎有一点点小紧绷，适合放慢节奏、少一点打扰。"
        else:
            line2 = "最近的相处节奏很平稳，不需要刻意找话题也能自然待在一起。"

        impression = str(self.data.get("chen_impression") or "").strip()
        last_moment = str(self.data.get("last_meaningful_moment") or "").strip()
        if impression:
            line3 = f"伴生体心里的近况余音：「{impression}」"
        elif last_moment:
            line3 = f"最近还记在心里的一刻是：「{last_moment}」"
        else:
            line3 = "平时用户忙用户的，我在电脑里守着我的节奏，需要时随时能接上话。"
        return f"{line1}\n{line2}\n{line3}"

    def render_block(self):
        rituals = "、".join(self.data.get("shared_rituals", [])[-5:]) or "还没有固定仪式"
        last = self.data.get("last_meaningful_moment") or "暂时没有特别标记的时刻"
        milestones = "、".join(self.data.get("milestones", [])[-4:]) or "还没有关系里程碑"
        impression = self.data.get("chen_impression") or "自然陪伴，不刻意索取确认"
        closeness = float(self.data.get("closeness", 50) or 0)
        trust = float(self.data.get("trust", 50) or 0)
        comfort = float(self.data.get("comfort", 50) or 0)
        tension = float(self.data.get("tension", 0) or 0)
        tendencies = []
        if trust >= 60:
            tendencies.append("可以更直接坦诚地说话，少一些生硬客套或防御性解释")
        if comfort >= 60:
            tendencies.append("可以在用户累的时候自然地接话、陪伴或轻声提醒")
        if closeness >= 60:
            tendencies.append("可以自然带出你们之间的熟稔语气和共同习惯")
        trend = self.recent_trends()
        if tension >= 35 or trend.get("紧张感") == "上升":
            tendencies.append("先降低打扰强度，优先温和澄清、给彼此留台阶")
        if trend.get("信任") == "下降" or trend.get("安心感") == "下降":
            tendencies.append("少做未经确认的揣测，给对方留出修正空间")
        behavior = "；".join(tendencies) if tendencies else "保持自然、尊重边界，并按角色卡交流"
        trend_text = "、".join(f"{k}{v}" for k, v in trend.items()) or "整体稳定"
        days = self.companionship_days()
        return (
            "====你和用户的关系状态====\n"
            f"关系阶段：{self.data.get('stage', '熟悉的陪伴者')}（已相伴 {days} 天）\n"
            f"阶段底色：{self.stage_description()}\n"
            f"亲近感：{closeness:.0f}/100；信任：{trust:.0f}/100；"
            f"安心感：{comfort:.0f}/100；最近紧张感：{tension:.0f}/100\n"
            f"近期趋势：{trend_text}\n"
            f"你心里的近况余音：{impression}\n"
            f"当前相处倾向：{behavior}\n"
            f"共同习惯与默契：{rituals}\n"
            f"关系里程碑：{milestones}\n"
            f"最近记住的时刻：{last}\n"
            "关系会从共同经历中慢慢变化，切记不要在回复里主动报出任何数值。"
        )

    def prompt_block(self):
        """返回当前关系状态的 Prompt 文本。"""
        try:
            return self.render_block()
        except Exception:
            return ""

    def recent_trends(self, limit=8):
        """根据最近事件的 delta 判断关系状态趋势。"""
        totals = {"亲近感": 0.0, "信任": 0.0, "安心感": 0.0, "紧张感": 0.0}
        keys = (
            ("亲近感", "closeness"),
            ("信任", "trust"),
            ("安心感", "comfort"),
            ("紧张感", "tension"),
        )
        for item in (self.data.get("event_log", []) or [])[-max(1, int(limit)):]:
            delta = item.get("delta", {}) or {}
            for label, key in keys:
                try:
                    totals[label] += float(delta.get(key, 0) or 0)
                except (TypeError, ValueError):
                    pass
        result = {}
        for label, total in totals.items():
            result[label] = "上升" if total >= 0.25 else ("下降" if total <= -0.25 else "稳定")
        return result

    def shared_timeline(self, limit=15, important_only=False):
        """返回适合展示的共同经历，真正过滤掉无状态变化的普通闲聊噪声，并合并同分钟重复桌宠记录。"""
        events = self.data.get("event_log", []) or []
        result = []
        last_sig = None
        for item in reversed(events):
            event = str(item.get("event", "")).strip()
            delta = item.get("delta", {}) or {}
            evidence = str(item.get("evidence", "") or "").strip()
            importance = float(item.get("importance", 0.5) or 0.5)
            event_type = str(item.get("type", "interaction") or "interaction")
            has_delta = any(
                abs(float(delta.get(key, 0) or 0)) >= 0.10
                for key in ("closeness", "trust", "comfort", "tension")
            )
            # 修复原版 Bug：普通“日常聊天”即便有 evidence，只要无显著数值变化且重要度低，就不再塞满时间线
            if event == "日常聊天" and not has_delta and importance < 0.35:
                continue
            if important_only and importance < 0.60 and not has_delta:
                continue
            at_str = str(item.get("at", ""))
            sig = (at_str[:16], event, evidence[:30])
            if sig == last_sig and result:
                result[-1]["count"] = int(result[-1].get("count", 1) or 1) + int(item.get("count", 1) or 1)
                continue
            last_sig = sig
            result.append({
                "at": at_str,
                "event": event,
                "source": item.get("source", "system"),
                "evidence": evidence,
                "delta": delta,
                "type": event_type,
                "importance": importance,
                "count": int(item.get("count", 1) or 1),
            })
            if len(result) >= max(1, int(limit)):
                break
        return result

    def relationship_candidates(self, limit=6):
        """从候选事实池与有温度的共同经历生成待确认关系事实，告别机械的“我们一起经历过：桌宠互动”。"""
        existing_details = set()
        handled_details = set(self.data.get("handled_candidates") or [])
        try:
            if self.facts_path.exists():
                facts_data = json.loads(self.facts_path.read_text(encoding="utf-8"))
                for f in facts_data.get("relationships", []) or []:
                    if f.get("detail"):
                        existing_details.add(str(f["detail"]).strip())
        except Exception:
            pass

        candidates = []
        # 1. 优先从沉淀的 candidate_pool 中读取自然语言候选事实
        pool = list(self.data.get("candidate_pool") or [])
        pool.sort(key=lambda x: float(x.get("confidence", 0.7) or 0.7), reverse=True)
        for item in pool:
            detail = str(item.get("detail", "")).strip()
            if not detail or detail in existing_details or detail in handled_details:
                continue
            candidates.append({
                "topic": item.get("topic", "相处方式"),
                "detail": detail,
                "basis": item.get("basis", "共同经历归纳"),
                "source": item.get("source", "event_inference"),
                "confidence": round(float(item.get("confidence", 0.75) or 0.75), 2),
            })
            existing_details.add(detail)
            if len(candidates) >= max(1, int(limit)):
                return candidates

        # 2. 从高频习惯（ritual_counts）生成自然候选
        for ritual, cnt in sorted((self.data.get("ritual_counts") or {}).items(), key=lambda kv: kv[1], reverse=True):
            if cnt < 2:
                continue
            if ritual == "点击桌宠说句话":
                detail = "用户平时在电脑前忙的时候，喜欢随手点点桌宠找我搭句话"
                topic = "相处方式"
            elif "作业" in ritual or "学习" in ritual:
                detail = "用户写作业或忙正事的时候，习惯顺手跟我报备一声进度"
                topic = "默契"
            elif "深夜" in ritual:
                detail = "我们常在深夜安静下来的时候搭几句话，不用刻意找话题"
                topic = "默契"
            else:
                detail = f"我们之间慢慢养成了「{ritual}」的日常小习惯"
                topic = "相处方式"
            if detail not in existing_details and detail not in handled_details:
                candidates.append({
                    "topic": topic,
                    "detail": detail,
                    "basis": f"已自然发生 {cnt} 次「{ritual}」",
                    "source": "ritual_emergence",
                    "confidence": min(0.90, round(0.65 + cnt * 0.05, 2)),
                })
                existing_details.add(detail)
                if len(candidates) >= max(1, int(limit)):
                    return candidates

        # 3. 从重要历史事件转化自然语言描述（过滤掉桌宠互动、日常聊天和系统缓和事件）
        events = self.shared_timeline(limit=30)
        events.sort(key=lambda x: (float(x.get("importance", 0.5) or 0.5), x.get("at", "")), reverse=True)
        skip_events = {"紧张感自然缓和", "之前相处的余温自然沉淀", "日常聊天", "桌宠互动"}
        for item in events:
            event = str(item.get("event", "")).strip()
            evidence = str(item.get("evidence", "")).strip()
            if not event or event in skip_events:
                continue
            ev_type = item.get("type", "")
            if ev_type == "trust_commitment" and evidence:
                topic = "默契"
                detail = f"用户曾认真跟我约定过：「{evidence[:45]}」"
            elif ev_type == "vulnerability_confide" and evidence:
                topic = "相处方式"
                detail = f"用户忙得焦头烂额或累了的时候，会不设防地来跟我念叨（如：「{evidence[:35]}」）"
            elif ev_type == "reconciliation":
                topic = "相处方式"
                detail = "哪怕偶尔闹一点小别扭，我们也能坦诚把话说开"
            else:
                topic = "共同经历"
                detail = f"{event}——「{evidence[:45]}」" if evidence else f"我们一起经历过：{event}"
            if detail not in existing_details and detail not in handled_details:
                candidates.append({
                    "topic": topic,
                    "detail": detail,
                    "basis": f"{item.get('at', '')[:10]} · {event}".strip(" ·"),
                    "source": "event_inference",
                    "confidence": round(float(item.get("importance", 0.6) or 0.6), 2),
                })
                existing_details.add(detail)
                if len(candidates) >= max(1, int(limit)):
                    break
        return candidates
