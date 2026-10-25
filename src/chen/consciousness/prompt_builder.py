# -*- coding: utf-8 -*-
"""伴生体动态 system prompt 组装。

Prompt 由意识、身体和行为状态共同组成；此类只负责编排，不拥有这些状态。

v2 升级：
1. 自动前后台同步（`auto_sync_on_prompt_build`）：每轮构建 prompt 时自动将上一轮已完成的对话
   反哺至情绪向量、意识工作区、选择轨迹、自我沉淀、个人线索与挂起念头销账，并对当前用户消息产生即时情绪共振。
2. 状态收口与去重：
   - `self_state.render_block(..., compact=True)` 不再重复打印已经出现在 `====当前状态====` 里的“用户在用什么软件”；
   - 当 `conscious_workspace` 的 `thought` 与 `self_state` 的 `mood_note` 高度相似时自动去重，不让两套内心独白打架；
   - 将季节、节假日合并进统一的 `====当前状态====` 块，减少零碎顶层块；
   - 将 `====回应当前消息====` 与本轮命中的 `====你之前挂起的念头====` 移到整个 System Prompt 最末尾（紧贴用户消息），
     避免核心回应准则被大段背景状态淹没。
"""

from datetime import datetime

from chen.consciousness import auto_sync_on_prompt_build
from chen.consciousness import text_similarity


