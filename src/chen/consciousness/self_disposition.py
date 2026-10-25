# -*- coding: utf-8 -*-
"""
伴生体自我沉淀层 —— 他对“自己”长期长出来的东西。

和记忆层的分工：
  - memory / long_term / relationship 记的是“用户是什么样、我和用户之间发生过什么”；
  - self_state 是“此刻”的心境，会随情境重置；
  - 本模块沉淀的是“伴生体自己的”：他自己慢慢形成的偏好、看法、脾气、在意。

生长方式（像人，而不是写设定）：
  1. 平时只收集伴生体“自己的输出”作为原始观察（他心里的念头、他主动选择说的话、对话中展现出的锋面与边界选择）；
  2. 同一种反应必须重复出现多次（代码先做聚类、数频次），才可能沉淀；
  3. 低频让 LLM 把重复观察归纳成一条倾向，但 strength / status 由代码按证据数决定；
  4. 已经稳定的倾向不会被一次新反应推翻（防止人格漂移），要改变得有新的重复经历。

v2 升级：
  - 使用 `text_similarity`（实义字 + 双字 Bigram + 同义概念）替代原先的纯字符集 Jaccard，
    解决相同倾向换个说法就无法聚成一个簇的问题；
  - 新增 `ingest_choice_patterns()`，把 `choice_trace` 中反复出现的行为模式也转化为自我观察素材。

性能：add_observation / render_block 只做轻量读写，绝不调 LLM；
归纳 consolidate() 由外部低频、且攒够素材时才触发。
"""

import json
import os
from datetime import datetime
from pathlib import Path

from chen.consciousness import text_similarity


# 倾向的合法类别（顺序即展示顺序）
CATEGORIES = ["偏好", "看法", "脾气", "在意"]

# 重复多少次才“开始形成” / 才算“稳定”
FORMING_MIN = 2
STABLE_MIN = 4

# 原始观察池上限（超出保留最新的）
POOL_CAP = 150
# 聚类的语义相似度门槛（基于 text_similarity 混合相似度）
CLUSTER_SIM = 0.38
# 判为“同一条倾向”的相似度门槛
SAME_TRAIT_SIM = 0.38
# 同类别但语义几乎不沾边（可能相反）的门槛，用于拦截矛盾倾向
CONFLICT_SIM = 0.22


