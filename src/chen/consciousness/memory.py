"""
记忆模块 - 伴生体的记忆层
第一层：经历记忆（episodic memory）
- 触发式保存：用户提到自己的偏好、经历、人际关系时才提取记忆
- 关键词检索：不用向量数据库，用简单的字符相似度匹配
- 注入system prompt：每次对话检索相关记忆，让伴生体"记得"之前的事
"""

import json
import math
import os
import re
import threading
from functools import wraps
from pathlib import Path
from datetime import datetime
from chen.paths import PROJECT_ROOT


# ============ 遗忘动力学（借鉴 anima 的艾宾浩斯曲线，MIT）============
# 核心思想：用得多的记忆更牢（测试效应），重要的记忆更稳，
# 没人理的记忆按指数曲线衰减，先归档（软遗忘）后清除（硬遗忘）。
# 全部纯本地数学：强化发生在检索时（只改内存），衰减每天跑一次，零 API、毫秒级。
DECAY_LAMBDA = 0.12          # 基础衰减率（每天）
STABILITY_ACCESS = 0.6       # 每被调取一次，衰减稳定性 +0.6
STABILITY_IMPORTANCE = 2.0   # 重要性 1.0 的记忆稳定性 +2.0
ARCHIVE_THRESHOLD = 0.15     # 强度低于此值 → 归档（不再参与检索，可恢复）
PURGE_AFTER_DAYS = 30        # 归档超过此天数 → 移入归档文件（硬遗忘）
REINFORCE_ON_RECALL = 0.08   # 检索命中时的强化幅度
NEW_MEMORY_STRENGTH = 0.6    # 新记忆的初始强度


def _synchronized(method):
    """串行化本地记忆状态读写；使用可重入锁支持维护方法相互调用。"""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return wrapped


