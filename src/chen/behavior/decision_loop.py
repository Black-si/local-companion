"""伴生体后台决策心跳编排。"""

import random
import time
from datetime import datetime, timedelta

from chen.consciousness.decision import DecisionType
from chen.consciousness import auto_sync_on_prompt_build


class DecisionLoop:
    """把感知、生活、情绪与决策引擎串成低频行为循环。"""

    def __init__(self, chen):
        self.chen = chen

    def run(self):
        """后台线程：定期询问决策层是否需要主动开口"""
        import time
        while not self.chen._stopped:
            # 每60秒检查一次
            for _ in range(60):
                if self.chen._stopped:
                    return
                time.sleep(1)

            if not self.chen.decision:
                continue

            # -1. 自动消化已完成的前台聊天（确保即使没有发起下一轮对话，刚聊完的那一轮也能在60秒内反哺情绪/工作区/线索）
            try:
                auto_sync_on_prompt_build(self.chen, "")
            except Exception:
                pass

            # 0. 记忆的每周深度整理（满7天后台归纳一次，不阻塞决策）
            self.chen._maybe_daily_maintain()
            self.chen._maybe_weekly_consolidate()
            # 每晚自动反思一次（今天还没反思、模型就绪时）
            self.chen._maybe_daily_reflection()

            # 1. 从感知层取出新事件（可能为空——时间关怀不依赖事件）
            events = self.chen.perception.pop_events() if self.chen.perception else []

            # 2. 用户现在是否在电脑前、距上次聊天多久（供决策层判断）
            presence = "ACTIVE"
            if self.chen.perception:
                presence, _ = self.chen.perception.get_presence_status()
            gap_minutes = self.chen._get_last_chat_gap_minutes()

            # 2.5 实时采集伴生体此刻心境（纯确定性计算，不调模型）和用户在用的软件，
            #      让决策层知道他“想不想开口”，而不只是被事件触发
            dec_app, dec_detail, dec_away = "", "", 0
            dec_dur = 0
            if self.chen.perception:
                if presence == "AWAY":
                    _, dec_away = self.chen.perception.get_presence_status()
                else:
                    dec_app, dec_dur, dec_detail = self.chen.perception.get_current_app_status()
            dec_mood, dec_note = "无特别想法", ""
            try:
                _snap = self.chen.self_state.gather(
                    presence=presence, away_sec=dec_away or 0,
                    app=dec_app or "", detail=dec_detail or "",
                    context=self.chen.context.data.get("context", "日常"),
                    topic=self.chen.context.data.get("topic", ""),
                    gap_minutes=gap_minutes,
                    memories=getattr(self.chen.memory, "memories", []) if self.chen.memory else [],
                )
                dec_mood = _snap["mood"]
                # 心境小念头只在标签与落盘值一致时才用，避免拿过期念头当素材
                if self.chen.self_state.data.get("mood") == dec_mood:
                    dec_note = self.chen.self_state.data.get("mood_note", "")
            except Exception as e:
                print(f"心境采集异常：{e}")

            # 3. 构造当前简要状态
            current_state = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M"),
            }

            # 3.1 检查压着的开口意图：到点了先看他方不方便，合适才说
            try:
                self.chen._tick_pending_intents(presence, dec_app)
            except Exception as _e:
                print(f"延迟意图异常：{_e}")

            # 3.5 推进小世界内部活动（桌面世界不做事）。正常路径由 WorldMind 选意图。
            _world_intent = None
            try:
                _world_mind = getattr(self.chen, "world_mind", None)
                if _world_mind and self.chen.world.is_in_small_world():
                    _dom, _dv = self.chen.emotion.dominant() if self.chen.emotion else ("", 0)
                    _world_intent = _world_mind.decide(
                        world_state=self.chen.world.get_state(),
                        life_state=getattr(self.chen.life, "state", {}) or {},
                        mood=dec_mood, emotion=_dom or "", emotion_value=_dv or 0,
                    )
            except Exception as _e:
                print(f"小世界意图决策异常：{_e}")
            self.chen.world.tick(mood=dec_mood, activity_intent=_world_intent)
            # 只在活动标签变化时发一次语义目标；MV 自己负责寻路和执行。
            try:
                bridge = getattr(self.chen, "world_bridge", None)
                if bridge:
                    bridge.sync_activity_intent(_world_intent)
            except Exception as _e:
                print(f"小世界行动同步异常：{_e}")
            # 3.51 拍照检查：伴生体在室外+冷却过了+概率触发，后台线程拍照
            try:
                self.chen._maybe_take_photo(dec_mood)
            except Exception as _e:
                print(f"拍照检查异常：{_e}")
            # 3.55 生活节律：作息/精力/桌面日常（跨世界，该睡会从小世界回家睡）
            try:
                _life_changes = self.chen.life.tick(mood=dec_mood, presence=presence)
                if _life_changes.get("went_sleep"):
                    print("🌙 伴生体去睡了")
                if _life_changes.get("woke_up"):
                    print("☀️ 伴生体醒了")
                if _life_changes.get("went_home"):
                    print("🏠 该睡了，伴生体从小世界回了家")
            except Exception as _e:
                print(f"生活节律异常：{_e}")
            # 3.56 情绪向量：按真实时间自然衰减，再从生活变化和感知事件里产生波动
            #      （烦闷散得快、想念退得慢；关机几天回来，情绪也该淡了）
            try:
                self.chen.emotion.decay_to()
                if _life_changes.get("woke_up"):
                    self.chen.emotion.add("倦", 0.4, source="刚醒")
                if _life_changes.get("went_sleep"):
                    self.chen.emotion.add("倦", -0.5, source="去睡")
                    self.chen.emotion.add("安心", 0.2, source="到家睡觉")
                for _ev in events:
                    _et = _ev.get("type", "")
                    if _et == "USER_BACK":
                        self.chen.emotion.add("想念", -0.5, source="用户回来了")
                        self.chen.emotion.add("开心", 0.3, source="用户回来了")
                    elif _et == "USER_AWAY":
                        self.chen.emotion.add("想念", 0.15, source="用户走开了")
                self.chen.emotion.save()
            except Exception as _e:
                print(f"情绪结算异常：{_e}")
            # 情绪反哺心境标签：烦闷/委屈占了上风时，他此刻就是想安静
            try:
                _dom, _dv = self.chen.emotion.dominant()
                if _dom in ("烦闷", "委屈") and _dv >= 0.5 and dec_mood != "想安静":
                    dec_mood = "想安静"
                    if not dec_note:
                        dec_note = self.chen.emotion.describe_for_prompt()
            except Exception:
                pass
            # 3.57 Environment：聚合此刻整个可感知世界，落盘供虚拟桌面 V1 / 未来 RSS 层
            try:
                _env_snapshot = self.chen.environment.snapshot(
                    presence=presence, app=dec_app or "",
                    detail=dec_detail or "", duration_min=dec_dur or 0)
                # 环境层只把明确标为 decision_candidate 的事件交给决策层；
                # notice/trace 继续只留痕，避免把每条观察都变成主动打扰。
                events = self.chen._merge_environment_decision_events(
                    events, _env_snapshot.get("events") or [])
                # 环境变化先成为当天经历；是否主动开口仍由 decision.py 的显著度/冷却规则决定。
                for _env_event in (_env_snapshot.get("events") or []):
                    _kind = _env_event.get("kind", "environment")
                    _summary = _env_event.get("summary", "")
                    if _summary:
                        self.chen.day_log.log(
                            _kind, f"环境变化：{_summary}",
                            world=self.chen.world.get_current_world(),
                            extra={"type": _env_event.get("type", ""),
                                   "attention": _env_event.get("attention", "trace"),
                                   "attention_reason": _env_event.get("attention_reason", ""),
                                   "data": _env_event.get("data", {})})
                self.chen.get_presence_snapshot(persist=True)
            except Exception as _e:
                print(f"环境快照异常：{_e}")

            # 3.575 自主活动：只有独处且冷却到期时，才从受限活动池选择一个低风险
            # 只读工具；一轮最多执行一个。工具结果写进伴生体自己的经历日志，
            # 不把模型直接暴露给整张工具列表。
            try:
                autonomy = getattr(self.chen, "autonomy", None)
                if autonomy:
                    from chen.behavior.autonomy import ActivityContext
                    _life_state = getattr(self.chen.life, "state", {}) or {}
                    _auto_context = ActivityContext(
                        presence=presence,
                        gap_minutes=float(gap_minutes or 0),
                        mood=dec_mood,
                        energy=float(_life_state.get("energy", 100) or 0),
                        awake=bool(_life_state.get("awake", True)),
                    )
                    _auto_decision, _auto_result, _auto_record = autonomy.run_once(_auto_context)
                    if _auto_record:
                        print(f"[自主活动] {_auto_record.summary}")
            except Exception as _e:
                print(f"自主活动异常：{_e}")

            # 低频意识整合放在情绪结算和环境事件合并之后，让“此刻念头”看到最新状态。
            # 每分钟心跳只检查到期，不会因此每次调用模型。
            try:
                _cw_memories = []
                if self.chen.memory:
                    _cw_memories = [m.get("fact", "") for m in self.chen.memory.retrieve(
                        dec_app or self.chen.context.data.get("topic", ""), top_k=5)]
                _cw_threads = [
                    t for t in self.chen.personal_threads.data.get("threads", [])
                    if t.get("status") == "open"
                ]
                _cw_disposition = (
                    self.chen.disposition.data.get("traits", [])
                    + self.chen.disposition.data.get("observations", [])[-4:]
                )
                _cw_signals = self.chen.conscious_workspace.collect_signals(
                    mood=dec_mood,
                    emotion=self.chen.emotion.dominant()[0] if self.chen.emotion else "",
                    emotion_value=self.chen.emotion.dominant()[1] if self.chen.emotion else 0,
                    presence=presence, app=dec_app, detail=dec_detail,
                    gap_minutes=gap_minutes, events=events,
                    world_state=self.chen.world.get_state(),
                    open_threads=_cw_threads,
                    recent_choices=self.chen.choice_trace.data.get("traces", [])[-5:],
                    memories=_cw_memories, disposition=_cw_disposition,
                )
                _cw_snap = self.chen.conscious_workspace.maybe_integrate(
                    self.chen.llm_api, _cw_signals)
                if getattr(self.chen, "personal_threads", None) and hasattr(self.chen.personal_threads, "sync_from_workspace"):
                    self.chen.personal_threads.sync_from_workspace(_cw_snap)
            except Exception as _e:
                print(f"意识工作区整合异常（不影响使用）：{_e}")

            # 决策前快照必须在意识整合之后构造，才能拿到最新候选念头。
            decision_snapshot = self.chen._build_decision_snapshot(dec_mood)
            # 3.575 感知层 → 记忆池：每隔 45 分钟把"用户今天在电脑上做什么"存一条活动记忆。
            try:
                _now = datetime.now()
                _last_am = getattr(self.chen, "_last_activity_memory_at", None)
                if self.chen.memory and self.chen.perception and (_last_am is None
                        or (_now - _last_am).total_seconds() >= 45 * 60):
                    _sum_text, _sum_counts = self.chen.perception.get_daily_summary()
                    if _sum_counts and len(_sum_counts) >= 2:
                        self.chen.memory.save(
                            f"用户今天在电脑上：{_sum_text[:60]}",
                            importance=0.4, source="activity")
                        self.chen._last_activity_memory_at = _now
            except Exception as _e:
                print(f"活动记忆异常：{_e}")
            # 3.554 遗忘曲线：睡前把记忆衰减一轮（纯本地数学，毫秒级；每天一次）
            try:
                _decay = self.chen.memory.decay_and_forget(now=datetime.now())
                if _decay:
                    print(f"🧠 遗忘曲线：衰减{_decay['decayed']}条、归档{_decay['archived']}条、清除{_decay['purged']}条")
            except Exception as _e:
                print(f"遗忘衰减异常：{_e}")
            # 3.555 睡前消化这一天：23点后把今天的经历酿成念头/日记
            try:
                _before_ids = {it.get("id") for it in self.chen.world_store.all_items()}
                _digested = self.chen.day_digest.maybe_run(
                    now=datetime.now(), day_log=self.chen.day_log,
                    world_store=self.chen.world_store, llm=self.chen.llm_api)
                if _digested:
                    print(f"[一日沉淀] 他把这一天酿成了 {_digested} 条念头/日记")
                    for it in self.chen.world_store.all_items():
                        if (it.get("id") not in _before_ids
                                and it.get("type") == "念头"
                                and it.get("status") != "deleted"):
                            try:
                                self.chen.expression.add(
                                    it.get("text", ""),
                                    reason=f"一天的沉淀（{it.get('date','')}）",
                                    trigger="", priority="normal")
                            except Exception:
                                pass
                            try:
                                if getattr(self.chen, "personal_threads", None):
                                    self.chen.personal_threads.observe(
                                        it.get("text", ""),
                                        source="day_digest",
                                        evidence_id=f"digest_{it.get('id')}",
                                    )
                            except Exception:
                                pass
            except Exception as _e:
                print(f"一日沉淀异常：{_e}")
            # 3.556 REM 做梦：日消化之后，他从自己的记忆里抽碎片做梦，
            #      梦本身散了，从梦里挖出的洞见留下（念头+挂起，一晚最多2次 API）
            try:
                _dream = self.chen.dream.maybe_run(
                    now=datetime.now(), memory=self.chen.memory,
                    world_store=self.chen.world_store, expression=self.chen.expression,
                    llm=self.chen.llm_api, day_log=self.chen.day_log)
                if _dream:
                    print(f"🌙 他做了个梦：{_dream['dream'][:40]}…"
                          + (f" 带回 {len(_dream['insights'])} 个念头" if _dream["insights"] else ""))
                    if getattr(self.chen, "personal_threads", None):
                        for _ins in (_dream.get("insights") or []):
                            self.chen.personal_threads.observe(
                                _ins, source="dream", evidence_id=f"dream_{datetime.now().strftime('%Y-%m-%d')}")
            except Exception as _e:
                print(f"做梦异常：{_e}")
            # 3.558 互联网感知 V1：白名单 RSS → 本地兴趣筛选 → 感知事件
            try:
                _web_events = self.chen.internet.maybe_run(now=datetime.now())
                for _ev in (_web_events or []):
                    self.chen.day_log.log("web", f"网上看到：{_ev['title']}",
                                     extra={"link": _ev.get("link", ""),
                                            "source": _ev.get("source", "")})
                    print(f"[互联网感知] 他注意到：{_ev['title']}")
            except Exception as _e:
                print(f"互联网感知异常：{_e}")
            # 记录当前活动（用于爱好统计）
            try:
                _ws = self.chen.world.get_state()
                _act = _ws.get("activity", "")
                if _act:
                    self.chen._record_activity(_act)
            except Exception:
                pass
            # 3.6 生命引擎：独处够久或用户走开了，他可能在自己的世界里留下一条东西（走API）
            try:
                _wctx = self.chen.world.get_state()
                _known = (f"最近天气{_wctx.get('weather') or '未知'}，"
                          f"{_wctx.get('last_visit_summary') or '一直待在桌面'}")
            except Exception:
                _known = ""
            try:
                _new_item = self.chen.world_engine.solo_tick(
                    last_action_minutes=gap_minutes,
                    walked_away=(presence == "AWAY"),
                    known_context=_known,
                    llm=self.chen.llm_api,
                )
                if _new_item:
                    print(f"[世界生长] 他在自己的世界里留下了东西")
            except Exception as _e:
                print(f"世界生长异常：{_e}")

            # 4. 让决策引擎研判（强感知事件→时间关怀→打开软件结合记忆→心境自由发话）
            _world_state = self.chen.world.get_state()
            try:
                _ls = self.chen.life.state
                _world_state["life"] = {
                    "awake": _ls.get("awake"),
                    "energy": int(_ls.get("energy", 0)),
                    "activity": _ls.get("activity", ""),
                    "recent": [e.get("text", "") for e in _ls.get("recent_events", [])[-4:]],
                }
            except Exception:
                pass
            decision = self.chen.decision.decide(
                events=events,
                current_state=current_state,
                llm=self.chen.llm_api,
                context=self.chen.context,
                presence=presence,
                last_chat_gap_minutes=gap_minutes,
                memory=self.chen.memory,
                mood=dec_mood,
                mood_note=dec_note,
                app=dec_app or "",
                detail=dec_detail or "",
                expression=self.chen.expression,
                choice_trace=self.chen.choice_trace,
                world_state=_world_state,
                decision_context=decision_snapshot,
            )

            # 观测台只记录决策摘要，不保存完整消息或聊天上下文。
            try:
                _decision_kind = getattr(decision.type, "value", decision.type)
                _decision_source = "consciousness" if "CONSCIOUS" in (decision.reason or "") else ""
                self.chen.conscious_workspace.observe_decision(
                    decision_type=_decision_kind,
                    reason=decision.reason or "",
                    source=_decision_source,
                    message_available=bool(decision.message),
                )
            except Exception:
                pass

            # 5. 如果决定开口：显著度分级
            if decision.type == DecisionType.SPEAK and decision.message:
                processed = self.chen._post_process_reply(decision.message)
                _salience = self.chen._salience_of(decision.reason or "")
                if _salience == "strong":
                    self.chen._deliver_proactive(processed, decision.reason or "")
                    try:
                        self.chen.conscious_workspace.observe_decision(
                            decision_type="speak", reason=decision.reason or "",
                            source="consciousness" if "CONSCIOUS" in (decision.reason or "") else "",
                            delivered=True, message_available=True)
                    except Exception:
                        pass
                else:
                    _delay = random.randint(60, 120) if _salience == "normal" \
                        else random.randint(300, 600)
                    if len(self.chen._pending_intents) < 3:
                        self.chen._pending_intents.append({
                            "text": processed,
                            "reason": decision.reason or "",
                            "salience": _salience,
                            "deliver_at": datetime.now() + timedelta(seconds=_delay),
                        })
                        try:
                            self.chen.conscious_workspace.observe_decision(
                                decision_type="speak", reason=decision.reason or "",
                                source="consciousness" if "CONSCIOUS" in (decision.reason or "") else "",
                                pending=True, message_available=True)
                        except Exception:
                            pass

            # 5.5 世界切换：进入/离开小世界
            elif decision.type == DecisionType.ENTER_WORLD:
                self.chen.world.enter_small_world()
                print(f"\n[决策触发] 伴生体进入小世界（{decision.reason}）")
                try:
                    self.chen.day_log.log("world", "进入小世界",
                                     extra={"reason": decision.reason or ""})
                except Exception:
                    pass
                try:
                    self.chen.choice_trace.record(
                        trigger="世界·进入小世界", inclination=decision.reason or "",
                        feeling=dec_mood, concern="", choice="enter_world",
                        expression="进入小世界待一会儿")
                except Exception:
                    pass
            elif decision.type in (DecisionType.LEAVE_WORLD, DecisionType.GO_TO_DESKTOP):
                _go_to_desktop = decision.type == DecisionType.GO_TO_DESKTOP
                _summary = self.chen.world.get_state().get("last_visit_summary", "")
                self.chen.world.leave_small_world()
                if _go_to_desktop:
                    # 只有意识明确选择“去用户的桌面”才发出一次性桌宠意图。
                    # LEAVE_WORLD、作息回家和手动离开只改变世界状态。
                    try:
                        self.chen.request_desktop_pet(
                            decision.reason or "想回桌面看看",
                            source="consciousness",
                        )
                    except Exception as _e:
                        print(f"[桌宠] 意识意图登记失败：{_e}")
                print(f"\n[决策触发] 伴生体{'想去桌面' if _go_to_desktop else '离开小世界'}（{decision.reason}）")
                try:
                    self.chen.day_log.log("world", "离开小世界", extra={
                        "reason": decision.reason or "", "summary": _summary,
                        "desktop_pet_intent": _go_to_desktop})
                except Exception:
                    pass
                try:
                    self.chen.choice_trace.record(
                        trigger="世界·去桌面" if _go_to_desktop else "世界·离开小世界",
                        inclination=_summary,
                        feeling="", concern="", choice="go_to_desktop" if _go_to_desktop else "leave_world",
                        expression=("回到用户的桌面" if _go_to_desktop else (_summary or "从小世界回来")))
                except Exception:
                    pass
                # 小世界→桌面：把这次小世界经历留下一条痕迹到他的生活里
                try:
                    _visit_item = self.chen.world.generate_visit_item()
                    if _visit_item and getattr(self.chen, "world_store", None):
                        _vtype, _template_text = _visit_item
                        _final_text = self.chen._generate_visit_text_with_api(_vtype, _template_text)
                        self.chen.world_store.add(
                            text=_final_text, type=_vtype, privacy="公开",
                            reason="从小世界回来", source="small_world"
                        )
                        print(f"[小世界→桌面] 留下一条{_vtype}：{_final_text}")
                except Exception as _e:
                    print(f"[小世界→桌面] 留痕迹失败：{_e}")

            # 6. 低频更新伴生体的自我状态（约15分钟一次，含一句LLM心境；首次启动后尽快一次）
            self.chen._state_tick += 1
            try:
                need_state_update = self.chen._state_tick >= 15 or not self.chen.self_state.data.get("updated_at")
                if not need_state_update:
                    try:
                        ua = self.chen.self_state.data.get("updated_at")
                        if ua:
                            last_upd = datetime.strptime(ua, "%Y-%m-%d %H:%M")
                            if (datetime.now() - last_upd).total_seconds() > 2 * 3600:
                                need_state_update = True
                    except Exception:
                        pass
                if need_state_update:
                    self.chen._state_tick = 0
                    self.chen._update_self_state(presence, gap_minutes)
            except Exception as e:
                print(f"自我状态更新异常：{e}")

            # 表达层：清理过期的挂起念头
            try:
                self.chen.expression.prune()
            except Exception:
                pass
            # 自我沉淀：攒够原始观察（≥12条）就归纳一次，让倾向随经历生长
            self.chen._maybe_disposition_consolidate()
            # 个人线索只从现有文件确定性整理，不增加聊天轮次的模型调用
            try:
                self.chen._update_personal_threads()
            except Exception as _e:
                print(f"个人线索整理异常（不影响使用）：{_e}")
