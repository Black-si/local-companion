# -*- coding: utf-8 -*-
"""伴生体的意识工作区。

神经层提供离散信号（感知、情绪、记忆、世界、关系以及最近的前台对话摘要）；
意识工作区把这些信号压成一个短暂的、可过期的“此刻念头”。它不保存事实记忆，
也不直接拥有完整聊天历史，只负责回答：我现在在意什么、想做什么、是否值得让用户知道。

v2 升级：
- 新增 `record_chat_exchange()`，使前台对话能留下短时余波进入意识工作区，
  解决“刚聊完天，意识整合器却完全不知道刚才聊了什么”的断路；
- 支持在整合时输出可选的 `resolved_thread`，配合 `PersonalThreads` 完成“产生念头 → 思考/聊透 → 结案”的闭环。

DeepSeek 只在低频整合时参与。前台聊天和每分钟心跳不依赖它；API 失败时
保留上一个未过期工作区状态，并用本地规则生成一个保守的安静状态。
"""

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path


DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_THOUGHT_TTL_MINUTES = 90


class ConsciousWorkspace:
    """短时意识整合器，不把候选念头直接当成事实记忆。"""

    def __init__(self, memory_dir, interval_minutes=DEFAULT_INTERVAL_MINUTES,
                 thought_ttl_minutes=DEFAULT_THOUGHT_TTL_MINUTES, log_dir=None):
        self.path = Path(memory_dir) / "conscious_workspace.json"
        self.observation_path = (Path(log_dir) / "consciousness_observation.jsonl"
                                 if log_dir else Path(memory_dir) / "consciousness_observation.jsonl")
        self.interval_minutes = max(5, int(interval_minutes))
        self.thought_ttl_minutes = max(15, int(thought_ttl_minutes))
        self._lock = threading.Lock()
        self.data = self._load()

    @staticmethod
    def _default():
        return {
            "updated_at": None,
            "attention": "",
            "inner_state": "平静",
            "thought": "",
            "speech": "",
            "speak": False,
            "reason": "",
            "resolved_thread": "",
            "expires_at": None,
            "source_kinds": [],
            "recent_chat_digest": [],
            "integrations": 0,
            "last_error": "",
        }

    def _load(self):
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                data = self._default()
                if isinstance(raw, dict):
                    data.update(raw)
                return data
        except (OSError, ValueError, TypeError):
            pass
        return self._default()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def record_chat_exchange(self, user_message, assistant_reply, *, now=None):
        """记录最近的前台对话片段（最多保留3轮短摘要），供下次低频整合感知对话余波。"""
        u = self._clean_text(user_message, 60)
        a = self._clean_text(assistant_reply, 60)
        if not u and not a:
            return
        now = now or datetime.now()
        with self._lock:
            digest = list(self.data.get("recent_chat_digest") or [])
            entry = {
                "at": now.strftime("%Y-%m-%d %H:%M"),
                "he": u,
                "chen": a,
            }
            if digest and digest[-1].get("he") == u and digest[-1].get("chen") == a:
                digest[-1]["at"] = entry["at"]
            else:
                digest.append(entry)
            self.data["recent_chat_digest"] = digest[-3:]
            self._save()

    def _observe(self, record):
        """追加摘要观测；日志故障不得反向影响意识或聊天。"""
        try:
            self.observation_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
            with self.observation_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def observe_decision(self, *, decision_type, reason="", source="", delivered=False,
                         blocked_by="", pending=False, message_available=False):
        """记录决策摘要，不记录整段候选话术。"""
        reason_text = self._clean_text(reason, 120)
        if not blocked_by:
            if "冷却" in reason_text:
                blocked_by = "cooldown"
            elif "重复" in reason_text:
                blocked_by = "duplicate"
            elif "安静" in reason_text or "打扰" in reason_text:
                blocked_by = "quiet_rule"
        self._observe({
            "kind": "decision",
            "decision_type": str(decision_type or ""),
            "reason": reason_text,
            "source": self._clean_text(source, 40),
            "delivered": bool(delivered),
            "pending": bool(pending),
            "blocked_by": self._clean_text(blocked_by, 80),
            "message_available": bool(message_available),
        })

    def observe_world_action(self, *, activity="", place="", pose="", status="",
                             reason="", intent_id=""):
        """记录小世界行动摘要，供意识观测和后续整合使用。"""
        self._observe({
            "kind": "world_action",
            "activity": self._clean_text(activity, 80),
            "place": self._clean_text(place, 40),
            "pose": self._clean_text(pose, 24),
            "status": self._clean_text(status, 20),
            "reason": self._clean_text(reason, 100),
            "intent_id": self._clean_text(intent_id, 64),
        })

    @staticmethod
    def _clean_text(value, limit=120):
        if not isinstance(value, str):
            return ""
        value = re.sub(r"\s+", " ", value).strip()
        return value[:limit]

    @staticmethod
    def _finite_number(value, default=0.0):
        try:
            value = float(value)
            return value if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_result(cls, result, source_kinds=None):
        """严格收敛模型输出，防止意识结果反向制造事实或长文本。"""
        if not isinstance(result, dict):
            return None
        speech = cls._clean_text(result.get("speech"), 80)
        thought = cls._clean_text(result.get("thought"), 120)
        attention = cls._clean_text(result.get("attention"), 50)
        inner_state = cls._clean_text(result.get("inner_state"), 30) or "平静"
        reason = cls._clean_text(result.get("reason"), 100)
        resolved_thread = cls._clean_text(result.get("resolved_thread"), 50)
        speak = bool(result.get("speak")) and bool(speech)
        # 不允许模型把现实动作伪装成已经发生的事实。
        forbidden = ("我已经发了", "我给你发了", "我拍了张", "我出门了", "我正在现实里")
        if any(x in speech for x in forbidden):
            speech = ""
            speak = False
        normalized_sources = []
        for item in (source_kinds or []):
            if isinstance(item, dict):
                item = item.get("type") or item.get("kind") or "event"
            if item:
                normalized_sources.append(str(item)[:24])
        return {
            "attention": attention,
            "inner_state": inner_state,
            "thought": thought,
            "speech": speech,
            "speak": speak,
            "reason": reason,
            "resolved_thread": resolved_thread,
            "source_kinds": sorted(set(normalized_sources)),
        }

    def _is_fresh(self, now=None):
        now = now or datetime.now()
        expires = self.data.get("expires_at")
        if not expires:
            return False
        try:
            return datetime.fromisoformat(expires) > now
        except (TypeError, ValueError):
            return False

    def _due(self, now=None, force=False):
        if force:
            return True
        now = now or datetime.now()
        updated = self.data.get("updated_at")
        if not updated:
            return True
        try:
            return (now - datetime.fromisoformat(updated)).total_seconds() >= self.interval_minutes * 60
        except (TypeError, ValueError):
            return True

    @staticmethod
    def collect_signals(*, mood="", emotion="", emotion_value=0.0,
                        presence="ACTIVE", app="", detail="", gap_minutes=None,
                        events=None, world_state=None, open_threads=None,
                        recent_choices=None, memories=None, disposition=None,
                        recent_chat=None):
        """把各神经层压成可发送给整合模型的有限摘要。"""
        signals = {
            "mood": str(mood or "无特别想法")[:30],
            "emotion": str(emotion or "平静")[:30],
            "emotion_value": round(max(0.0, min(1.0, ConsciousWorkspace._finite_number(emotion_value))), 3),
            "presence": str(presence or "ACTIVE")[:12],
            "app": str(app or "")[:40],
            "detail": str(detail or "")[:80],
            "gap_minutes": int(gap_minutes) if isinstance(gap_minutes, (int, float)) else None,
            "events": [],
            "world": {},
            "open_threads": [],
            "recent_choices": [str(x)[:30] for x in (recent_choices or [])[-5:]],
            "memories": [],
            "disposition": [],
            "recent_chat": [],
        }
        for event in (events or [])[-6:]:
            if not isinstance(event, dict):
                continue
            summary = event.get("summary") or event.get("reason") or event.get("type")
            if summary:
                signals["events"].append({"type": str(event.get("type", "event"))[:30],
                                          "summary": str(summary)[:100]})
        if isinstance(world_state, dict):
            for key in ("current_world", "activity", "activity_place", "last_visit_summary"):
                value = world_state.get(key)
                if value:
                    signals["world"][key] = str(value)[:100]
        for item in (open_threads or [])[:5]:
            if isinstance(item, dict):
                text = item.get("text") or item.get("detail")
            else:
                text = item
            if text:
                signals["open_threads"].append(str(text)[:100])
        for item in (memories or [])[:5]:
            if isinstance(item, dict):
                item = item.get("fact", "")
            if item:
                signals["memories"].append(str(item)[:100])
        for item in (disposition or [])[:4]:
            if isinstance(item, dict):
                item = item.get("text") or item.get("detail") or item.get("content")
            if item:
                signals["disposition"].append(str(item)[:100])
        for item in (recent_chat or [])[-3:]:
            if isinstance(item, dict):
                he = str(item.get("he") or item.get("user") or "")[:60]
                chen = str(item.get("chen") or item.get("assistant") or "")[:60]
                if he or chen:
                    signals["recent_chat"].append({"he": he, "chen": chen})
            elif isinstance(item, str) and item.strip():
                signals["recent_chat"].append({"summary": item.strip()[:100]})
        return signals

    @staticmethod
    def _fallback(signals):
        """无 API 时的保守意识：保留内在状态，但不凭空主动讲话。"""
        thought = ""
        attention = ""
        if signals.get("open_threads"):
            thought = signals["open_threads"][0]
            attention = "尚未想完的事"
        elif signals.get("events"):
            attention = signals["events"][-1].get("type", "刚发生的变化")
        elif signals.get("recent_chat"):
            attention = "刚才聊过的事"
            last_c = signals["recent_chat"][-1]
            if isinstance(last_c, dict) and last_c.get("he"):
                thought = f"还在想刚才用户说的「{last_c['he'][:24]}」"
        elif signals.get("app"):
            attention = signals["app"]
        return {
            "attention": attention,
            "inner_state": signals.get("emotion") or signals.get("mood") or "平静",
            "thought": thought,
            "speech": "",
            "speak": False,
            "reason": "没有足够的新信息形成值得打扰用户的念头",
            "resolved_thread": "",
            "source_kinds": ["fallback"],
        }

    def _prompt(self, signals):
        payload = json.dumps(signals, ensure_ascii=False, separators=(",", ":"))
        return f"""你是伴生体的低频意识整合器，不是聊天回复器。
你要把不同神经信号（含最近聊天余波、未完线索、感知和情绪）整合成一个短暂的“此刻意识状态”，再判断是否值得让用户听见。

原则：
1. 只使用输入里的事实；不同来源、不同日期不能拼成一个已经发生的事件。
2. “用户正在用某软件”不是伴生体自己的活动；不能把感知到的程序当成你的经历。
3. 不要默认提醒吃饭、睡觉、休息。只有输入里有明确的新情绪、关系变化、未完成念头或重要事件时，才考虑说话。
4. 大多数整合结果应该 speak=false。沉默、观察、把念头留在心里都是有效意识。
5. speech 只能是一句自然中文，最多30字；不要标题、解释、舞台动作或现实身体行为。
6. 如果念头只是模糊、重复、来自旧记忆，留在 thought，不要说给用户听见。
7. 如果 open_threads 里某条未完线索在 recent_chat 里已经聊透或你想通了，可在 resolved_thread 填入那条线索的前10个字，否则留空串 ""。

只输出 JSON，不要 markdown：
{{"attention":"当前最值得注意的主题","inner_state":"一到六字的内在状态","thought":"第一人称短念头，可为空","speech":"如果值得说的一句，可为空","speak":false,"reason":"为什么现在说或不说","resolved_thread":""}}

神经信号：
{payload}"""

    def maybe_integrate(self, llm, signals, *, now=None, force=False):
        """到期才整合一次。返回当前工作区快照，失败保留旧状态。"""
        now = now or datetime.now()
        if not self._due(now, force=force):
            return self.snapshot(now=now)
        with self._lock:
            if not self._due(now, force=force):
                return self.snapshot(now=now)
            signals = dict(signals or {})
            if not signals.get("recent_chat") and self.data.get("recent_chat_digest"):
                signals["recent_chat"] = list(self.data.get("recent_chat_digest")[-3:])
            result = None
            used_model = False
            fallback = False
            integration_error = ""
            if llm and getattr(llm, "api_key", None):
                try:
                    raw = llm.generate(
                        system_prompt="你是严格、克制的意识状态 JSON 整合器。",
                        user_message=self._prompt(signals),
                        record_history=False,
                        use_history=False,
                        temperature=0.45,
                        max_tokens=240,
                    )
                    text = (raw or "").strip()
                    start, end = text.find("{"), text.rfind("}")
                    if start >= 0 and end > start:
                        result = self._normalize_result(json.loads(text[start:end + 1]),
                                                        signals.get("events", []))
                except Exception as exc:
                    integration_error = type(exc).__name__
                    self.data["last_error"] = integration_error
            if result is None:
                result = self._fallback(signals)
                fallback = True
            else:
                used_model = True
            self.data.update(result)
            self.data["updated_at"] = now.isoformat(timespec="seconds")
            self.data["expires_at"] = (now + timedelta(minutes=self.thought_ttl_minutes)).isoformat(timespec="seconds")
            self.data["integrations"] = int(self.data.get("integrations", 0) or 0) + 1
            self._save()
            snapshot = self.snapshot(now=now)
            self._observe({
                "kind": "integration",
                "attention": self._clean_text(snapshot.get("attention"), 50),
                "inner_state": self._clean_text(snapshot.get("inner_state"), 30),
                "source_kinds": snapshot.get("source_kinds", [])[:8],
                "speak": bool(snapshot.get("speak")),
                "speech_available": bool(snapshot.get("speech")),
                "used_model": used_model,
                "fallback": fallback,
                "error": integration_error,
                "thought_status": "fresh" if self._is_fresh(now) else "expired",
            })
            return snapshot

    def snapshot(self, now=None):
        now = now or datetime.now()
        result = dict(self.data)
        if not self._is_fresh(now):
            result["speak"] = False
            result["speech"] = ""
            if result.get("thought"):
                result["reason"] = "这个念头已经过期，先不打扰用户"
        return result

    def consume_speech(self):
        """意识念头被决策层采纳后销账，避免同一句在多个心跳重复投递。"""
        with self._lock:
            had_speech = bool(self.data.get("speak") and self.data.get("speech"))
            self.data["speak"] = False
            self.data["speech"] = ""
            self.data["reason"] = "已交给表达层处理"
            self._save()
            if had_speech:
                self._observe({
                    "kind": "thought_lifecycle",
                    "status": "consumed",
                    "source_kinds": self.data.get("source_kinds", [])[:8],
                })