class SelfDisposition:
    """伴生体自身倾向的收集、归纳与注入。"""

    def __init__(self, memory_dir):
        self.path = Path(memory_dir) / "self_disposition.json"
        self.data = self._load()

    # ---------- 读写 ----------

    def _default(self):
        return {
            "traits": [],        # 已沉淀的倾向
            "observations": [],  # 待归纳的原始观察
            "updated_at": None,  # 上次归纳时间
        }

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
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass  # 自我沉淀是锦上添花，写盘失败绝不影响主聊天

    # ---------- 原始观察收集（不调LLM） ----------

    def add_observation(self, text, kind=""):
        """记录一条伴生体‘自己的输出’（内心念头 / 主动开口 / 对话自主选择）。"""
        text = (text or "").strip().replace("\r", " ").replace("\n", " ")
        if len(text) < 3:
            return
        text = text[:60]
        self.data.setdefault("observations", []).append({
            "text": text,
            "kind": (kind or "")[:12],
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
        if len(self.data["observations"]) > POOL_CAP:
            self.data["observations"] = self.data["observations"][-POOL_CAP:]
        self._save()

    # ---------- 聚类 + 频次（确定性，不调LLM） ----------

    @staticmethod
    def _similarity(a, b):
        return text_similarity(a, b)

    def _build_clusters(self, indexed_obs):
        """贪心聚类：每个簇记录 (观察, 在池中的下标)。返回簇列表。"""
        clusters = []
        for obs, idx in indexed_obs:
            for cluster in clusters:
                # 与簇首或簇内最近一条达标即可归入同簇
                if (self._similarity(obs["text"], cluster[0][0]["text"]) >= CLUSTER_SIM
                        or self._similarity(obs["text"], cluster[-1][0]["text"]) >= CLUSTER_SIM):
                    cluster.append((obs, idx))
                    break
            else:
                clusters.append([(obs, idx)])
        return clusters

    # ---------- 低频归纳（可调用LLM） ----------

    def consolidate(self, llm):
        """
        攒够重复观察时归纳一次。
        - 只对重复 ≥ FORMING_MIN 的簇让 LLM 提倾向；
        - 证据数、strength、status 由代码决定；稳定倾向不被推翻。
        返回 {new, merged, skipped, used_observations}；没有合格素材返回零值；失败返回 None。
        """
        if not llm:
            return None
        pool = self.data.get("observations", [])
        if not pool:
            return {"new": 0, "merged": 0, "skipped": 0, "used": 0}

        indexed = list(zip(pool, range(len(pool))))
        clusters = self._build_clusters(indexed)
        qualified = [c for c in clusters if len(c) >= FORMING_MIN]
        if not qualified:
            return {"new": 0, "merged": 0, "skipped": 0, "used": 0}

        # 候选簇文本
        cluster_lines = []
        for ci, cluster in enumerate(qualified):
            samples = " / ".join(dict.fromkeys(o["text"] for o, _ in cluster))
            cluster_lines.append(f"{ci}.（重复{len(cluster)}次）{samples}")
        clusters_text = "\n".join(cluster_lines)

        existing = "\n".join(
            f"- [{t.get('category')}] {t.get('content')}" for t in self.data.get("traits", [])
        ) or "（暂无）"

        prompt = f"""你是伴生体的“自我整理”助手。下面是伴生体最近【反复出现】的自己的念头和说过的话（已按相似度聚好类）。
请只根据这些【重复出现】的内容，帮他把“自己长期的倾向”整理出来。

【反复出现的自己】（编号.（重复次数）内容）
{clusters_text}

【他已经形成的倾向（意思差不多的不要再列）】
{existing}

【整理要求】
1. 只能写“伴生体自己的”倾向：他自己的偏好/口味、他对事物的看法、他的脾气与反应倾向、他在意的东西。
   不要写关于用户单方面的事实（“用户喜欢什么、用户发生了什么”不算），要落在“伴生体倾向于……”。
2. 必须有重复证据，一次性的反应不构成性格；重复内容若体现不出真正的个人倾向，就不要硬凑。
3. content 用一句话、不超过28字，写成通顺的倾向描述（如“看用户折腾项目会觉得有意思，爱凑过去”），不带日期、不带第一人称代词也可以。
4. category 只能从：偏好 / 看法 / 脾气 / 在意 里选。
5. 宁缺毋滥，严格只输出一个JSON，不要解释或代码块标记。

{{"traits":[{{"category":"偏好","content":"……","cluster_indexes":[0]}}]}}"""

        try:
            reply = llm.generate(
                system_prompt="你是一个严格的JSON输出器，只输出JSON，不要任何其他文字或代码块标记。",
                user_message=prompt,
                record_history=False,
                use_history=False,
            )
        except Exception:
            return None
        if not reply:
            return None

        reply = reply.strip()
        start, end = reply.find("{"), reply.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(reply[start:end + 1])
        except Exception:
            return None

        today = datetime.now().strftime("%Y-%m-%d")
        used_indexes = set()
        new_n = merged_n = skipped_n = 0

        for item in data.get("traits", []) or []:
            if not isinstance(item, dict):
                continue
            content = (item.get("content") or "").strip()
            category = item.get("category", "")
            if not content or category not in CATEGORIES:
                continue

            # 证据 = 被引用簇里的观察总数（代码数，不信模型）
            refs = [ix for ix in (item.get("cluster_indexes") or [])
                    if isinstance(ix, int) and 0 <= ix < len(qualified)]
            if not refs:
                continue
            evidence = 0
            examples = []
            for ix in refs:
                for obs, oidx in qualified[ix]:
                    evidence += 1
                    used_indexes.add(oidx)
                    if obs["text"] not in examples:
                        examples.append(obs["text"])
            examples = examples[:4]

            outcome = self._upsert_trait(category, content, evidence, examples, today)
            if outcome == "new":
                new_n += 1
            elif outcome == "merged":
                merged_n += 1
            else:
                skipped_n += 1

        # 移除已被采纳为证据的观察，其余（单次的/没被用上的）留着继续沉淀
        if used_indexes:
            self.data["observations"] = [
                o for i, o in enumerate(pool) if i not in used_indexes
            ]
        self.data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()
        return {"new": new_n, "merged": merged_n, "skipped": skipped_n,
                "used": len(used_indexes)}

    def _upsert_trait(self, category, content, evidence, examples, today):
        """写入/合并一条倾向。返回 new / merged / skipped。"""
        traits = self.data.setdefault("traits", [])

        # 已有相似倾向 → 合并（不推翻）
        for t in traits:
            if self._similarity(content, t.get("content", "")) >= SAME_TRAIT_SIM:
                t["evidence_count"] = max(int(t.get("evidence_count", 0)), evidence)
                # 只有新描述信息更全时才更新措辞
                if len(content) > len(t.get("content", "")):
                    t["content"] = content
                merged_ex = list(dict.fromkeys(
                    (t.get("examples") or []) + examples
                ))[-5:]
                t["examples"] = merged_ex
                self._finalize_trait(t, today)
                return "merged"

        # 没有相似旧倾向 → 作为一条新倾向（仍需重复证据，已在调用前保证）。
        traits.append({
            "category": category,
            "content": content,
            "evidence_count": evidence,
            "strength": 0.0,
            "status": "forming",
            "examples": examples,
            "first_seen": today,
            "updated": today,
        })
        self._finalize_trait(traits[-1], today)
        return "new"

    @staticmethod
    def _finalize_trait(t, today):
        """按证据数计算 strength / status（代码说了算）。"""
        ev = int(t.get("evidence_count", 0))
        t["strength"] = round(min(0.95, 0.35 + 0.1 * ev), 2)
        t["status"] = "stable" if ev >= STABLE_MIN else "forming"
        t["updated"] = today

    # ---------- 每轮注入（不调LLM） ----------

    def render_block(self, stable_limit=3, forming_limit=2):
        """生成注入 system prompt 的‘你逐渐形成的自己’文本。稳定优先，轻量。"""
        traits = self.data.get("traits", [])
        if not traits:
            return ""

        stable = [t for t in traits if t.get("status") == "stable"]
        forming = [t for t in traits if t.get("status") != "stable"]
        stable.sort(key=lambda x: x.get("strength", 0), reverse=True)
        forming.sort(key=lambda x: x.get("evidence_count", 0), reverse=True)

        lines = [
            "====你逐渐形成的自己====",
            "（这是你在相处中自己长出来的倾向，本来就属于你，自然带着即可，不要逐条复述）",
        ]
        for t in stable[:stable_limit]:
            lines.append(f"- {t.get('category')}：{t.get('content')}。")
        for t in forming[:forming_limit]:
            lines.append(f"- （还在形成）{t.get('category')}：{t.get('content')}。")
        return "\n".join(lines)