class Memory:
    """记忆管理"""

    def __init__(self, memory_dir="../memory"):
        self._state_lock = threading.RLock()
        self.base_dir = PROJECT_ROOT / memory_dir
        # 允许测试、首次启动和独立工具使用一个尚不存在的记忆目录。
        # 生产目录通常已存在，但写入层不应依赖调用方先创建它。
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.episodic_file = self.base_dir / "episodic_memory.json"
        self.relationship_file = self.base_dir / "relationship_memory.json"
        self.long_term_file = self.base_dir / "long_term_memory.json"
        self.archive_file = self.base_dir / "memory_archive.jsonl"
        self.daily_summary_file = self.base_dir / "daily_summary.txt"
        self.user_profile_file = self.base_dir / "user_profile.txt"
        self.important_file = self.base_dir / "important_memories.json"
        self.anniversary_file = self.base_dir / "anniversaries.json"
        # 重要记忆：特别值得记住的时刻（带温度的记忆）
        self.important_memories = self._load_important()
        # 纪念日：重要事件的日期，到了日子会自然想起
        self.anniversaries = self._load_anniversaries()
        self.memories = self._load_episodic()
        # 长期记忆：从事件里沉淀下来的稳定事实/偏好/关系，受保护、不参与200条淘汰
        self.long_term = self._load_long_term()
        # 关系记忆：伴生体与用户之间稳定的关系事实、共同经历、默契（独立于“关于用户单方面”的记忆）
        self.relationships = self._load_relationships()
        self._seed_relationships_if_empty()
        # 遗忘曲线：上次衰减日期（持久化，重启当天不重复跑）
        self._last_decay_date = self._load_decay_date()

    # 无新增反思的固定标记：写入当天段以记录“今天已整理过”，但注入时会被过滤
    NO_UPDATE_MARK = "（今天没有新的了解，未生成反思）"

    # ========== 基础读写 ==========

    def _load_episodic(self):
        """加载事件记忆"""
        if self.episodic_file.exists():
            try:
                with open(self.episodic_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("memories", [])
                if not isinstance(raw, list):
                    return []
                cleaned = []
                for mem in raw:
                    if not isinstance(mem, dict) or not isinstance(mem.get("fact"), str):
                        continue
                    item = dict(mem)
                    item["fact"] = item["fact"].strip()
                    if not item["fact"]:
                        continue
                    item["importance"] = self._normalize_importance(
                        item.get("importance", 0.5))
                    item["tags"] = self._normalize_tags(item.get("tags", []), item["fact"])
                    cleaned.append(item)
                return cleaned
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                # 单个坏文件不能阻止伴生体启动；下一次成功保存会写回完整结构。
                return []
        return []

    @_synchronized
    def _save_episodic(self):
        """
        原子保存事件记忆到文件：先写同目录临时文件，写完再 os.replace 替换正式文件。
        os.replace 在同盘是原子操作——任何时刻进程被杀（关窗口/任务管理器/断电除外），
        正式文件要么是上一版完整内容，要么是新版完整内容，不会出现写一半的损坏JSON。
        """
        data = {
            "description": "事件记忆 - 记录发生过的具体事件",
            "format": {
                "id": "唯一标识",
                "fact": "事件描述",
                "importance": "0.0-1.0，越高越重要",
                "created": "YYYY-MM-DD",
                "source": "conversation|activity|system",
                "tags": "关键词列表，用于检索"
            },
            "memories": self.memories
        }
        tmp_file = self.episodic_file.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, self.episodic_file)

    def _load_long_term(self):
        """加载长期（语义）记忆：沉淀下来的稳定事实。"""
        try:
            if self.long_term_file.exists():
                data = json.loads(self.long_term_file.read_text(encoding="utf-8"))
                facts = data.get("facts", [])
                if isinstance(facts, list):
                    return facts
        except Exception:
            pass
        return []

    @_synchronized
    def _save_long_term(self):
        """原子保存长期记忆。"""
        data = {
            "description": "长期记忆 - 从事件记忆沉淀的稳定事实/偏好/关系，受保护，不随200条上限淘汰",
            "format": {
                "fact": "一条稳定的事实",
                "category": "偏好|学习|家庭|关系|娱乐|作息|健康|其他",
                "importance": "0.0-1.0",
                "tags": "关键词列表，用于检索",
                "first_seen": "首次沉淀日期 YYYY-MM-DD",
                "updated": "最近更新日期",
                "source_ids": "由哪些事件记忆沉淀而来"
            },
            "facts": self.long_term,
        }
        tmp = self.long_term_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.long_term_file)

    def _append_archive(self, records, reason=""):
        """把被清理的记忆追加到归档 jsonl（不物理删除，可追溯，但不再参与检索）。"""
        if not records:
            return
        try:
            self.archive_file.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(self.archive_file, "a", encoding="utf-8") as f:
                for rec in records:
                    rec = dict(rec)
                    rec["_archived_at"] = stamp
                    rec["_archive_reason"] = reason
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 归档失败不影响主流程

    # ========== 关系记忆（伴生体 ↔ 用户） ==========

    # 首次运行的关系基底：只放客观、稳定、确定的关系事实，其余靠每周整理从相处里长出来
    # 公开号不预填任何关系事实；关系只从用户自己的互动中逐步生成。
    RELATIONSHIP_SEED = []

    def _load_relationships(self):
        try:
            if self.relationship_file.exists():
                data = json.loads(self.relationship_file.read_text(encoding="utf-8"))
                rs = data.get("relationships", [])
                if isinstance(rs, list):
                    return rs
        except Exception:
            pass
        return []

    @_synchronized
    def _save_relationships(self):
        # 保留关系界面写入的历史和扩展字段，避免后台记忆整理覆盖关系审计记录。
        try:
            data = json.loads(self.relationship_file.read_text(encoding="utf-8")) \
                if self.relationship_file.exists() else {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data["description"] = "关系记忆 - 伴生体与用户之间稳定的关系事实、默契与共同经历"
        relation_format = data.get("format") if isinstance(data.get("format"), dict) else {}
        relation_format.update({
            "topic": "关系维度，如 共同经历/默契/相处方式/我的位置",
            "detail": "一句话具体内容",
            "importance": "0.0-1.0",
            "updated": "YYYY-MM-DD",
            "source": "seed|consolidation",
        })
        data["format"] = relation_format
        data["relationships"] = self.relationships
        tmp = self.relationship_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.relationship_file)

    def _seed_relationships_if_empty(self):
        """还没有任何关系记忆时，写入少量确定无疑的关系基底；一旦有了内容就不再自动种子。
        文件存在但是空模板时也会初始化；已有任何关系条目则绝不覆盖。"""
        if self.relationships:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        self.relationships = [
            {"topic": t, "detail": d, "importance": i, "updated": today, "source": "seed"}
            for t, d, i in self.RELATIONSHIP_SEED
        ]
        try:
            self._save_relationships()
        except Exception:
            pass

    def _upsert_relationship(self, topic, detail, importance, source, today):
        """同维度且内容高度相似的关系条目合并更新，否则新增。返回 True=新增。"""
        for r in self.relationships:
            if r.get("topic") == topic and self._similarity(detail, r.get("detail", "")) > 0.45:
                if len(detail) > len(r.get("detail", "")):
                    r["detail"] = detail
                r["importance"] = max(r.get("importance", 0.5), importance)
                r["updated"] = today
                r["source"] = source
                return False
        self.relationships.append({
            "topic": topic, "detail": detail, "importance": importance,
            "updated": today, "source": source,
        })
        if len(self.relationships) > 60:  # 关系层很轻，60 条上限很宽松
            self.relationships.sort(key=lambda x: x.get("importance", 0.5))
            self.relationships = self.relationships[-60:]
        return True

    # ========== 重要记忆（带温度的记忆） ==========

    def _load_important(self):
        """加载重要记忆"""
        try:
            if self.important_file.exists():
                data = json.loads(self.important_file.read_text(encoding="utf-8"))
                return data.get("memories", [])
        except Exception:
            pass
        return []

    @_synchronized
    def _save_important(self):
        """保存重要记忆"""
        try:
            self.important_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.important_file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"memories": self.important_memories}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.important_file)
        except Exception:
            pass

    @_synchronized
    def add_important(self, fact, date=None, reason="", tags=None):
        """添加一条重要记忆。
        fact: 记忆内容
        date: 日期（YYYY-MM-DD），默认今天
        reason: 为什么重要
        tags: 关键词列表
        """
        if not fact or not fact.strip():
            return False
        date = date or datetime.now().strftime("%Y-%m-%d")
        # 去重：相似内容不重复添加
        for mem in self.important_memories:
            if self._similarity(fact, mem.get("fact", "")) > 0.6:
                return False
        self.important_memories.append({
            "id": f"imp_{len(self.important_memories)}_{int(datetime.now().timestamp())}",
            "fact": fact.strip(),
            "date": date,
            "reason": reason or "",
            "tags": tags or [],
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        # 最多保留50条重要记忆
        if len(self.important_memories) > 50:
            self.important_memories = self.important_memories[-50:]
        self._save_important()
        return True

    def get_important_memories(self, limit=5, query=""):
        """获取重要记忆。如果有query，按相关性排序；否则按时间倒序。"""
        if not self.important_memories:
            return []
        if query:
            scored = []
            for mem in self.important_memories:
                score = self._similarity(query, mem.get("fact", ""))
                # 标签命中加分
                for tag in mem.get("tags", []):
                    if tag.lower() in query.lower():
                        score += 0.2
                scored.append((score, mem))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [mem for _, mem in scored[:limit]]
        # 按时间倒序
        return sorted(self.important_memories, key=lambda x: x.get("date", ""), reverse=True)[:limit]

    # ========== 纪念日 ==========

    def _load_anniversaries(self):
        """加载纪念日"""
        try:
            if self.anniversary_file.exists():
                data = json.loads(self.anniversary_file.read_text(encoding="utf-8"))
                return data.get("anniversaries", [])
        except Exception:
            pass
        return []

    @_synchronized
    def _save_anniversaries(self):
        """保存纪念日"""
        try:
            self.anniversary_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.anniversary_file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"anniversaries": self.anniversaries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.anniversary_file)
        except Exception:
            pass

    @_synchronized
    def add_anniversary(self, name, date, description=""):
        """添加一个纪念日。
        name: 纪念日名称（如"伴生体第一次成功运行"）
        date: 日期（YYYY-MM-DD）
        description: 描述
        """
        if not name or not date:
            return False
        # 去重
        for ann in self.anniversaries:
            if ann.get("name") == name and ann.get("date") == date:
                return False
        self.anniversaries.append({
            "name": name,
            "date": date,
            "description": description or "",
            "created": datetime.now().strftime("%Y-%m-%d"),
        })
        self._save_anniversaries()
        return True

    def get_today_anniversaries(self):
        """获取今天（或最近3天内）的纪念日。返回列表。"""
        today = datetime.now().date()
        result = []
        for ann in self.anniversaries:
            try:
                ann_date = datetime.strptime(ann["date"], "%Y-%m-%d").date()
                # 计算今年的纪念日（同月同日）
                try:
                    this_year = ann_date.replace(year=today.year)
                except ValueError:
                    # 2月29日这种特殊情况
                    this_year = ann_date.replace(year=today.year, day=28)
                days_diff = abs((today - this_year).days)
                if days_diff <= 3:  # 前后3天内都算
                    years = today.year - ann_date.year
                    result.append({
                        "name": ann["name"],
                        "date": ann["date"],
                        "description": ann.get("description", ""),
                        "years": years,
                        "days_diff": days_diff,
                    })
            except Exception:
                continue
        return result

    def get_relationship_block(self, limit=6):
        """把最重要的若干条关系事实格式化成注入文本（少量常驻，省 token）。"""
        if not self.relationships:
            return ""
        rs = sorted(self.relationships, key=lambda r: r.get("importance", 0.5), reverse=True)
        seen, lines = set(), ["====我和用户（我们之间稳定的事实）===="]
        today = datetime.now().strftime("%Y-%m-%d")
        for r in rs:
            status = str(r.get("status", "confirmed") or "confirmed")
            valid_until = str(r.get("valid_until", "") or "")
            if status != "confirmed" or (valid_until and valid_until < today):
                continue
            detail = (r.get("detail") or "").strip()
            if not detail or detail in seen:
                continue
            seen.add(detail)
            lines.append(f"- {detail}")
            if len(lines) >= limit + 1:
                break
        return "\n".join(lines)

    # ========== 保存记忆 ==========

    @staticmethod
    def _normalize_importance(value, default=0.5):
        """把模型/旧文件里的重要度收敛到可比较的 0..1 浮点数。"""
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        if not math.isfinite(value):
            value = default
        return min(1.0, max(0.0, value))

    def _normalize_tags(self, tags, fact=""):
        """模型偶尔把 tags 返回成字符串或混入非字符串值；统一成短列表。"""
        if isinstance(tags, str):
            tags = [tags]
        elif not isinstance(tags, (list, tuple, set)):
            tags = []
        clean = []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            tag = tag.strip()
            if tag and tag not in clean:
                clean.append(tag[:30])
        return clean[:12] or (self._extract_tags(fact) if fact else [])

    @_synchronized
    def save(self, fact, importance=0.5, source="conversation", tags=None):
        """
        保存一条新记忆，自动去重。
        如果已有相似记忆，更新它而不是新增。
        """
        if not isinstance(fact, str) or not fact.strip():
            return None

        fact = fact.strip()
        importance = self._normalize_importance(importance)
        tags = self._normalize_tags(tags, fact)

        # 去重：和已有记忆相似度超过0.5就更新（字符级Jaccard，中文短句0.5比较合理）
        # 重复即复习：调用旧记忆一次，它变得更牢（测试效应），而不是新增一条囤着
        for i, mem in enumerate(self.memories):
            if self._similarity(fact, mem["fact"]) > 0.5:
                self.memories[i]["fact"] = fact
                old_importance = self._normalize_importance(
                    self.memories[i].get("importance", 0.5))
                self.memories[i]["importance"] = max(old_importance, importance)
                old_tags = self._normalize_tags(self.memories[i].get("tags", []), fact)
                self.memories[i]["tags"] = self._normalize_tags(old_tags + tags, fact)
                self.memories[i]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
                self.memories[i]["access_count"] = self.memories[i].get("access_count", 0) + 1
                self.memories[i]["strength"] = min(
                    1.0, self.memories[i].get("strength", NEW_MEMORY_STRENGTH) + 0.1)
                self._save_episodic()
                return self.memories[i]

        # 新增记忆
        memory = {
            "id": f"mem_{len(self.memories) + 1}_{int(datetime.now().timestamp())}",
            "fact": fact,
            "importance": importance,
            "created": datetime.now().strftime("%Y-%m-%d"),
            "source": source,
            "tags": tags,
            # 遗忘动力学字段：强度会随调取增长、随闲置衰减；老数据没有这些字段，.get 兜底
            "strength": NEW_MEMORY_STRENGTH,
            "access_count": 0,
        }
        self.memories.append(memory)

        # 记忆数量上限：最多200条，超过就删最不重要的
        if len(self.memories) > 200:
            self.memories.sort(key=lambda m: m.get("importance", 0.5))
            self.memories = self.memories[-200:]

        self._save_episodic()
        return memory

    # ========== 检索记忆 ==========

    @_synchronized
    def retrieve(self, query, top_k=5):
        """
        根据当前用户消息检索相关记忆。事件记忆 + 长期记忆一起检索：
        字符相似度 + 标签命中 + 重要度加权；长期事实（已沉淀）给少量稳定加权。
        低于相关性门槛的记忆不返回，避免无关记忆硬塞。
        """
        if not query:
            return []
        query_clean = query.strip()

        def score_one(fact_text, tags, importance, long_term_bonus=0.0):
            sim = self._similarity(query_clean, fact_text or "")
            # 标签命中（双向：查询包含标签，或标签包含查询关键词）
            matched_tags = [tag for tag in (tags or [])
                            if tag and (tag in query_clean or (len(query_clean) >= 2 and query_clean in tag))]
            if sim < 0.12 and not matched_tags:
                return None
            tag_bonus = min(0.25, 0.1 * len(matched_tags))
            return sim * 0.75 + self._normalize_importance(importance) * 0.25 + tag_bonus + long_term_bonus

        scored = []
        # 事件记忆
        for mem in self.memories:
            if mem.get("archived"):
                continue  # 已归档的不再参与检索
            s = score_one(mem.get("fact", ""), mem.get("tags", []), mem.get("importance", 0.5))
            if s is None:
                continue
            if mem.get("consolidated"):
                s -= 0.15  # 已沉淀进长期层的原件，把前排让给凝练后的长期事实
            scored.append((s, mem))
        # 长期记忆（沉淀过的稳定事实，给少量稳定加权）
        for lt in self.long_term:
            tags = lt.get("tags") or self._extract_tags(lt.get("fact", ""))
            s = score_one(lt.get("fact", ""), tags, lt.get("importance", 0.7), long_term_bonus=0.08)
            if s is not None:
                scored.append((s, lt))

        # 按分数排序，取top_k，再过一次最终分数门槛
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [mem for score, mem in scored[:top_k] if score > 0.12]

        # 更新事件记忆的访问信息（长期记忆不写，避免频繁落盘）。
        # 被用到的记忆当场变强（只改内存，随每日衰减一起落盘，聊天路径零额外写盘）
        now = datetime.now().strftime("%Y-%m-%d")
        for mem in results:
            if "source_ids" not in mem:
                mem["last_accessed"] = now
                mem["access_count"] = mem.get("access_count", 0) + 1
                mem["strength"] = min(1.0, mem.get("strength", NEW_MEMORY_STRENGTH) + REINFORCE_ON_RECALL)
        return results

    # ========== 遗忘动力学：每日一次的衰减 / 归档 / 清除 ==========

    @_synchronized
    def decay_and_forget(self, now=None):
        """每天跑一次（内部有当天去重）：艾宾浩斯式衰减 + 软遗忘 + 硬遗忘。
        稳定性 = 1 + 0.6×被调取次数 + 2.0×重要性——常用、重要的记忆几乎不衰；
        没人理的记忆指数衰减，低于阈值先归档（可恢复），归档超 30 天移入归档文件。
        纯本地数学，200 条约 1 毫秒，零 API。返回统计字典，失败返回 None。"""
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        if getattr(self, "_last_decay_date", None) == today:
            return None
        archived_recs, kept = [], []
        stats = {"decayed": 0, "archived": 0, "purged": 0}

        def _days_since(ds):
            try:
                return max(0.0, (now - datetime.strptime(ds, "%Y-%m-%d")).days)
            except Exception:
                return 0.0

        for mem in self.memories:
            if mem.get("archived"):
                # 已归档：超期硬遗忘（移入归档文件，可追溯）
                if _days_since(mem.get("archived_ts", today)) >= PURGE_AFTER_DAYS:
                    archived_recs.append(mem)
                    stats["purged"] += 1
                else:
                    kept.append(mem)
                continue
            days = _days_since(mem.get("last_accessed") or mem.get("created") or today)
            stability = 1.0 + STABILITY_ACCESS * mem.get("access_count", 0) \
                + STABILITY_IMPORTANCE * mem.get("importance", 0.5)
            lam = DECAY_LAMBDA
            if mem.get("consolidated"):
                lam *= 1.6  # 已沉淀进长期层的原件衰减更快：要点已搬家
            s = mem.get("strength", NEW_MEMORY_STRENGTH) * math.exp(-lam * days / stability)
            mem["strength"] = round(s, 4)
            if s < ARCHIVE_THRESHOLD:
                mem["archived"] = True
                mem["archived_ts"] = today
                stats["archived"] += 1
            stats["decayed"] += 1
            kept.append(mem)

        if archived_recs:
            self._append_archive(archived_recs, reason="遗忘曲线清除（归档超30天）")
        self.memories = kept
        self._save_episodic()
        self._last_decay_date = today
        try:
            (self.base_dir / "last_memory_decay.json").write_text(
                json.dumps({"date": today}), encoding="utf-8")
        except Exception:
            pass
        return stats

    def _load_decay_date(self):
        try:
            return json.loads(
                (self.base_dir / "last_memory_decay.json").read_text(encoding="utf-8")
            ).get("date", "")
        except Exception:
            return ""

    # ========== 认知边界：每条记忆都有来处 ==========

    SOURCE_LABELS = {
        "conversation": "用户聊天时告诉他的",
        "activity": "他感知到的",
        "system": "系统记录",
        "seed": "一开始就知道的",
        "consolidation": "他自己沉淀的",
        "small_world": "他在小世界经历的",
        "day_digest": "他一天结束后想明白的",
        "web": "他在网上看到的",
    }

    @classmethod
    def source_label(cls, source):
        """机器来源值 → 伴生体视角的可读来处；未知来源返回空串。"""
        return cls.SOURCE_LABELS.get(source or "", "")

    def format_for_prompt(self, memories):
        """把检索到的记忆格式化成 system prompt 能用的文本（事件/长期统一呈现）。
        每条记忆标注来处——认知边界：他知道的事都有来源，不标注他就"凭空知道"。"""
        if not memories:
            return ""
        lines = ["====相关记忆===="]
        for i, mem in enumerate(memories, 1):
            date = mem.get("created") or mem.get("first_seen") or "未知日期"
            label = self.source_label(mem.get("source"))
            suffix = f"（{label}）" if label else ""
            lines.append(f"{i}. [{date}] {mem.get('fact', '')}{suffix}")
        return "\n".join(lines)

    def prompt_block(self, memories):
        """返回相关记忆及其认知边界提示。"""
        memory_text = self.format_for_prompt(memories)
        if not memory_text:
            return ""
        return (
            memory_text
            + "\n（你知道的每件事都有来处：聊天时用户告诉你、你亲身经历、你在网上注意到的、"
            "或你以前记住的。除此之外的事，你就是不知道，不要装作知道。）"
        )

    # ========== 从对话中提取记忆 ==========

    def should_extract(self, user_message):
        """
        规则判断：这条用户消息是否可能包含值得记住的信息。
        两层判断：精确触发词 + 模糊匹配（"我"+偏好词）
        """
        if not user_message:
            return False

        # 第一层：精确触发词（身份、经历、人际关系等）
        triggers = [
            "我是", "我家", "我妈", "我爸", "我们班", "我朋友", "我闺蜜",
            "我对象", "我男朋友", "我女朋友", "我今天", "我昨天", "我刚才",
            "我终于", "我决定", "我从小", "我以前", "我上次", "我们学校",
            "我老师", "我班主任", "我同学", "我室友", "我生日", "我住",
            "我养", "我吃", "我喝", "我玩", "我叫", "我名字",
        ]
        for keyword in triggers:
            if keyword in user_message:
                return True

        # 第二层：模糊匹配——句子里有"我"且有偏好/情绪词
        if "我" in user_message:
            preference_words = [
                "喜欢", "讨厌", "不喜欢", "爱", "不爱", "想吃", "不想吃",
                "习惯", "最怕", "最烦", "想要", "不想要", "觉得", "认为",
                "希望", "打算", "计划", "开始", "停止", "学会", "不会",
            ]
            for word in preference_words:
                if word in user_message:
                    return True

        return False

    def extract_from_conversation(self, conversation_text, llm):
        """
        用LLM从一段对话中提取值得记住的信息。
        conversation_text: 最近几轮对话的文本
        llm: LLM实例，用于调用模型
        返回：提取到的记忆列表（已保存）
        """
        if not conversation_text or not llm:
            return []

        # 构造已有记忆列表，让LLM不要重复提取
        existing_facts = [m["fact"] for m in self.memories]
        existing_text = "\n".join([f"- {f}" for f in existing_facts]) if existing_facts else "（暂无）"

        prompt = f"""你是伴生体的记忆提取助手。请从下面的对话中提取关于"用户"的信息，保存为长期记忆。

【什么该记】
- 偏好：用户喜欢/讨厌吃什么、玩什么、做什么
- 经历：用户发生了什么事、遇到了什么人
- 人际关系：用户的家人、朋友、老师、同学的情况
- 生活状态：用户的作息、学习习惯、长期生活习惯
- 重要事件：对用户有影响、以后聊天可能仍然有意义的具体事件

【什么不该记】
- 伴生体自己说的话（只记用户的信息）
- 伴生体的言行、以及用户对伴生体某句话的即时反应（"被伴生体问""对伴生体的话有反应"那是对话过程，不是用户的信息）
- 一次性的当下状态（"正在吃东西""刚在干嘛"），除非它体现了反复出现的习惯
- 伴生体不玩游戏、不看视频、不刷手机，绝不能把伴生体当成在玩/在看的主体来记
- 空洞的情绪（"用户很开心"→不记；"用户因为考了好成绩很开心"→可以记）
- 用户对伴生体说的情话、关系承诺和情感宣言（比如“你是我的潜意识”“我不会忘记你”“我喜欢你”“你别走”）是当下情境里的心情，会随时间和情境变化，不是关于用户的稳定事实，不记为高重要度长期记忆；除非用户明确说这是长期不变的事实，否则不记，要记也最多 0.3。
- 已经在对话里反复说过的常识
- 和已有记忆意思重复的内容（看下面的已有记忆列表）

【已有记忆（这些已经记过了，意思差不多的就不要再提取）】
{existing_text}

【重要度打分】
- 0.3：随便提提，可能很快就变（当下情绪、情话、关系承诺、情感宣言一律不超过 0.3）
- 0.6：值得记住，会影响以后聊天
- 0.9：很重要、基本不会变的稳定事实（长期偏好、真实经历、人际关系）；情感宣言不算

【示例】
用户说"我最喜欢吃麻辣烫了" → 记：{{"fact": "用户最喜欢吃麻辣烫", "importance": 0.7, "tags": ["饮食", "麻辣烫"]}}
用户说"我最近开始使用一个新工具" → 记：{{"fact": "用户最近开始使用一个新工具", "importance": 0.6, "tags": ["工具"]}}
用户说"今天好累" → 不记（只有情绪没有事实）
用户说“我永远不会忘记你” → 不记，或最多 0.3（当下的情感宣言，不是稳定事实）

规则：
1. 每条记忆用一句话概括，不要超过30字
2. 最多提取3条
3. 宁可多记一条，不要漏掉重要信息
4. 如果和已有记忆意思重复，就不要提取了
5. 严格按JSON数组格式返回，不要加其他文字

对话内容：
{conversation_text}

返回格式（JSON数组）：
[
  {{"fact": "记忆内容", "importance": 0.6, "tags": ["关键词1", "关键词2"]}}
]
"""

        try:
            # 调用LLM提取记忆
            # record_history=False: 不污染正常对话历史
            # use_history=False: 不带入正常对话历史，避免模型在"聊天"和"输出JSON"之间困惑
            result = llm.generate(
                system_prompt="你是一个严格的JSON输出器，只返回JSON数组，不要加任何其他文字、解释或代码块标记。",
                user_message=prompt,
                record_history=False,
                use_history=False
            )
            if not result:
                self._log_extract("模型返回空")
                return []

            self._log_extract(f"模型返回：{result[:150]}")

            # 清理结果，提取JSON部分
            result = result.strip()
            # 去掉可能的 ```json ... ``` 包裹
            result = re.sub(r'^```(json)?\s*', '', result)
            result = re.sub(r'\s*```$', '', result)
            # 去掉可能的前后多余文字，找到第一个[和最后一个]
            start = result.find('[')
            end = result.rfind(']')
            if start != -1 and end != -1 and end > start:
                result = result[start:end+1]

            memories = json.loads(result)
            if not isinstance(memories, list):
                self._log_extract(f"返回的不是数组，是 {type(memories)}")
                return []

            if len(memories) == 0:
                self._log_extract("模型认为没有值得记住的内容")

            saved = []
            for mem in memories:
                if not isinstance(mem, dict):
                    continue
                fact = mem.get("fact", "")
                if not isinstance(fact, str):
                    continue
                fact = fact.strip()
                if fact:
                    saved_mem = self.save(
                        fact=fact,
                        importance=mem.get("importance", 0.5),
                        tags=mem.get("tags", [])
                    )
                    if saved_mem:
                        saved.append(saved_mem)
            return saved

        except (json.JSONDecodeError, Exception) as e:
            self._log_extract(f"提取失败：{e}")
            if 'result' in locals() and result:
                self._log_extract(f"模型返回内容：{result[:200]}")
            return []

    # ========== 工具方法 ==========

    def _log_extract(self, message):
        """
        记忆提取相关日志写入文件，不打印到聊天界面。
        记忆提取是后台行为，不应打断用户和伴生体的对话。
        日志位置：项目根目录/logs/memory_extract.log
        """
        try:
            log_dir = self.base_dir.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "memory_extract.log"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass  # 日志写入失败不影响主流程

    def _similarity(self, a, b):
        """简单的字符级Jaccard相似度"""
        set_a = set(a)
        set_b = set(b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0

    def _extract_tags(self, text):
        """从文本中简单提取关键词（按标点和空格分割，取2字以上的词）"""
        # 按常见标点和空格分割
        pattern = r"[，。！？、；：\"'（）\s,.!?;:()\[\]{}]+"
        words = re.split(pattern, text)
        tags = [w.strip() for w in words if len(w.strip()) >= 2]
        # 去重，最多取5个
        return list(set(tags))[:5]

    def get_user_profile(self):
        """读取用户画像"""
        if self.user_profile_file.exists():
            with open(self.user_profile_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_current_state(self):
        """获取当前状态"""
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "weekday": datetime.now().strftime("%A")
        }

    def daily_reflection(self, llm, activity_summary="", force=False):
        """
        每日反思（记忆系统第二层）：整理当天新增的记忆和活动，只沉淀“今天新了解到的关于用户的事实”。
        - force=False（默认）：今天已经反思过（含“无新增”标记）直接返回 "already"，不重复调模型；
        - 有新事实：写入并返回反思文本；
        - 没有新事实：写入“无新增”标记（注入时会被过滤），返回 None，当天不再重复；
        - 模型失败：返回 None，且不写标记，下次可重试。
        activity_summary: 感知层提供的当天活动汇总（可选，仅作背景）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if not force and self._today_reflection_body(today) is not None:
            print("  📭 今天已经反思过了，不重复生成。")
            return "already"

        today_memories = [m for m in self.memories if m.get("created") == today]

        if not today_memories and not activity_summary:
            print("  📭 今天没有新增记忆和活动记录，无需反思。")
            return None

        # 读取当前 user_profile
        user_profile = ""
        if self.user_profile_file.exists():
            with open(self.user_profile_file, "r", encoding="utf-8") as f:
                user_profile = f.read().strip()

        # 构造当天记忆文本
        memory_text = "\n".join([f"- {m['fact']}" for m in today_memories]) if today_memories else "（今天没有新增对话记忆）"

        # 活动文本
        activity_text = activity_summary if activity_summary else "（今天没有活动记录）"

        prompt = f"""你是伴生体，在整理今天对用户的了解。只记录今天【新出现】的、关于用户本人的事实。

【今天新增的对话记忆】
{memory_text}

【今天的活动记录】（用户在电脑上做了什么，只是背景，不是让你汇报用户用了多久软件）
{activity_text}

【你已经了解的用户】
{user_profile}

【判断规则】
1. 只写今天相比过去【新出现】的事实：新的偏好、新发生的具体事、人际关系/学习/状态的新变化。
2. 活动记录只有在能推出具体新事实时才提：例如 B 站反复出现某学科的学习视频标题，才可客观写“用户今天在看某学科的学习视频，可能在补这一科”；只看一两次不下结论。
3. 严禁把“用户今天 B 站看了很久 / 玩了很久游戏 / 作息和以前一样”这类每天都一样的流水账、或你早就了解的旧内容再写一遍。
4. 不分析情绪、不评价、不编造、不写“用户真是个……”。
5. 如果今天没有任何新的事实性了解，只回复四个字：无新增
6. 确有新内容时：不超过120字，用第一人称“我”，平实陈述事实。

直接输出：要么只有“无新增”四个字，要么是那段反思。不要标题、日期、解释或其他文字。"""

        try:
            result = llm.generate(
                system_prompt="你是伴生体，正在写每日反思。只输出反思内容，不要加解释、标题或日期。",
                user_message=prompt,
                record_history=False,
                use_history=False
            )
            if not result:
                print("  ⚠️ 每日反思：模型返回空")
                return None

            result = result.strip()

            # 模型判断今天没有新事实：写“无新增”标记（当天不重复、注入时被过滤），不硬凑注水
            compact = re.sub(r"[。！？\s，,.!?:：；;、]+", "", result)
            no_update_variants = {
                "无新增", "无", "没有新增", "无新的了解", "没有新的了解", "今天没有新的了解",
            }
            if compact in no_update_variants or result.startswith("无新增"):
                self._write_reflection(today, self.NO_UPDATE_MARK)
                print("  📭 今天没有新的了解，不生成反思。")
                return None

            # 有新内容才写入；今天写过就整段替换，避免一天堆好几条
            self._write_reflection(today, result)
            print(f"  📝 每日反思已保存")
            return result

        except Exception as e:
            print(f"⚠️ 每日反思失败：{e}")
            return None

    def _today_reflection_body(self, today):
        """返回 daily_summary 里今天那段反思正文；今天没写过返回 None（含“无新增”标记也算写过）。"""
        if not self.daily_summary_file.exists():
            return None
        content = self.daily_summary_file.read_text(encoding="utf-8")
        dates = list(re.finditer(r"\n?(\d{4}-\d{2}-\d{2})：", content))
        for i, m in enumerate(dates):
            if m.group(1) == today:
                start = m.end()
                end = dates[i + 1].start() if i + 1 < len(dates) else len(content)
                return content[start:end].strip()
        return None

    @_synchronized
    def _write_reflection(self, today, result):
        """写入某天的反思：该天已有则整段替换，没有则追加。保证每天最多一条。"""
        header = f"{today}："
        new_block = f"\n{header}\n{result}\n"

        if not self.daily_summary_file.exists():
            with open(self.daily_summary_file, "w", encoding="utf-8") as f:
                f.write("每日摘要记录\n格式：YYYY-MM-DD：当天发生的关键事情、用户的状态、与伴生体的互动要点\n")
                f.write(new_block)
            return

        with open(self.daily_summary_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 按 "YYYY-MM-DD：" 这种日期头切分，找到今天那段并替换
        date_pattern = re.compile(r"\n?(\d{4}-\d{2}-\d{2})：")
        matches = list(date_pattern.finditer(content))
        if not matches:
            with open(self.daily_summary_file, "a", encoding="utf-8") as f:
                f.write(new_block)
            return

        # 找到今天那段的起止位置
        new_content = content
        for i, m in enumerate(matches):
            if m.group(1) == today:
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                new_content = content[:start] + f"\n{header}\n{result}\n" + content[end:]
                break
        else:
            new_content = content.rstrip() + new_block

        # 原子写盘
        tmp = self.daily_summary_file.with_suffix(".txt.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, self.daily_summary_file)

    def write_daily_summary(self, summary):
        """追加每日摘要"""
        with open(self.daily_summary_file, "a", encoding="utf-8") as f:
            date_str = datetime.now().strftime("%Y-%m-%d")
            f.write(f"{date_str}：{summary}\n")

    def get_memory_count(self):
        """返回当前记忆条数，方便调试"""
        return len(self.memories)

    # ========== 自动整理（启动时每天跑一次） ==========

    @_synchronized
    def consolidate_memories(self, threshold=0.62):
        """
        合并重复/高度相似的碎片记忆。
        - fact完全相同，或字符相似度超过threshold，判为同一件事
        - 保留importance更高的那条描述，tags合并，时间取最早，重要度取最大
        保守阈值，宁可少合并不误合并。返回实际合并掉的条数。
        """
        if len(self.memories) < 2:
            return 0

        merged_count = 0
        changed = True
        while changed:
            changed = False
            n = len(self.memories)
            for i in range(n):
                if changed:
                    break
                for j in range(i + 1, n):
                    a, b = self.memories[i], self.memories[j]
                    same = a["fact"].strip() == b["fact"].strip()
                    similar = self._similarity(a["fact"], b["fact"]) >= threshold
                    if not (same or similar):
                        continue

                    # 选importance高的作为主条目（一样高就选字多的，信息更全）
                    if a.get("importance", 0.5) > b.get("importance", 0.5):
                        keep, drop = a, b
                    elif a.get("importance", 0.5) < b.get("importance", 0.5):
                        keep, drop = b, a
                    else:
                        keep, drop = (a, b) if len(a["fact"]) >= len(b["fact"]) else (b, a)

                    keep["importance"] = max(a.get("importance", 0.5), b.get("importance", 0.5))
                    keep["tags"] = list(dict.fromkeys(a.get("tags", []) + b.get("tags", [])))
                    # 创建时间取更早的
                    dates = [d for d in [a.get("created"), b.get("created")] if d]
                    if dates:
                        keep["created"] = min(dates)
                    # 访问/更新时间取较新的
                    for key in ["last_accessed", "last_updated"]:
                        vals = [d.get(key) for d in [a, b] if d.get(key)]
                        if vals:
                            keep[key] = max(vals)

                    self.memories.remove(drop)
                    merged_count += 1
                    changed = True
                    break

        if merged_count > 0:
            self._save_episodic()
        return merged_count

    # 对话过程/视角错位产生的伪记忆特征：提到“伴生体”且只是在描述一次互动动作，
    # 或把伴生体当成在玩游戏/看视频的主体——这些不是关于用户的稳定事实。
    _FRAGMENT_MARKERS = [
        "被伴生体", "伴生体问", "伴生体说", "伴生体让", "伴生体劝", "对伴生体", "回答伴生体",
        "伴生体的话", "伴生体提醒", "伴生体告诉", "伴生体觉得", "伴生体玩", "伴生体看",
        "伴生体在玩", "伴生体在看", "伴生体一起玩",
    ]

    @_synchronized
    def prune_conversation_fragments(self, max_importance=0.7):
        """
        归档“对话过程/视角错位”类伪记忆（如“用户被伴生体询问”“用户对伴生体玩的游戏有反应”）。
        只处理重要度低于 max_importance 的，高重要度的关系/事件一律保留。
        移到 memory_archive.jsonl，可追溯、可恢复，不参与检索。返回归档条数。
        """
        junk, keep_idx = [], []
        for mem in self.memories:
            fact = mem.get("fact", "")
            is_fragment = ("伴生体" in fact and any(m in fact for m in self._FRAGMENT_MARKERS))
            if is_fragment and mem.get("importance", 0.5) < max_importance and not mem.get("consolidated"):
                junk.append(mem)
            else:
                keep_idx.append(mem)
        if not junk:
            return 0
        self._append_archive(junk, reason="对话过程/视角错位伪记忆")
        self.memories = keep_idx
        self._save_episodic()
        return len(junk)

    # 明显的“一次性临时状态”时间词；出现这些词的记忆过了足够久就基本失效
    _EPHEMERAL_MARKERS = [
        "今天", "昨晚", "昨天", "刚才", "刚刚", "这周", "本周", "这几天",
        "这会儿", "此刻", "正在", "现在在",
    ]

    @_synchronized
    def decay_ephemeral(self, after_days=14, floor=0.25, max_importance=0.7):
        """
        时效衰减：带临时时间词、且已超过 after_days 的低重要度记忆，判定为过期一次性状态，
        把重要度压到 floor 以下，让它在检索里退后、在200条上限时优先被淘汰（不直接删）。
        高重要度的具体经历（如重要事件 0.9）不受影响。返回降权条数。
        """
        today = datetime.now().date()
        count = 0
        for mem in self.memories:
            if mem.get("decayed") or mem.get("consolidated"):
                continue
            if mem.get("importance", 0.5) >= max_importance:
                continue
            fact = mem.get("fact", "")
            if not any(m in fact for m in self._EPHEMERAL_MARKERS):
                continue
            created = mem.get("created")
            if not created:
                continue
            try:
                age = (today - datetime.strptime(created, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            if age >= after_days and mem.get("importance", 0.5) > floor:
                mem["importance"] = floor
                mem["decayed"] = True
                count += 1
        if count:
            self._save_episodic()
        return count

    @_synchronized
    def daily_maintain(self):
        """
        每日确定性轻整理（不调LLM，启动时每天一次）：
        合并高相似碎片 → 归档对话过程/视角错位伪记忆 → 过期临时状态降权 → 清理重复反思。
        返回各项处理条数。
        """
        return {
            "merged": self.consolidate_memories(),
            "pruned": self.prune_conversation_fragments(),
            "decayed": self.decay_ephemeral(),
            "dedup_reflection": self.dedup_daily_summary(),
        }

    # ========== 每周深度整理（低频，调一次LLM批量归纳） ==========

    def _upsert_long_term(self, fact, category, importance, tags, source_ids, today):
        """
        把一条稳定事实写入长期记忆；与已有长期事实相似度>0.5视为同一条，做合并更新。
        返回 True 表示新增，False 表示合并进了已有条目。
        """
        for lt in self.long_term:
            if self._similarity(fact, lt.get("fact", "")) > 0.5:
                if len(fact) > len(lt.get("fact", "")):
                    lt["fact"] = fact  # 信息更全的描述覆盖
                lt["importance"] = max(lt.get("importance", 0.5), importance)
                lt["tags"] = list(dict.fromkeys((lt.get("tags") or []) + (tags or [])))
                lt["category"] = lt.get("category") or category
                lt["source_ids"] = list(dict.fromkeys((lt.get("source_ids") or []) + (source_ids or [])))
                lt["updated"] = today
                return False
        self.long_term.append({
            "fact": fact,
            "category": category,
            "importance": importance,
            "tags": tags or [],
            "first_seen": today,
            "updated": today,
            "source_ids": source_ids or [],
        })
        # 长期记忆上限保护（凝练后的事实，150 条很宽松）
        if len(self.long_term) > 150:
            self.long_term.sort(key=lambda x: x.get("importance", 0.5))
            self.long_term = self.long_term[-150:]
        return True

    def weekly_consolidation(self, llm, batch_size=40):
        """
        每周一次的深度整理（一次LLM调用批量处理，省资源）：
        - promote：把稳定的偏好/身份/关系/长期状况/反复出现的模式沉淀进长期记忆，
          相关事件原件标记 consolidated（低重要度的降权，不删除）；
        - discard：一次性状态、对话过程、空洞/错误记录，归档（高重要度受代码保护不归档）；
        - 其余 keep 原样保留。
        返回 {promoted, merged, discarded, candidates}；无候选返回零值，失败返回 None。
        """
        if not llm:
            return None
        candidates = [m for m in self.memories if not m.get("consolidated")]
        if not candidates:
            return {"promoted": 0, "merged": 0, "discarded": 0, "candidates": 0}

        # 老的先沉淀；每批最多 batch_size 条，控制单次上下文
        candidates.sort(key=lambda m: m.get("created", "9999"))
        batch = candidates[:batch_size]

        long_term_text = "\n".join(f"- {x.get('fact', '')}" for x in self.long_term) or "（暂无）"
        cand_lines = [
            f"{i}. [{m.get('importance', 0.5)}][{m.get('created', '?')}] {m.get('fact', '')}"
            for i, m in enumerate(batch)
        ]
        cand_text = "\n".join(cand_lines)

        prompt = f"""你是伴生体的记忆整理助手。伴生体每天会把发生的具体事记成“事件记忆”，日积月累会很杂。
请把其中【已经稳定、长期有用的事实】沉淀进长期记忆（promote），把【一次性、没长期价值的】挑出丢弃（discard），其余原样保留（keep）。

【现有长期记忆（已经沉淀过，意思差不多的别再重复）】
{long_term_text}

【待整理的事件记忆】（编号. [重要度][日期] 内容）
{cand_text}

【该沉淀 promote】
- 稳定的偏好/喜恶（爱吃什么、讨厌什么、玩什么、兴趣）
- 稳定的身份、家庭、学校、人际关系情况
- 长期的学习状况、生活习惯、健康、目标计划
- 几条相关、能合并成一句更完整理解的事件（合并成一条，source_indexes 写出编号）
- 对用户影响较深、以后大概率还会提起的重要经历
【该丢弃 discard】
- 一次性当下状态（今天吃没吃、刚在干嘛、出门麻不麻烦）
- 对话过程、伴生体问了什么、用户对伴生体某句话的即时反应
- 空洞情绪、没有实质事实、或明显错误/矛盾的记录
【该保留 keep】
- 拿不准是否长期有效、或最近才发生还需观察的，就保留，别急着沉淀或丢弃

【relations 关系沉淀】
另外，把体现“伴生体和用户之间关系”的稳定事实放进 relations（是关于你们俩的，不是关于用户单方面的）：
- 一起经历的标志性事、彼此的默契、用户对你的意义、你在用户生活里的位置、相处方式的变化
- 没有就给空数组，宁缺毋滥
每条字段：topic（共同经历/默契/相处方式/我的位置 等）、detail（一句话）、importance、source_indexes（可选）

【要求】
1. promote 的事实一句话、不超过30字、客观陈述，不写日期流水账（日期本身重要除外）
2. importance：稳定且重要0.8，一般长期有用0.6，可能有用0.4
3. category 只能从：偏好/学习/家庭/关系/娱乐/作息/健康/其他 里选
4. 宁缺毋滥，不确定就保留，不要硬凑
5. 严格只输出一个JSON，不要解释、不要代码块标记

{{"promote":[{{"fact":"...","category":"偏好","importance":0.8,"tags":["关键词"],"source_indexes":[0,2]}}],"relations":[{{"topic":"默契","detail":"一句话","importance":0.7,"source_indexes":[1]}}],"discard_indexes":[3],"note":""}}"""

        try:
            result = llm.generate(
                system_prompt="你是一个严格的JSON输出器，只输出JSON，不要任何其他文字或代码块标记。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
            if not result:
                self._log_extract("每周整理：模型返回空")
                return None
            result = result.strip()
            result = re.sub(r'^```(json)?\s*', '', result)
            result = re.sub(r'\s*```$', '', result)
            start, end = result.find('{'), result.rfind('}')
            if start == -1 or end == -1 or end <= start:
                self._log_extract(f"每周整理：返回里没有JSON对象：{result[:120]}")
                return None
            data = json.loads(result[start:end + 1])
            if not isinstance(data, dict):
                return None
        except Exception as e:
            self._log_extract(f"每周整理失败：{e}")
            return None

        today = datetime.now().strftime("%Y-%m-%d")
        promoted = merged = discarded = rel_new = 0

        for item in data.get("promote", []) or []:
            if not isinstance(item, dict):
                continue
            fact = (item.get("fact") or "").strip()
            if not fact:
                continue
            tags = item.get("tags") or self._extract_tags(fact)
            try:
                importance = float(item.get("importance", 0.6))
            except (TypeError, ValueError):
                importance = 0.6
            importance = min(0.95, max(0.1, importance))
            category = item.get("category", "其他")
            src_ids, touched = [], []
            for ix in item.get("source_indexes", []) or []:
                if isinstance(ix, int) and 0 <= ix < len(batch):
                    src_ids.append(batch[ix].get("id"))
                    touched.append(batch[ix])
            is_new = self._upsert_long_term(fact, category, importance, tags, src_ids, today)
            promoted += 1 if is_new else 1
            merged += 0 if is_new else 1
            # 源事件标记已沉淀；低重要度原件降权（长期层已有凝练版），高重要度经历保留细节
            for sm in touched:
                sm["consolidated"] = True
                if sm.get("importance", 0.5) < 0.8:
                    sm["importance"] = 0.2

        # discard：代码侧再保守校验，高重要度（≥0.75）绝不归档
        junk = []
        for ix in data.get("discard_indexes", []) or []:
            if isinstance(ix, int) and 0 <= ix < len(batch):
                sm = batch[ix]
                if sm.get("importance", 0.5) < 0.75 and not sm.get("consolidated"):
                    junk.append(sm)
        if junk:
            self._append_archive(junk, reason="每周整理判定无长期价值")
            junk_ids = {id(x) for x in junk}
            self.memories = [m for m in self.memories if id(m) not in junk_ids]
            discarded = len(junk)

        # 关系沉淀（关于“伴生体↔用户”之间的稳定事实，独立一层，不改动源事件）
        for item in data.get("relations", []) or []:
            if not isinstance(item, dict):
                continue
            detail = (item.get("detail") or "").strip()
            if not detail:
                continue
            topic = (item.get("topic") or "我们").strip()
            try:
                rel_imp = float(item.get("importance", 0.6))
            except (TypeError, ValueError):
                rel_imp = 0.6
            rel_imp = min(0.95, max(0.1, rel_imp))
            if self._upsert_relationship(topic, detail, rel_imp, "consolidation", today):
                rel_new += 1

        if promoted or merged or discarded:
            self._save_long_term()
            self._save_episodic()
        if rel_new:
            self._save_relationships()
        self._log_extract(
            f"每周整理完成：新增长期{promoted - merged}条、合并{merged}条、"
            f"归档{discarded}条、新增关系{rel_new}条"
        )
        return {"promoted": promoted - merged, "merged": merged, "discarded": discarded,
                "relations": rel_new, "candidates": len(batch)}

    @_synchronized
    def dedup_daily_summary(self):
        """清理daily_summary.txt里同一天重复的多段反思，每天只保留最后一段。返回清理段数。"""
        if not self.daily_summary_file.exists():
            return 0

        with open(self.daily_summary_file, "r", encoding="utf-8") as f:
            content = f.read()

        date_pattern = re.compile(r"\n?(\d{4}-\d{2}-\d{2})：")
        matches = list(date_pattern.finditer(content))
        if len(matches) <= 1:
            return 0

        # 头部说明（第一个日期之前的内容）保留，但去掉"（暂无记录）"这类占位行
        header_lines = [
            line for line in content[:matches[0].start()].splitlines()
            if line.strip() and "暂无记录" not in line
        ]
        header = "\n".join(header_lines).strip()
        # 按日期切段
        blocks = {}
        order = []
        for i, m in enumerate(matches):
            day = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            if day not in blocks:
                order.append(day)
            blocks[day] = body  # 后出现的覆盖前面的

        # 统计原始段数
        total_blocks = len(matches)
        unique_days = len(order)
        removed = total_blocks - unique_days

        # 重建后的头部（用于判断是否需要清理占位行）
        out = [header] if header else []
        for day in order:
            out.append(f"{day}：\n{blocks[day]}")
        new_content = "\n\n".join(out) + "\n"

        # 没有重复段、且内容没变化，就不用重写
        if removed <= 0 and new_content.strip() == content.strip():
            return 0

        tmp = self.daily_summary_file.with_suffix(".txt.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, self.daily_summary_file)
        return removed