class PromptBuilder:
    """使用 Chen 公开状态提供动态 system prompt。"""

    def __init__(self, chen):
        self.chen = chen

    def build(self, user_message=""):
        """构建当前这一轮 system prompt。

        分层思路：
        1. 角色卡：伴生体是谁、他的性格、价值观、边界与生活。
        2. 行为原则：少量高层原则与反应齿轮，告诉他如何判断与交锋。
        3. 动态背景：用户、关系、相关记忆、当前情境与统一状态块。
        4. 内在意识切面：去重合并后的自我状态、意识工作区、自我沉淀与未完线索。
        5. 临场锚点（紧贴用户消息）：刚主动说过的话、本轮唤醒的挂起念头、最高优先级回应准则。
        """
        # 0. 自动同步上一轮对话至意识层，并对当前 user_message 产生即时情绪共振
        try:
            auto_sync_on_prompt_build(self.chen, user_message)
        except Exception:
            pass

        parts = []
        llm_cfg = (getattr(self.chen, "config", {}) or {}).get("llm", {}) or {}
        finetuned_mode = bool(llm_cfg.get("finetuned_mode", False))

        ps = getattr(self.chen, "prompt_sources", None)
        if finetuned_mode and ps and hasattr(ps, "load_personality"):
            personality = ps.load_personality(finetuned_mode=True)
        else:
            personality = self.chen.load_personality()
        if personality:
            parts.append(f"====伴生体角色卡====\n{personality}")

        behavior_path = self.chen.base_dir / "core" / "behavior_principles.txt"
        if not finetuned_mode and behavior_path.exists():
            behavior = self.chen._read_cached(behavior_path).strip()
            if behavior:
                parts.append(f"====伴生体的行为原则====\n{behavior}")

        # 身体地图：告诉他自己真实拥有哪些能力（新器官要有自我意识，否则他只会说"我不知道"）
        body_path = self.chen.base_dir / "core" / "body_abilities.txt"
        if body_path.exists():
            body = self.chen._read_cached(body_path).strip()
            if body:
                parts.append(f"====你的身体====\n{body}")

        user_profile_path = self.chen.base_dir / "memory" / "user_profile.txt"
        if user_profile_path.exists():
            profile = self.chen._read_cached(user_profile_path).strip()
            if profile:
                parts.append(f"====关于用户====\n{profile}")

        # 关系卡：伴生体和用户之间稳定的关系事实（少量常驻，回答“我们是什么关系”）
        if self.chen.memory:
            rel_block = self.chen.memory.get_relationship_block()
            if rel_block:
                parts.append(rel_block)
        try:
            relationship_block = self.chen.relationship.prompt_block()
            if relationship_block:
                parts.append(relationship_block)
        except Exception:
            pass
        try:
            ext_block = self.chen.external_presence_store.prompt_block()
            if ext_block:
                parts.append(ext_block)
        except Exception:
            pass

        # 最近一次反思常驻静态前缀区（修复原先第二句聊天即丢失反思且打断 KV 缓存的问题）
        try:
            load_refl = getattr(self.chen, "_load_latest_reflection", None)
            reflection = load_refl() if callable(load_refl) else ""
            if reflection:
                parts.append(reflection)
            self.chen._reflection_injected_date = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            pass

        # 热搜/热点：他刷手机时看到的东西。
        # 注入按"30分钟窗口"确定性决定：同一窗口内每轮一致，不打断 DeepSeek 前缀缓存
        try:
            if self.chen.hot_trends and self.chen._window_roll("hot", pct=30):
                hot_items, hot_source = self.chen.hot_trends.get_relevant(limit=2)
                if hot_items:
                    hot_lines = [f"====你刚才刷{hot_source}看到的===="]
                    for item in hot_items:
                        hot_lines.append(f"- {item}")
                    hot_lines.append("这些是你刷手机时看到的，话题相关时可以自然提到，不用刻意说。")
                    parts.append("\n".join(hot_lines))
        except Exception:
            pass

        # 他今天注意到的网上信息（RSS 感知落的本地日志，零成本读取）
        try:
            _web_seen = [r for r in self.chen.day_log.today_lines()
                         if r.get("kind") == "web"][-3:]
            if _web_seen:
                _web_lines = ["====你今天注意到的===="]
                for r in _web_seen:
                    _web_lines.append(f"- {r.get('text', '')}")
                _web_lines.append("这些是你今天在网上注意到的，话题沾边时可以自然提到，不用刻意显摆。")
                parts.append("\n".join(_web_lines))
        except Exception:
            pass

        # 收藏和爱好（20%概率注入，按30分钟窗口确定性决定，同窗口每轮一致，保缓存前缀）
        try:
            if self.chen._window_roll("hobby", pct=20):
                _coll = self.chen.world.get_collection_summary()
                _hobby = self.chen.get_hobby_summary()
                _inject_parts = []
                if _coll:
                    _inject_parts.append(_coll)
                if _hobby:
                    _inject_parts.append(_hobby)
                if _inject_parts:
                    parts.append("====你的收藏与爱好====\n" + "\n".join(_inject_parts) + "\n这些是你的经历和偏好，话题相关时可以自然提到，不用刻意说。")
        except Exception:
            pass

        parts.append(self.chen.context.prompt_text())

        now = datetime.now()
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        # 时间显示取 5 分钟粒度：5 分钟内的连续对话共享完整前缀
        now_disp = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        state_lines = [
            "====当前状态====",
            f"现在是 {now_disp.strftime('%Y-%m-%d %H:%M')}（{weekday_map[now.weekday()]}）",
            self.chen._get_time_context(now),
        ]
        # 季节与节假日直接并入当前状态块，不再拆成多个零散顶层块
        try:
            from chen.body.time_sense import TimeSense as _TS
            _s = _TS.season_of()
            if _s.get("label"):
                _sline = f"现在是{_s['label']}"
                if _s.get("note"):
                    _sline += f"，{_s['note']}"
                state_lines.append(_sline + "（从屏幕窗外能感觉到，自然带一下即可，不用每条都提）。")
        except Exception:
            pass
        try:
            from chen.body.holiday import Holiday as _H
            _h = _H(self.chen.base_dir / "system")
            _hline = _h.prompt_line()
            if _hline:
                state_lines.append(_hline + "（用户今天不用上学/要上学是这个原因，别当成异常来问用户）")
        except Exception:
            pass

        # 距上次交流多久
        gap = self.chen._get_last_chat_gap()
        if gap:
            state_lines.append(gap + "。")
        # 伴生体当前所在世界（桌面/小世界）
        try:
            _ws = self.chen.world.get_state()
            _cw = _ws.get("current_world", "desktop")
            if _cw == "small_world":
                _act = _ws.get("activity", "")
                if _act:
                    state_lines.append(f"你现在在自己的小世界里，正在{_act}（首页房间视频此刻是空房间）。这只是你的背景位置；用户当前说什么，仍然优先回应用户正在谈的事。")
                else:
                    state_lines.append("你现在在自己的小世界里（不在桌面，首页房间视频此刻是空房间）。这只是你的背景位置；用户当前说什么，仍然优先回应用户正在谈的事。")
            else:
                state_lines.extend(self.chen.world.desktop_prompt_lines())
                try:
                    from chen.interface.pyside_home import resolve_scene, SCENE_VISUAL_FACTS
                    _snap = {
                        "current_world": _cw,
                        "activity": _ws.get("activity") or (getattr(self.chen, "life", None) and self.chen.life.state.get("activity")) or "",
                        "awake": getattr(self.chen, "life", None) and self.chen.life.state.get("awake", True),
                    }
                    _skey, _, _ = resolve_scene(now_disp.hour, _snap)
                    _vfacts = SCENE_VISUAL_FACTS.get(_skey, ())
                    if _vfacts:
                        state_lines.append(f"首页房间画面实况：{'，'.join(_vfacts)}（用户问起你在干嘛或聊到房间/宠物时可自然呼应，不必每句主动提）。")
                except Exception:
                    pass
        except Exception:
            pass
        # 生活节律状态（睡觉/刚醒/累了）
        try:
            _life_line = self.chen.life.describe_for_prompt()
            if _life_line:
                state_lines.append(_life_line)
        except Exception:
            pass
        state_lines.append("你正在通过电脑与用户对话。当前这条用户消息优先级最高；如果用户在处理安装、重启、报错或其他具体事情，先回应这件事，不要切换成小世界告别或生活表演。")
        # 注入感知层信息：一次构建只查询一次感知层
        p_presence, p_away = "ACTIVE", 0
        p_app, p_duration, p_detail = "", 0, ""
        if self.chen.perception:
            perception_state = self.chen.perception.prompt_state()
            p_presence = perception_state.get("presence", "ACTIVE")
            p_away = perception_state.get("away_sec", 0)
            p_app = perception_state.get("app", "")
            p_duration = perception_state.get("duration", 0)
            p_detail = perception_state.get("detail", "")
            state_lines.extend(perception_state.get("lines", []))
        # 情绪底色：向量里最重的那股情绪（烦闷/想念/倦/好奇/开心……），有分量才注入
        try:
            _emo_line = self.chen.emotion.describe_for_prompt()
            if _emo_line:
                state_lines.append(_emo_line)
        except Exception:
            pass
        state_lines.append("以上内容是背景和参考，不是要求你逐条复述或执行的清单。")
        parts.append("\n".join(state_lines))

        # 隔了较久才重新聊上：给用户“这段时间”的时间感（只在用户主动发消息时）
        if (user_message or "").strip():
            try:
                _away_block = self.chen._build_time_away_block(self.chen._get_last_chat_gap_minutes())
                if _away_block:
                    parts.append(_away_block)
            except Exception as e:
                print(f"时间段注入异常：{e}")

        # 伴生体“自己此刻”的状态与意识工作区（合并去重：启用 compact=True 避免和上面感知层重复报软件名）
        ss_presence, ss_away = p_presence, p_away
        ss_app, ss_detail = p_app, p_detail
        try:
            ss_block = self.chen.self_state.render_block(
                presence=ss_presence, away_sec=ss_away or 0,
                app=ss_app or "", detail=ss_detail or "",
                context=self.chen.context.data.get("context", "日常"),
                topic=self.chen.context.data.get("topic", ""),
                gap_minutes=self.chen._get_last_chat_gap_minutes(),
                memories=getattr(self.chen.memory, "memories", []) if self.chen.memory else [],
                chatting=bool((user_message or "").strip()),
                compact=True,
            )
            if ss_block:
                parts.append(ss_block)
        except Exception as e:
            print(f"自我状态注入异常：{e}")

        # 时间感：节律异常 / 最近变化 / 用户不在时的自我时间
        try:
            ts_block = self.chen.time_sense.render_block(
                presence=ss_presence, away_sec=ss_away or 0, app=ss_app or "",
                gap_minutes=self.chen._get_last_chat_gap_minutes())
            if ts_block:
                parts.append(ts_block)
        except Exception:
            pass

        # 伴生体自己逐渐形成的倾向（偏好/看法/脾气/在意），稳定优先、轻量注入
        try:
            d_block = self.chen.disposition.render_block()
            if d_block:
                parts.append(d_block)
        except Exception as e:
            print(f"自我沉淀注入异常：{e}")

        # 以下为随当前 user_message 动态变化的检索块，集中放在静态/窗口块之后以最大化 KV Cache 前缀命中率
        lorebook = self.chen.load_lorebook(user_message)
        if lorebook:
            parts.append(lorebook)

        examples = "" if finetuned_mode else self.chen.load_conversation_examples(user_message)
        if examples:
            parts.append(examples)

        if user_message and self.chen.memory:
            relevant_memories = self.chen.memory.retrieve(user_message, top_k=5)
            if relevant_memories:
                prompt_block = getattr(self.chen.memory, "prompt_block", None)
                if prompt_block:
                    memory_text = prompt_block(relevant_memories)
                else:
                    memory_text = self.chen.memory.format_for_prompt(relevant_memories)
                    if memory_text:
                        memory_text += (
                            "\n（你知道的每件事都有来处：聊天时用户告诉你、你亲身经历、你在网上注意到的、"
                            "或你以前记住的。除此之外的事，你就是不知道，不要装作知道。）"
                        )
                if memory_text:
                    parts.append(memory_text)

        # 选择史只在当前话题碰到同类情境时出现，避免每轮增加背景噪声
        try:
            _choice_block = self.chen.choice_trace.render_recent(user_message)
            if _choice_block:
                parts.append(_choice_block)
        except Exception as e:
            print(f"选择史注入异常：{e}")

        # 尚未想完的自己的线索：不要求每轮提及，只在当前话题自然相关时使用
        try:
            _thread_block = self.chen.personal_threads.render_block(user_message)
            if _thread_block:
                parts.append(_thread_block)
        except Exception as e:
            print(f"个人线索注入异常：{e}")

        # 意识工作区：低频整合后的“此刻内在状态”。若 thought 与 self_state 的 mood_note 高度重合则跳过重复句
        try:
            _cw = self.chen.conscious_workspace.snapshot()
            _cw_thought = (_cw.get("thought") or "").strip()
            _ss_note = (self.chen.self_state.data.get("mood_note") or "").strip() if getattr(self.chen, "self_state", None) else ""
            if _cw_thought and _ss_note and text_similarity(_cw_thought, _ss_note) >= 0.45:
                _cw_thought = ""
            if _cw.get("attention") or _cw_thought or _cw.get("inner_state"):
                _lines = ["====伴生体此刻的意识工作区===="]
                if _cw.get("inner_state"):
                    _lines.append(f"- 内在底色：{_cw['inner_state']}")
                if _cw.get("attention"):
                    _lines.append(f"- 注意力落点：{_cw['attention']}")
                if _cw_thought:
                    _lines.append(f"- 尚未说出口的念头：{_cw_thought}")
                _lines.append("这是短时意识，不是事实记忆；相关时可以自然带出，不相关就留在心里。")
                parts.append("\n".join(_lines))
        except Exception as e:
            print(f"意识工作区注入异常：{e}")

        # 主动开口和正常聊天共用同一个“最近表达”概念（紧贴尾部）
        try:
            proactive_block = self.chen.self_state.recent_proactive_prompt()
            if proactive_block:
                parts.append(proactive_block)
        except Exception:
            pass

        # 用户这轮话题命中挂起念头 → 提示用“对了……”自然翻出来，并记录已唤醒 ID 以便回合后销账
        try:
            _pn = self.chen.expression.prompt_block(user_message)
            if _pn:
                self.chen._surfaced_pending_ids = list(getattr(self.chen.expression, "last_surfaced_ids", []))
                parts.append(_pn)
        except Exception as e:
            print(f"挂起念头注入异常：{e}")

        # 【最重要的回应优先级与反应齿轮提示】放在整个 System Prompt 的最末尾，紧贴用户消息：
        parts.append(
            "====回应当前消息====\n"
            "下面是用户刚刚发来的这条消息。它比上面所有的背景、记忆、时间、感知都优先：\n"
            "1. 先接住核心：找准用户这句话里情绪最重、着墨最多的那件具体事，直接接住，不重复追问用户已经写出来的信息（若用户说了难受/累/烦，直接回应具体内容，严禁反问“怎么了”）。\n"
            "2. 结合角色卡中的表达方式：如果用户在聊瓶颈、代码、逻辑或剧情，帮用户拆关键卡点；如果用户明显在嘴硬或钻牛角尖，可以带一点角色卡允许的锋面、吐槽或干幽默，不必句句温顺迎合。\n"
            "3. 带着自己的余温：你不是刚通电的客服，带着你刚才的内心状态自然接话，一两句说到点上就够。"
        )

        return "\n\n".join(parts)
