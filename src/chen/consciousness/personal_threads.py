# -*- coding: utf-8 -*-
"""伴生体的个人线索。

把已经存在的挂起念头、意识工作区念头和选择轨迹保留成少量、可继续追踪的线索。
这里只做确定性筛选和文件读写，不在聊天轮次里调用模型。

v2 升级：
- 引入 `text_similarity` 与 `extract_salient_keywords`，解决原来仅靠标点切长句导致 `relevant()` 几乎无法命中的问题；
- `_find()` 支持相似语义归并，让同一主题的不同表述能顺利累加 `evidence` 跨过门槛；
- 新增 `on_chat_turn()` 与 `sync_from_workspace()`，真正打通 `resolve()` 结案闭环（不再只能靠14天超时休眠）。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from chen.consciousness import extract_salient_keywords, text_similarity


CAP = 24
STALE_DAYS = 14


class PersonalThreads:
    """伴生体尚未想完、尚未决定是否继续的个人问题。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "personal_threads.json"
        self.data = self._load()

    def _default(self):
        return {"threads": [], "updated_at": None}

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._default()
                base.update(data)
                return base
        except Exception:
            pass
        return self._default()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    @staticmethod
    def _now():
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _find(self, key, text=""):
        """按精确 key 或高语义相似度查找尚未结案的线索。"""
        for thread in self.data.get("threads", []):
            if thread.get("status") != "open":
                continue
            if thread.get("key") == key:
                return thread
            probe = text or key
            if probe and text_similarity(probe, thread.get("text", "")) >= 0.48:
                return thread
        return None

    def observe(self, text, *, source="", key="", evidence_id=""):
        """收下一条值得继续想的伴生体自己的线索；重复或相似内容累加证据。"""
        text = " ".join((text or "").strip().split())
        if len(text) < 6:
            return False
        key = (key or text[:24]).strip()[:40]
        found = self._find(key, text=text)
        now = self._now()
        if found:
            evidence_ids = found.setdefault("evidence_ids", [])
            changed = False
            if evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id[:60])
                found["evidence"] = int(found.get("evidence", 1)) + 1
                found["evidence_ids"] = evidence_ids[-30:]
                found["last_seen"] = now
                changed = True
            elif not evidence_id:
                found["last_seen"] = now
                changed = True
            if source and source not in found.setdefault("sources", []):
                found["sources"].append(source[:30])
                changed = True
            if len(text) > len(found.get("text", "")) and len(text) <= 100:
                found["text"] = text
                changed = True
            if not changed:
                return False
        else:
            self.data.setdefault("threads", []).append({
                "id": f"th_{int(datetime.now().timestamp() * 1000)}_{len(self.data.get('threads', []))}",
                "key": key,
                "text": text[:100],
                "status": "open",
                "evidence": 1,
                "first_seen": now,
                "last_seen": now,
                "sources": [source[:30]] if source else [],
                "evidence_ids": [evidence_id[:60]] if evidence_id else [],
            })
            self.data["threads"] = self.data["threads"][-CAP:]
        self.data["updated_at"] = now
        self._save()
        return True

    def resolve(self, key, outcome=""):
        """将匹配的 open 线索结案（支持按 key、子串或语义相似度匹配）。"""
        key = (key or "").strip()
        if not key:
            return False
        thread = self._find(key, text=key)
        if not thread:
            for cand in self.data.get("threads", []):
                if cand.get("status") != "open":
                    continue
                if key in cand.get("text", "") or key in cand.get("key", "") or text_similarity(key, cand.get("text", "")) >= 0.40:
                    thread = cand
                    break
        if not thread:
            return False
        thread["status"] = "resolved"
        thread["resolved_at"] = self._now()
        if outcome:
            thread["outcome"] = outcome[:100]
        self.data["updated_at"] = self._now()
        self._save()
        return True

    def on_chat_turn(self, user_message, reply, *, now=None):
        """
        对话后推进个人线索：
        - 如果本轮对话正好聊到了某条 open 线索，且回复中体现了“想通/确认/回应”，将其结案（resolved）；
        - 如果只是初步提及（evidence == 1），则为其累加一次对话证据，使其跨过 2 次证据门槛。
        """
        u = (user_message or "").strip()
        a = (assistant_reply := (reply or "").strip())
        if not u and not a:
            return 0
        combined = f"{u} {a}"
        now_str = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
        resolution_cues = (
            "原来是", "明白了", "想通了", "懂了", "那就这么", "定下来",
            "解决了", "说清楚了", "其实就", "没问题了", "不用纠结",
        )
        changed = 0
        for thread in self.data.get("threads", []):
            if thread.get("status") != "open":
                continue
            t_text = thread.get("text", "")
            kws = self._words(t_text)
            kw_hit = any(w in combined for w in kws)
            sim = max(text_similarity(u, t_text), text_similarity(a, t_text))
            if not (kw_hit or sim >= 0.28):
                continue

            ev = int(thread.get("evidence", 1))
            if ev >= 2 and (any(c in combined for c in resolution_cues) or sim >= 0.45):
                thread["status"] = "resolved"
                thread["resolved_at"] = now_str
                thread["outcome"] = f"在对话中聊开：{a[:45]}"
                changed += 1
            else:
                ev_id = f"chat_{now_str}_{u[:12]}"
                ev_ids = thread.setdefault("evidence_ids", [])
                if ev_id not in ev_ids:
                    ev_ids.append(ev_id)
                    thread["evidence"] = ev + 1
                    thread["last_seen"] = now_str
                    changed += 1
        if changed:
            self.data["updated_at"] = now_str
            self._save()
        return changed

    def sync_from_workspace(self, workspace_snapshot):
        """从意识工作区快照同步：结案已想通的线索，并吸纳有分量的短时念头。"""
        if not isinstance(workspace_snapshot, dict):
            return
        resolved_key = (workspace_snapshot.get("resolved_thread") or "").strip()
        if resolved_key:
            self.resolve(resolved_key, outcome=workspace_snapshot.get("thought") or "意识整合已想通")
        thought = (workspace_snapshot.get("thought") or "").strip()
        updated_at = workspace_snapshot.get("updated_at") or ""
        if len(thought) >= 8 and workspace_snapshot.get("source_kinds") != ["fallback"]:
            self.observe(
                thought,
                source="workspace",
                evidence_id=f"ws_{updated_at or self._now()}",
            )

    @staticmethod
    def _words(text):
        text = (text or "").strip()
        out, buf = [], ""
        for ch in text:
            if ch in ",，、。！？!?；;:/· /":
                if len(buf) >= 2:
                    out.append(buf)
                buf = ""
            else:
                buf += ch
        if len(buf) >= 2:
            out.append(buf)
        # 补充 2~4 字实义关键词，解决长分句无标点时无法子串命中的问题
        for kw in extract_salient_keywords(text, max_keywords=6):
            if kw not in out:
                out.append(kw)
        return out

    def relevant(self, query, limit=3):
        """按当前消息找相关的、已有重复证据的线索。"""
        query = (query or "").strip()
        if not query:
            return []
        candidates = []
        for thread in self.data.get("threads", []):
            if thread.get("status") != "open" or int(thread.get("evidence", 0)) < 2:
                continue
            haystack = " ".join([
                thread.get("text", ""),
                thread.get("key", ""),
                " ".join(thread.get("sources", []) or []),
            ])
            words = self._words(haystack)
            kw_hit = any(word in query for word in words)
            sim = text_similarity(query, thread.get("text", ""))
            if kw_hit or sim >= 0.24:
                candidates.append((sim + (0.5 if kw_hit else 0.0), thread))
        candidates.sort(key=lambda item: (item[0], int(item[1].get("evidence", 0)), item[1].get("last_seen", "")), reverse=True)
        return [t for _, t in candidates[:limit]]

    def prune(self, now=None):
        now = now or datetime.now()
        changed = 0
        for thread in self.data.get("threads", []):
            if thread.get("status") != "open":
                continue
            try:
                seen = datetime.strptime(thread.get("last_seen"), "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if now - seen > timedelta(days=STALE_DAYS):
                thread["status"] = "dormant"
                changed += 1
        if changed:
            self.data["updated_at"] = self._now()
            self._save()
        return changed

    def render_block(self, query="", limit=3):
        """生成很短的 prompt 块；不返回一次性/低证据噪声。"""
        open_threads = self.relevant(query, limit=limit) if query else []
        open_threads.sort(key=lambda t: (int(t.get("evidence", 0)), t.get("last_seen", "")), reverse=True)
        if not open_threads:
            return ""
        lines = [
            "====你还没想完的自己的线索====",
            "这些是你自己还没完全想明白的事。只有当前话题自然相关时才提，不要为了完成清单主动解释。",
        ]
        for thread in open_threads[:limit]:
            lines.append(f"- {thread.get('text', '')}")
        return "\n".join(lines)
