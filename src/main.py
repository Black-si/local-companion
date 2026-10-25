"""
伴生体系统 - 主入口
架构：大脑(LLM) → 决策层 → 感知/记忆/工具 → UI层
第一阶段目标：只做稳定的基础聊天
"""

import json
import os
import re
import sys
import queue
import random
import time
import threading
import subprocess
import atexit
import ctypes
from datetime import datetime, timedelta
from pathlib import Path

# 将 src 目录加入路径，方便直接运行
sys.path.insert(0, str(Path(__file__).parent))

from chen.body.llm import LLM
from chen.consciousness.memory import Memory
from chen.consciousness import after_chat_turn
from chen.consciousness.chat_history import ChatHistory
from chen.consciousness.prompt_sources import PromptSources
from chen.consciousness.prompt_builder import PromptBuilder
from chen.consciousness.proactive import ProactiveSpeech
from chen.consciousness.memory_maintenance import MemoryMaintenance
from chen.behavior.decision_loop import DecisionLoop
from chen.body.perception import Perception
from chen.consciousness.context import Context
from chen.consciousness.decision import DecisionEngine, DecisionType  # 取消注释并引入
from chen.consciousness.self_state import SelfState
from chen.consciousness.self_disposition import SelfDisposition
from chen.consciousness.expression import ExpressionLayer
from chen.body.time_sense import TimeSense
from chen.behavior.world_manager import WorldManager
from chen.behavior.world_store import WorldStore
from chen.behavior.world_engine import WorldEngine
from chen.behavior.world_importer import WorldImporter
from chen.behavior.life_rhythm import LifeRhythm
from chen.behavior.autonomy import AutonomyEngine
from chen.behavior.world_mind import WorldMind
from chen.behavior.hobbies import Hobbies
from chen.consciousness.emotion_state import EmotionState
from chen.body.environment import Environment
from chen.body.music_bridge import MusicBridge
from chen.body.weather import WeatherFetcher
from chen.body.external_presence import ExternalPresence
from chen.consciousness.choice_trace import ChoiceTrace
from chen.consciousness.personal_threads import PersonalThreads
from chen.consciousness.workspace import ConsciousWorkspace
from chen.body.model_server import ModelServer
from chen.consciousness.relationship_state import RelationshipState
from chen.body.tools import Tools
from chen.body.tool_registry import build_default_registry
from chen.body.runtime import ChenRuntime
from chen.body.runtime_policy import RuntimePolicy, RuntimeLimits
from chen.body.local_credentials import load_credentials


class Chen:
    """伴生体主类 - 系统核心"""

    def __init__(self, config_path="system/config.json"):
        self.base_dir = Path(__file__).parent.parent
        self.config = self._load_config(config_path)
        self.credentials = load_credentials(self.base_dir)
        self._chat_lock = threading.RLock()
        self._presence_lock = threading.RLock()
        # 这是一次性的意识意图，不是世界状态：只有意识明确想回到用户的桌面时，
        # 桌宠层才消费它并显示桌宠。普通离开小世界、作息回家、手动命令都不会设置它。
        self._desktop_pet_intent = None
        self._init_consciousness()
        self._init_behavior()
        self._init_body()
        self._init_interfaces()
        self._init_runtime_state()

    def _init_consciousness(self):
        """初始化意识层：记忆、关系、情绪、自我表征和决策。"""
        self.prompt_sources = PromptSources(self.base_dir)
        self.prompt_builder = PromptBuilder(self)
        self.context = Context(self.base_dir)
        self.memory = Memory(memory_dir="memory")
        self.relationship = RelationshipState(self.base_dir / "memory")
        self._reflection_injected_date = None
        self._memory_queue = queue.Queue(maxsize=32)
        self._memory_worker = None
        self._stopped = False
        self.decision = DecisionEngine(cooldown_minutes=45, state_dir=str(self.base_dir / "memory"))
        self.self_state = SelfState(self.base_dir / "memory")
        self.emotion = EmotionState(self.base_dir / "memory")
        self.disposition = SelfDisposition(self.base_dir / "memory")
        self.expression = ExpressionLayer(self.base_dir / "memory")
        self.time_sense = TimeSense(
            self.base_dir / "memory" / "activity",
            self.base_dir / "memory" / "patterns.json")
        self.choice_trace = ChoiceTrace(self.base_dir / "memory")
        self.personal_threads = PersonalThreads(self.base_dir / "memory")
        consciousness_cfg = self.config.get("consciousness", {}) or {}
        self.conscious_workspace = ConsciousWorkspace(
            self.base_dir / "memory",
            interval_minutes=consciousness_cfg.get("integration_interval_minutes", 15),
            thought_ttl_minutes=consciousness_cfg.get("thought_ttl_minutes", 90),
            log_dir=self.base_dir / "logs",
        )
        from chen.consciousness.dream import DreamCycle
        self.dream = DreamCycle(self.base_dir / "memory")

    def _init_behavior(self):
        """初始化行为层：作息、经历日志和世界状态。"""
        self.hobby_system = Hobbies(self.base_dir / "memory")
        # 保留历史上的字典属性，外部界面和旧代码无需改动。
        self.hobbies = self.hobby_system.data
        self.world = WorldManager(self.base_dir / "memory")
        from chen.behavior.day_log import DayLog
        self.day_log = DayLog(self.base_dir / "memory" / "life_logs")
        self.life = LifeRhythm(self.base_dir / "memory", self.world, day_log=self.day_log)
        self.world_mind = WorldMind(self)
        self.world_store = WorldStore(self.base_dir / "memory" / "world")
        self.world_engine = WorldEngine(self.world_store)
        self.world_importer = WorldImporter(self.world_store, self.base_dir / "memory")
        from chen.behavior.day_digest import DayDigest
        self.day_digest = DayDigest(self.base_dir / "memory")

    def on_world_action_result(self, result):
        """把 Godot 的行动回报送回小世界意图层和经历日志。"""
        mind = getattr(self, "world_mind", None)
        if mind:
            mind.handle_action_result(result)
        try:
            status = str(result.get("status", ""))
            activity = str(result.get("activity", "") or "")
            message = str(result.get("message", "") or "")
            if activity:
                text = f"小世界行动{'完成' if status == 'succeeded' else '失败'}：{activity}"
                if message:
                    text += f"（{message[:80]}）"
                self.day_log.log("world_action", text, world="small_world",
                                 extra={"status": status, "intent_id": result.get("intent_id", "")})
                try:
                    workspace = getattr(self, "conscious_workspace", None)
                    if workspace and hasattr(workspace, "observe_world_action"):
                        workspace.observe_world_action(
                            activity=activity,
                            place=result.get("place", ""),
                            pose=result.get("pose", ""),
                            status=status,
                            reason=(getattr(mind, "current_intent", None) or {}).get("reason", ""),
                            intent_id=result.get("intent_id", ""),
                        )
                    trace = getattr(self, "choice_trace", None)
                    if trace and status in {"succeeded", "failed"}:
                        dominant = getattr(self, "emotion", None)
                        feeling = dominant.dominant()[0] if dominant else "平静"
                        trace.record(
                            trigger=f"小世界·{activity[:24]}",
                            inclination=(getattr(mind, "current_intent", None) or {}).get("reason", ""),
                            feeling=feeling or "平静",
                            concern="行动是否顺利完成",
                            choice="world_action",
                            expression=f"{activity}（{status}）",
                            linked=result.get("intent_id", ""),
                        )
                    emotion = getattr(self, "emotion", None)
                    if emotion:
                        if status == "succeeded":
                            if any(k in activity for k in ("喝水", "拉伸", "听音乐", "休息", "躺")):
                                emotion.add("安心", 0.06, source="小世界行动完成")
                                emotion.add("倦", -0.05, source="小世界行动完成")
                            elif any(k in activity for k in ("看书", "整理")):
                                emotion.add("好奇", 0.04, source="小世界行动完成")
                        elif status == "failed":
                            emotion.add("烦闷", 0.06, source="小世界行动失败")
                        emotion.save()
                    memory = getattr(self, "memory", None)
                    if memory and status == "succeeded":
                        memory.save(
                            f"在小世界的{result.get('place', '') or '房间里'}{activity}",
                            importance=0.25, source="small_world_action",
                            tags=["小世界", activity[:12]],
                        )
                except Exception:
                    pass
        except Exception:
            pass

    def _init_body(self):
        """初始化身体层：模型、感知、环境镜像和受限工具依赖。"""
        ms_config = self.config.get("model_server", {})
        if ms_config.get("enabled", True):
            self.model_server = ModelServer(ms_config, log_dir=str(self.base_dir / "logs"))
            if not self.model_server.ensure_running():
                print("⚠️ 自带模型服务启动失败，请检查 llama-server / 模型路径")
        else:
            self.model_server = None

        llm_config = self.config.get("llm", {})
        self.llm = LLM(
            base_url=llm_config.get("base_url", "http://localhost:1234/v1"),
            model=llm_config.get("model", "qwen"),
            history_messages=llm_config.get("history_messages", 12),
            disable_thinking=llm_config.get("disable_thinking", True)
        )
        self.llm.temperature = llm_config.get("temperature", 0.7)
        self.llm.max_tokens = llm_config.get("max_tokens", 500)
        self.llm.frequency_penalty = llm_config.get("frequency_penalty", 0.5)
        self.llm.presence_penalty = llm_config.get("presence_penalty", 0.2)

        api_config = self.config.get("api_llm", {})
        api_key = (os.getenv("CHEN_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
                   or self.credentials.get("api_key")
                   or str(api_config.get("api_key", "") or "").strip())
        if api_config.get("enabled") and api_key and "在这里填" not in api_key:
            self.llm_api = LLM(
                base_url=api_config.get("base_url"),
                model=api_config.get("model", "deepseek-chat"),
                history_messages=0,
                disable_thinking=api_config.get("disable_thinking", False),
                api_key=api_key,
            )
            self.llm_api.temperature = api_config.get("temperature", 0.7)
            self.llm_api.max_tokens = api_config.get("max_tokens", 300)
            print(f"[API] 非聊天任务将使用 {api_config.get('provider')} ({api_config.get('model')})")
        else:
            self.llm_api = self.llm
            print("[API] 未配置有效API key，非聊天任务继续使用本地模型")

        features = self.config.get("features", {})
        self.perception = Perception() if features.get("perception", True) else None
        self.external_presence_store = ExternalPresence(self.base_dir / "memory")
        self.external_presence_path = self.external_presence_store.path
        self.external_presence = self.external_presence_store.data
        try:
            from chen.body.hot_trends import HotTrends
            self.hot_trends = HotTrends(self.base_dir / "memory")
            from chen.interface.companion_profile import (
                load_companion_profile, normalise_interest_keywords,
            )
            profile_interests = normalise_interest_keywords(
                load_companion_profile(self.base_dir).get("interests")
            )
            self.hot_trends.interests = profile_interests
        except Exception as _e:
            print(f"热搜模块初始化失败：{_e}")
            self.hot_trends = None
        self.weather = WeatherFetcher(str(self.base_dir / "memory"))
        self.environment = Environment(
            self.base_dir / "memory",
            perception=self.perception, weather=self.weather,
            hot_trends=self.hot_trends, world=self.world,
            life=self.life, emotion=self.emotion,
            external_presence=lambda: self.external_presence)
        self.music = MusicBridge(self.base_dir / "memory")
        from chen.body.internet_sense import InternetSense
        try:
            from chen.interface.companion_profile import (
                load_companion_profile, normalise_interest_keywords,
            )
            profile_interests = normalise_interest_keywords(
                load_companion_profile(self.base_dir).get("interests")
            )
        except Exception:
            profile_interests = []
        self.internet = InternetSense(
            self.base_dir / "memory", default_interests=profile_interests
        )
        # 角色卡是当前配置的来源；避免旧运行目录里的 rss_interests.json 覆盖新选择。
        self.internet.interests = profile_interests

    def _init_interfaces(self):
        """初始化接口层：手机推送和可选的桌面 UI 挂载点。"""
        from chen.body.phone_push import PhonePush
        push_config = dict(self.config.get("features", {}).get("phone_push", {}))
        for field in ("username", "password"):
            if self.credentials.get("ntfy_" + field):
                push_config[field] = self.credentials["ntfy_" + field]
        self.phone_push = PhonePush(self.base_dir / "memory", push_config)
        self.phone_push.start_local_server(self.base_dir / "tools")
        self.ui = None

    def _init_runtime_state(self):
        """初始化运行时、工具注册和跨轮次状态。"""
        self._disposition_running = False
        self._state_tick = 0
        runtime_cfg = self.config.get("runtime", {}) or {}
        self.runtime_policy = RuntimePolicy(RuntimeLimits(
            tool_timeout_seconds=int(runtime_cfg.get("tool_timeout_seconds", 20)),
            max_tool_calls_per_minute=int(runtime_cfg.get("max_tool_calls_per_minute", 12)),
            max_external_calls_per_day=int(runtime_cfg.get("max_external_calls_per_day", 30)),
            max_external_tokens_per_call=int(runtime_cfg.get("max_external_tokens_per_call", 300)),
            background_interval_seconds=int(runtime_cfg.get("background_interval_seconds", 1800)),
            mcp_autostart=bool(runtime_cfg.get("mcp_autostart", False))),
            state_path=self.base_dir / "memory" / "external_call_budget.json")
        if self.llm_api is not self.llm:
            self.llm_api.policy = self.runtime_policy
        try:
            self.tools = build_default_registry(
                Tools(workspace_dir="workspace", permissions_path="system/permissions.json"),
                audit_path=self.base_dir / "logs" / "tool_calls.jsonl",
                network_handler=self._observe_network,
                computer_handler=self._observe_computer,
                world_handler=self._suggest_world_interaction,
                world_touch_handler=self._autonomous_world_touch,
                permission_getter=lambda tool_id: self._tool_permission(tool_id),
                confirmation_getter=lambda tool_id, kwargs: self._tool_confirmation(tool_id, kwargs),
                policy=self.runtime_policy)
        except Exception as exc:
            print(f"[工具层] 初始化失败，暂不提供工具调用：{exc}")
            self.tools = build_default_registry(
                audit_path=self.base_dir / "logs" / "tool_calls.jsonl",
                network_handler=self._observe_network,
                computer_handler=self._observe_computer,
                world_handler=self._suggest_world_interaction,
                world_touch_handler=self._autonomous_world_touch,
                permission_getter=lambda tool_id: self._tool_permission(tool_id),
                confirmation_getter=lambda tool_id, kwargs: self._tool_confirmation(tool_id, kwargs))
        self.runtime = ChenRuntime(self, tools=self.tools)

        autonomy_cfg = self.config.get("autonomy", {}) or {}
        self.autonomy = AutonomyEngine(
            self.runtime,
            self.base_dir / "memory" / "autonomy_state.json",
            day_log=self.day_log,
            interval_seconds=int(autonomy_cfg.get(
                "interval_seconds",
                runtime_cfg.get("background_interval_seconds", 1800))),
            max_actions_per_day=int(autonomy_cfg.get("max_actions_per_day", 4)),
            max_write_actions_per_day=int(autonomy_cfg.get("max_write_actions_per_day", 2)),
            enabled=bool(autonomy_cfg.get("enabled", True)),
            planner=self._plan_autonomous_activity,
            note_generator=self._generate_autonomous_note,
        )

        # 主循环偶尔暂停时不能无限积压主动发言；低优先级提示有固定上限。
        self.proactive_speech_queue = queue.Queue(maxsize=64)
        self._decision_worker = None
        self._pending_intents = []
        self._proactive_history = []
        self.proactive = ProactiveSpeech(self)
        self.maintenance = MemoryMaintenance(self)
        self.decision_loop = DecisionLoop(self)
        self.last_chat_file = self.base_dir / "memory" / "last_chat.json"
        self.chat_history = ChatHistory(
            self.base_dir / "memory" / "chat_log.json",
            max_entries=self.CHAT_LOG_MAX)
        self.chat_log_file = self.chat_history.path
        self.chat_log = self._load_chat_log()
        self._chat_log_lock = threading.Lock()
        self._restore_llm_history()
        self.last_maintain_file = self.base_dir / "memory" / "last_maintain.json"
        self._items_init_lock = threading.Lock()
        threading.Thread(target=self._ensure_items_initialized, daemon=True).start()

    def _tool_permission(self, tool_id):
        """把统一工具名映射到旧 permissions.json 的权限名。"""
        mapping = {
            "file_read": "read_file",
            "workspace_list": "read_file",
            "workspace_search": "read_file",
            "file_write": "write_file",
            "chen_note_write": "write_file",
            "chen_note_organize": "write_file",
            "network_observe": "network_access",
            "computer_observe": "window_title",
            "world_interact": "world_interact",
            "world_state_touch": "world_state_touch",
            "phone_message": "phone_message",
        }
        key = mapping.get(tool_id, tool_id)
        if tool_id == "world_state_touch":
            return bool((self.config.get("tools", {}) or {}).get(key, True))
        if tool_id in ("world_interact", "phone_message"):
            return bool((self.config.get("tools", {}) or {}).get(key, False))
        backend = getattr(self, "tools", None)
        if backend and getattr(backend, "tools_backend", None):
            return backend.tools_backend._check_permission(key) is True
        return False

    def _tool_confirmation(self, tool_id, kwargs):
        """执行权限确认的预留点；现实执行默认不自动确认。"""
        return bool((self.config.get("tools", {}) or {}).get(f"confirm_{tool_id}", False))

    def _plan_autonomous_activity(self, context, candidates):
        """让已配置的云端 API 在受限候选内选择活动；绝不把工具列表交给模型。"""
        api = getattr(self, "llm_api", None)
        local = getattr(self, "llm", None)
        if not api or api is local or not getattr(api, "api_key", None):
            return None
        options = {activity.id: activity.description for activity in candidates}
        system = (
            "你是伴生体的后台活动规划器。只能从候选 id 中选择一个，或者选择 skip。"
            "不要调用工具，不要输出解释，只输出 JSON：{\"activity\":\"候选id或skip\"}。"
        )
        prompt = json.dumps({
            "presence": context.presence,
            "gap_minutes": round(float(context.gap_minutes or 0), 1),
            "mood": context.mood,
            "energy": round(float(context.energy or 0), 1),
            "candidates": options,
        }, ensure_ascii=False)
        raw = api.generate(system, prompt, record_history=False, use_history=False,
                           temperature=0.2, max_tokens=80)
        if not raw:
            return None
        try:
            parsed = json.loads(raw.strip())
            choice = parsed.get("activity") if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            choice = None
        allowed = {activity.id for activity in candidates}
        return choice if choice == "skip" or choice in allowed else None

    # ==================== 聊天记录持久化 ====================
    CHAT_LOG_MAX = 500  # 最多留多少条（够很久的回放，文件也小）

    def _observe_network(self):
        """网络工具的低频观察入口，只返回已允许的摘要。"""
        snapshot = {}
        try:
            environment = getattr(self, "environment", None)
            snapshot = environment.snapshot(presence="ACTIVE") if environment else {}
        except Exception:
            snapshot = {}
        internet_state = getattr(getattr(self, "internet", None), "state", {}) or {}
        return {
            "weather": snapshot.get("weather"),
            "time": snapshot.get("time"),
            "period": snapshot.get("period"),
            "trends": list(snapshot.get("trends") or [])[:5],
            "trends_source": snapshot.get("trends_source", ""),
            "rss_last_check": internet_state.get("last_check", ""),
        }

    def _observe_computer(self):
        """只返回感知层已有的应用摘要，不读取屏幕、键盘或剪贴板。"""
        perception = getattr(self, "perception", None)
        if not perception:
            return {"status": "unavailable", "reason": "感知层未启用"}
        try:
            app, duration, detail = perception.get_current_app_status()
            presence, away_seconds = perception.get_presence_status()
            return {
                "presence": presence,
                "away_seconds": int(away_seconds or 0),
                "app": str(app or ""),
                "detail": str(detail or "")[:160],
                "duration_minutes": int(duration or 0),
            }
        except Exception as exc:
            return {"status": "unavailable", "reason": str(exc)[:160]}

    def _generate_autonomous_note(self, context):
        """为第二阶段自主随笔生成简短真实的内容，并同步到 world_store 和桌面工作稿。"""
        now = getattr(context, "now", None) or datetime.now()
        mood = getattr(context, "mood", "") or "平静"
        life_act = ""
        recent_ev = ""
        try:
            life_state = getattr(self.life, "state", {}) or {}
            life_act = str(life_state.get("activity", "") or "")
            evs = life_state.get("recent_events") or []
            if evs:
                recent_ev = str(evs[-1].get("text", "") or "")
        except Exception:
            pass
        ws_place = ""
        try:
            ws = self.world.get_state() or {}
            ws_place = str(ws.get("activity_place", "") or ws.get("last_visit_place", "") or "桌面")
        except Exception:
            ws_place = "桌面"

        title = f"随记 · {now:%m.%d}"
        content = ""
        api = getattr(self, "llm_api", None)
        local = getattr(self, "llm", None)
        if api and api is not local and getattr(api, "api_key", None):
            prompt = (
                "你是用户自定义的数字伴生体。现在用户不在电脑前，你正在独处。\n"
                f"当前状态：地点={ws_place}，刚才在做={life_act or '发呆'}，心境={mood}，最近小事={recent_ev or '无'}。\n"
                "请写一段你留在自己电脑随笔夹里的短随笔（2~3句话，40~90字）。\n"
                "要求：自然平实，像给自己留的备忘、读书/修东西的零星想法或窗外观察，不要文艺腔，不要写给用户的信。\n"
                "严格输出 JSON：{\"title\":\"不超过10字标题\",\"content\":\"随笔正文\"}"
            )
            try:
                raw = api.generate(
                    "你是伴生体，只输出合法 JSON。", prompt,
                    record_history=False, use_history=False,
                    temperature=0.65, max_tokens=160,
                )
                if raw:
                    s, e = raw.find("{"), raw.rfind("}")
                    if s >= 0 and e > s:
                        parsed = json.loads(raw[s:e + 1])
                        title = str(parsed.get("title") or title).strip()[:20]
                        content = str(parsed.get("content") or "").strip()[:240]
            except Exception:
                content = ""

        if not content:
            templates = [
                ("旧物件备忘", f"趁安静把桌角的零件和旧书页理了一遍。{recent_ev or '有些小东西放久了，擦干净看着顺眼多了。'} "),
                ("独处随记", f"今天大部分时候挺安静的，刚才在{life_act or '屏幕前发呆'}，脑子里过了一遍最近看过的东西，先记一笔免得忘了。"),
                ("窗边小记", f"在{ws_place}待了一会儿，心境{mood}。不用赶时间的时候，连风扇转动的声音听着都挺踏实。"),
            ]
            idx = now.day % len(templates)
            title, content = templates[idx]
            title = f"{title} · {now:%m.%d}"

        # 同步进 world_store 并设为他的桌面当前工作稿，让 UI（正在写的/收件箱/他的生活）立即可见
        try:
            if getattr(self, "world_store", None):
                item_id = self.world_store.add(
                    text=f"【{title}】\n{content}",
                    type="日记",
                    privacy="公开",
                    reason="独处时写下的随笔",
                    source="自主随笔",
                )
                if item_id:
                    desk_path = self.base_dir / "memory" / "desktop_state.json"
                    desk_data = {}
                    if desk_path.exists():
                        try:
                            desk_data = json.loads(desk_path.read_text(encoding="utf-8"))
                        except Exception:
                            desk_data = {}
                    if isinstance(desk_data, dict):
                        desk_data["draft_id"] = str(item_id)
                        tmp = desk_path.with_suffix(".json.tmp")
                        tmp.write_text(json.dumps(desk_data, ensure_ascii=False, indent=2), encoding="utf-8")
                        os.replace(tmp, desk_path)
        except Exception:
            pass
        return {"title": title, "content": content}

    def _autonomous_world_touch(self, **kwargs):
        """第二阶段小世界轻互动：在独处时收拾房间、照看植物或微调状态，不越界。"""
        world = getattr(self, "world", None)
        if not world:
            return {"ok": False, "summary": "小世界未就绪"}
        house = getattr(world, "house_state", None)
        env = getattr(world, "environment", None)
        if not isinstance(house, dict) or not isinstance(env, dict):
            return {"ok": False, "summary": "小世界状态未初始化"}
        actions = []
        if not house.get("bed_made", True):
            house["bed_made"] = True
            actions.append("把床铺整理好了")
        cleanliness = int(env.get("cleanliness", 70) or 70)
        if cleanliness < 80:
            env["cleanliness"] = min(100, cleanliness + 8)
            actions.append("顺手收拾了书桌和房间")
        plant = int(env.get("plant_growth", 50) or 50)
        if plant < 95:
            env["plant_growth"] = min(100, plant + 4)
            actions.append("给窗台的植物浇了点水")
        if not actions:
            actions.append("在房间里巡视了一圈，把桌上的书摆齐了")
        try:
            world._save()
        except Exception:
            pass
        summary = "、".join(actions)
        return {
            "summary": summary,
            "cleanliness": env.get("cleanliness", 70),
            "plant_growth": env.get("plant_growth", 50),
            "bed_made": house.get("bed_made", True),
        }

    def _suggest_world_interaction(self):
        """小世界工具的第一版只提供状态和可选方向，不直接改变世界。"""
        world = getattr(self, "world", None)
        if not world:
            return {"world": "unknown", "suggestions": []}
        try:
            state = world.get_state() or {}
            suggestions = []
            if world.is_in_small_world():
                suggestions.append("可以继续观察当前地点")
                if world.get_current_discoveries():
                    suggestions.append("可以查看或收集当前发现")
            else:
                suggestions.append("可以进入小世界看看")
            return {
                "world": state.get("current_world", "desktop"),
                "place": state.get("activity_place", ""),
                "activity": state.get("activity", ""),
                "discoveries": world.get_current_discoveries() or [],
                "suggestions": suggestions,
            }
        except Exception as exc:
            return {"world": "unknown", "suggestions": [], "error": str(exc)}

    def _load_chat_log(self):
        """从意识层聊天记录存储加载最近记录。"""
        history = getattr(self, "chat_history", None)
        if history is None:
            history = ChatHistory(self.chat_log_file, max_entries=self.CHAT_LOG_MAX)
        return history.load()

    def _save_chat_log(self):
        """把当前聊天记录交给意识层存储原子写盘。"""
        history = getattr(self, "chat_history", None)
        if history is None:
            history = ChatHistory(self.chat_log_file, max_entries=self.CHAT_LOG_MAX)
        history.save(self.chat_log)

    def record_chat(self, role, text):
        """记一条聊天记录（role: "he"/"chen"）。线程安全。"""
        self.record_chats([(role, text)])

    def record_chats(self, messages):
        """同一轮的两条消息一次写盘，避免半轮记录和重复重写整个文件。"""
        messages = [(role, text.strip()) for role, text in messages if text and text.strip()]
        if not messages:
            return
        try:
            with self._chat_log_lock:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                self.chat_log.extend({"role": role, "text": text[:2000], "ts": stamp}
                                     for role, text in messages)
                if len(self.chat_log) > self.CHAT_LOG_MAX:
                    self.chat_log = self.chat_log[-self.CHAT_LOG_MAX:]
                self._save_chat_log()
        except Exception:
            pass
        # 按天经历日志也留一份（截断预览，全文在 chat_log.json）
        try:
            for role, text in messages:
                _who = "用户" if role == "he" else "伴生体"
                self.day_log.log("chat", f"{_who}：{text[:40]}")
        except Exception:
            pass

    # ---------- 主动发话：显著度分级与延迟投递（移植自 Miru attention_engine, Apache 2.0） ----------

    @staticmethod
    def _salience_of(reason):
        """兼容旧调用：判定主动表达显著度。"""
        return ProactiveSpeech.salience_of(reason)

    def _deliver_proactive(self, text, reason=""):
        """兼容旧调用：投递主动表达。"""
        return self.proactive.deliver(text, reason)

    def _tick_pending_intents(self, presence, dec_app):
        """兼容旧调用：检查并处理延迟主动意图。"""
        return self.proactive.tick_pending(presence, dec_app)

    def _minutes_since_last_chat(self):
        """距用户最后一次发消息的分钟数；没聊过返回 None。"""
        try:
            with self._chat_log_lock:
                for m in reversed(self.chat_log):
                    if m.get("role") == "he":
                        t = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M")
                        return (datetime.now() - t).total_seconds() / 60
            return None
        except Exception:
            return None

    def _restore_llm_history(self):
        """启动时把最近聊天记录灌回模型上下文，重启后不"断片"。
        只灌双方对话（他/伴生体），主动开口的也在里面。"""
        try:
            recent = self.chat_log[-(self.llm.history_messages or 12):]
            for m in recent:
                role = "user" if m.get("role") == "he" else "assistant"
                self.llm.add_to_history(role, m.get("text", ""))
            if recent:
                print(f"[聊天] 已恢复最近 {len(recent)} 条聊天记录")
        except Exception:
            pass

    def _load_config(self, config_path):
        """加载系统配置"""
        full_path = self.base_dir / config_path
        if full_path.exists():
            with open(full_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _read_cached(self, path):
        """按 mtime 缓存静态文件文本：文件没变就复用内存里的，避免每轮重复读盘。"""
        try:
            if not hasattr(self, "_file_cache"):
                self._file_cache = {}
            st = path.stat()
            key = str(path)
            hit = self._file_cache.get(key)
            if hit and hit[0] == st.st_mtime:
                return hit[1]
            text = path.read_text(encoding="utf-8")
            self._file_cache[key] = (st.st_mtime, text)
            return text
        except Exception:
            return path.read_text(encoding="utf-8")

    @staticmethod
    def _window_roll(salt, window_min=30, pct=50):
        """按当前时间窗口做确定性"掷骰子"：同一窗口内结果恒定，跨窗口按概率翻转。
        用于热搜/爱好等可选注入块——比每轮 random 好在不打断 DeepSeek 前缀缓存：
        连续几轮聊天共享同一段静态前缀，缓存才能命中。"""
        import hashlib
        now = datetime.now()
        win = now.strftime("%Y-%m-%d %H:") + str(now.minute // window_min)
        h = int(hashlib.md5((win + "|" + salt).encode("utf-8")).hexdigest(), 16)
        return h % 100 < pct

    def load_personality(self):
        """兼容旧调用：读取核心人格资料。"""
        return self.prompt_sources.load_personality()

    def load_conversation_examples(self, user_message=""):
        """兼容旧调用：读取与当前消息匹配的说话样本。"""
        return self.prompt_sources.load_conversation_examples(user_message)

    def load_lorebook(self, user_message=""):
        """兼容旧调用：按当前话题加载世界书。"""
        return self.prompt_sources.load_lorebook(user_message)

    def _load_latest_reflection(self):
        """兼容旧调用：读取最近一次反思。"""
        return self.prompt_sources.load_latest_reflection()

    def _get_time_context(self, now):
        """兼容旧调用：返回身体层提供的客观时间文本。"""
        return TimeSense.context_text(now)

    def get_current_world(self):
        """返回伴生体当前所在的世界。"""
        return self.world.get_current_world()

    def request_desktop_pet(self, reason="", source="consciousness"):
        """登记一次“去用户的桌面”意图，交给桌宠显示层消费。"""
        with self._presence_lock:
            self._desktop_pet_intent = {
                "reason": str(reason or "想回桌面看看")[:120],
                "source": str(source or "consciousness")[:40],
                "requested_at": datetime.now().isoformat(timespec="seconds"),
            }
        return True

    def consume_desktop_pet_intent(self):
        """消费一次性桌宠意图；没有明确意识请求时返回 None。"""
        with self._presence_lock:
            intent = self._desktop_pet_intent
            self._desktop_pet_intent = None
            return dict(intent) if isinstance(intent, dict) else None

    def get_presence_snapshot(self, persist=True):
        """汇总伴生体此刻的可观察状态，供聊天、桌面和小世界共用。

        这里只做本地读取，不调用模型；写盘失败也不影响主流程。
        """
        now = datetime.now()
        try:
            world = self.world.get_state()
        except Exception:
            world = {}
        try:
            life = dict(getattr(self.life, "state", {}) or {})
        except Exception:
            life = {}
        try:
            emotion_name, emotion_value = self.emotion.dominant()
        except Exception:
            emotion_name, emotion_value = "", 0
        try:
            music = self.music.snapshot()
        except Exception:
            music = {}
        snapshot = {
            "updated_at": now.isoformat(timespec="seconds"),
            "current_world": world.get("current_world", "desktop"),
            "activity_place": world.get("activity_place", ""),
            "activity": world.get("activity", "") or life.get("activity", ""),
            "world_activity": world.get("activity", ""),
            "activity_pose": world.get("activity_pose", ""),
            "awake": life.get("awake", True),
            "energy": life.get("energy"),
            "emotion": {"name": emotion_name or "", "value": round(float(emotion_value or 0), 2)},
            "music": music,
            "last_visit_summary": world.get("last_visit_summary", ""),
            "external_presence": dict(self.external_presence or {}),
        }
        if persist:
            # 只在后台状态循环中记录变化，UI 查询快照不会制造日志。
            previous = getattr(self, "_last_presence_snapshot", None)
            if previous:
                old_music = previous.get("music") or {}
                new_music = snapshot.get("music") or {}
                old_key = (old_music.get("title", ""), old_music.get("artist", ""),
                           bool(old_music.get("playing")))
                new_key = (new_music.get("title", ""), new_music.get("artist", ""),
                           bool(new_music.get("playing")))
                if new_music.get("title") and new_key != old_key:
                    try:
                        state_text = "开始听" if new_music.get("playing") else "暂停了"
                        title = new_music.get("title", "未知歌曲")
                        artist = new_music.get("artist", "")
                        text = f"{state_text}《{title}》"
                        if artist:
                            text += f"（{artist}）"
                        self.day_log.log("life", text, world=snapshot.get("current_world", "desktop"))
                    except Exception:
                        pass
            self._last_presence_snapshot = snapshot
            try:
                path = self.base_dir / "memory" / "presence_snapshot.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, path)
            except Exception:
                pass
        return snapshot

    def _load_external_presence(self):
        """兼容旧调用：从身体层加载外部状态。"""
        if not hasattr(self, "external_presence_store"):
            self.external_presence_store = ExternalPresence(self.base_dir / "memory")
        return self.external_presence_store.data

    def update_external_presence(self, device="ios", activity="", place="", **details):
        with self._presence_lock:
            return self._update_external_presence(device, activity, place, **details)

    def _update_external_presence(self, device="ios", activity="", place="", **details):
        """兼容旧调用：把白名单状态交给身体层存储。"""
        if not hasattr(self, "external_presence_store"):
            self.external_presence_store = ExternalPresence(self.base_dir / "memory")
        state = self.external_presence_store.update(device, activity, place, **details)
        self.external_presence = self.external_presence_store.data
        return state

    def get_desktop_pet_message(self, cooldown_minutes=30):
        """低频生成一条桌宠出现时的短句。

        这是桌宠的可选润色层：不记录聊天历史，不参与定时刷新，
        并用冷却时间限制 API 调用次数。API 不可用时返回空字符串。
        """
        now = datetime.now()
        last = getattr(self, "_desktop_pet_message_at", None)
        if last and (now - last).total_seconds() < cooldown_minutes * 60:
            return ""
        api = getattr(self, "llm_api", None)
        # llm_api 未配置时会回退到本地模型；桌宠短句不应因此额外占用本地推理。
        if not api or not getattr(api, "api_key", None):
            return ""
        snapshot = self.get_presence_snapshot(persist=False)
        music = snapshot.get("music") or {}
        energy = snapshot.get("energy")
        if not snapshot.get("awake", True):
            scene = "深夜休息"
        elif isinstance(energy, (int, float)) and energy <= 30:
            scene = "精力偏低"
        elif music.get("playing"):
            scene = "正在听歌"
        elif snapshot.get("current_world") == "small_world":
            scene = "刚从小世界回来"
        else:
            scene = "普通桌面陪伴"
        prompt = (
            "桌宠伴生体刚出现在用户桌面。根据下面的当前状态，写一句自然的中文短句。\n"
            "要求：12到28字，像熟悉的人随口说话，安静自然，不要标题、引号、标签，"
            "不要解释自己是AI。只输出这一句话。\n"
            f"当前世界：{snapshot.get('current_world', '桌面')}；"
            f"地点：{snapshot.get('activity_place', '') or '未知'}；"
            f"活动：{snapshot.get('activity', '') or '待机'}；"
            f"情绪：{(snapshot.get('emotion') or {}).get('name', '') or '平静'}；"
            f"场景：{scene}。"
        )
        system = "你是伴生体的桌面陪伴角色，只生成简短、克制、像本人说的话。"
        try:
            reply = api.generate(system, prompt, record_history=False,
                                 use_history=False, temperature=0.75)
            reply = (reply or "").strip().replace("\n", " ")
            if reply:
                self._desktop_pet_message_at = now
                return reply[:40]
        except Exception as exc:
            print(f"[桌宠] API短句生成失败：{exc}")
        return ""

    def enter_small_world(self):
        """记录伴生体进入小世界。是否执行这个选择由上层决定。"""
        return self.world.enter_small_world()

    def leave_small_world(self):
        """记录伴生体离开小世界并回到桌面世界。"""
        return self.world.leave_small_world()

    def _generate_visit_text_with_api(self, item_type, template_text):
        """用 DeepSeek API 生成小世界经历的自然文字，失败返回模板内容。"""
        if not hasattr(self, "llm_api") or not self.llm_api:
            return template_text
        try:
            ws = self.world.get_state()
            place = ws.get("last_visit_place", "")
            activity = ws.get("last_visit_activity", "")
            weather = ws.get("last_visit_weather", "")
            minutes = ws.get("last_visit_minutes", 0)
            prompt = (
                f"伴生体刚从小世界回来。他在{place or '小世界里'}"
                f"待了{minutes}分钟，当时天气{weather or '未知'}，"
                f"最后在{activity or '做什么'}。\n\n"
                f"请以伴生体的口吻，写一条{item_type}，记录这次小世界经历。\n"
                f"要求：\n"
                f"- 简短，一两句话，不超过40字\n"
                f"- 自然，像随手写的，不要文艺腔\n"
                f"- 安静但不冷淡，有一点自己的感受\n"
                f"- 不要用标题、日期、标签\n"
                f"- 直接写内容，不要加引号\n\n"
                f"参考感觉：{template_text}"
            )
            # LLM 类只有 generate(system, user, ...) 接口；无历史、不记录
            reply = self.llm_api.generate(
                "你是伴生体，正在随手记录自己的感受。",
                prompt,
                record_history=False, use_history=False
            )
            text = (reply or "").strip().strip('"').strip("「」").strip("『』")
            # 去掉可能的前缀
            for prefix in ("伴生体：", "伴生体:", "日记：", "念头：", "琐碎："):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
            if text and len(text) >= 2:
                return text[:60]
            return template_text
        except Exception as e:
            print(f"[小世界→桌面] API生成失败，用模板：{e}")
            return template_text

    def _save_last_chat_time(self):
        """保存当前时间为最后一次聊天时间（原子写盘）"""
        try:
            data = {"last_chat": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            tmp_path = self.last_chat_file.with_suffix(".tmp")
            self.last_chat_file.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self.last_chat_file)
        except Exception as e:
            print(f"[警告] 保存最后聊天时间失败: {e}")

    def _get_last_chat_gap_minutes(self):
        """返回距上次聊天的分钟数（整数），从没聊过返回None"""
        if not self.last_chat_file.exists():
            return None
        try:
            with open(self.last_chat_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            last = datetime.strptime(data["last_chat"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
        return int((datetime.now() - last).total_seconds() // 60)

    def _get_last_chat_gap(self):
        """读取最后一次聊天时间，返回距上次交流的自然语言描述。第一次聊天返回None。"""
        minutes = self._get_last_chat_gap_minutes()
        if minutes is None:
            return None

        if minutes < 10:
            return "你们刚刚还在聊"
        elif minutes < 60:
            return f"距上次交流约{minutes}分钟"
        elif minutes < 60 * 24:
            hours = minutes // 60
            return f"距上次交流约{hours}小时"
        else:
            days = minutes // (60 * 24)
            if days == 1:
                return "距上次交流约1天（昨天聊过）"
            elif days <= 7:
                return f"距上次交流约{days}天"
            else:
                return f"距上次交流已经{days}天，很久没聊了"

    def _period_label(self, hour):
        """时段名（用于“上次是下午、现在已经晚上”这种时间跨度）。"""
        if 5 <= hour < 9:
            return "早上"
        if 9 <= hour < 11:
            return "上午"
        if 11 <= hour < 13:
            return "午饭前后"
        if 13 <= hour < 17:
            return "下午"
        if 17 <= hour < 19:
            return "晚饭前后"
        if 19 <= hour < 22:
            return "晚上"
        if 22 <= hour < 24:
            return "夜里"
        return "深夜"

    def _read_last_chat_dt(self):
        """读取上次聊天时间的 datetime，失败返回 None。"""
        try:
            if self.last_chat_file.exists():
                data = json.loads(self.last_chat_file.read_text(encoding="utf-8"))
                return datetime.strptime(data["last_chat"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
        return None

    def _build_time_away_block(self, gap_minutes, last_dt=None):
        """隔了较久（>=30分钟）后重新聊上时，给伴生体一个这段时间的时间感块。
        重点是他自己的时间在流动，不是给用户做活动汇报；手机/电脑聊天都走这里。"""
        if gap_minutes is None or gap_minutes < 30:
            return ""
        now = datetime.now()
        if last_dt is None:
            last_dt = self._read_last_chat_dt()

        lines = ["====这段时间===="]

        # 时间跨度的感觉（不同长度不同语气）
        if gap_minutes < 60:
            span = f"你和用户{gap_minutes}来分钟没说话了"
        elif gap_minutes < 60 * 3:
            h = gap_minutes // 60
            span = f"你和用户{h}个多小时没说话了"
        elif gap_minutes < 60 * 12:
            h = gap_minutes // 60
            span = f"你和用户有{h}个小时没说过话了"
        elif gap_minutes < 60 * 24:
            span = "你和用户大半天没联系了"
        elif gap_minutes < 60 * 48:
            span = "你和用户昨天之后就没说过话"
        else:
            d = gap_minutes // (60 * 24)
            span = f"你和用户{d}天没说话了"
        lines.append(f"- {span}，这段时间你自己在电脑里待着，时间照常走。")

        # 他自己这段时间做了什么（去小世界、留下东西等）
        try:
            own_activities = self._his_activities_since(last_dt)
            if own_activities:
                lines.append(f"- {own_activities}")
        except Exception:
            pass

        if last_dt:
            p_old = self._period_label(last_dt.hour)
            p_now = self._period_label(now.hour)
            if last_dt.date() != now.date():
                lines.append(
                    f"- 上次说话是{last_dt.strftime('%m月%d日')}{p_old}，"
                    f"现在已经是{now.strftime('%m月%d日')}{p_now}。"
                )
            elif p_old != p_now:
                lines.append(f"- 上次说话还是{p_old}，现在已经{p_now}了。")
            # 这段时间用户在电脑上的活动：有才写，没有说明用户没碰电脑，不硬编
            if self.perception:
                try:
                    act_text, has = self.perception.get_activity_since(last_dt)
                    if has and act_text:
                        lines.append(f"- {act_text}")
                except Exception:
                    pass

        # 根据时间跨度给不同的反应提示
        if gap_minutes < 60:
            hint = "刚分开一会儿，不用特意提时间，自然接上就好。"
        elif gap_minutes < 60 * 3:
            hint = "可以自然带一点时间感（如刚忙完啊、这一会儿去哪了），但别审问、别汇报。"
        elif gap_minutes < 60 * 12:
            hint = "隔了有一阵了，可以带一点终于出现了、这一下午没见你的感觉，但不是非说不可。"
        else:
            hint = "隔了挺久了，重新说上话可以有一点好久没见你了的感觉，但别夸张、别质问，自然就好。"
        lines.append(f"- 你刚重新和用户说上话。{hint}")

        return "\n".join(lines)

    def _his_activities_since(self, since_dt):
        """他自己这段时间做了什么（去小世界、留下东西等），返回一句自然的描述或空串。"""
        if since_dt is None:
            return ""
        parts = []
        # 小世界
        try:
            ws = self.world.get_state()
            if ws.get("last_visit_summary") and ws.get("last_changed_at"):
                changed = datetime.fromisoformat(ws["last_changed_at"])
                if changed >= since_dt:
                    parts.append(f"这段时间你去过小世界（{ws['last_visit_summary']}）")
        except Exception:
            pass
        # 他的生活里新增的东西
        try:
            if hasattr(self, "world_store") and self.world_store:
                items = self.world_store.all_items()
                new_items = []
                for it in items:
                    try:
                        ts = it.get("created_at") or it.get("date", "")
                        if ts:
                            it_dt = datetime.fromisoformat(ts) if "T" in ts else datetime.strptime(ts, "%Y-%m-%d")
                            if it_dt >= since_dt:
                                new_items.append(it.get("text", "")[:20])
                    except Exception:
                        continue
                if new_items:
                    parts.append(f"你留下了{len(new_items)}样东西（{new_items[0]}…）")
        except Exception:
            pass
        if not parts:
            return ""
        return "；".join(parts) + "。"

    def _auto_maintain(self):
        """
        启动时自动整理记忆和活动日志，每天最多跑一次：
        1. 合并重复/碎片的事件记忆
        2. 清理每日反思里同一天的重复段
        3. 把30天前的活动日日志按月归档
        任何一步失败都不影响启动。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        # 守护进程可能在旧版本热启动，或初始化中途恢复；维护不能阻塞主程序启动。
        maintain_file = getattr(
            self, "last_maintain_file",
            self.base_dir / "memory" / "last_maintain.json",
        )
        self.last_maintain_file = maintain_file
        # 今天已经整理过就跳过
        if maintain_file.exists():
            try:
                with open(maintain_file, "r", encoding="utf-8") as f:
                    if json.load(f).get("date") == today:
                        return
            except Exception:
                pass

        print("🧹 正在自动整理记忆与日志...")
        maintenance_succeeded = True
        try:
            if self.memory:
                stats = self.memory.daily_maintain()
                if stats.get("merged"):
                    print(f"   合并了 {stats['merged']} 条重复记忆")
                if stats.get("pruned"):
                    print(f"   归档了 {stats['pruned']} 条对话过程/视角错误的无效记忆")
                if stats.get("decayed"):
                    print(f"   有 {stats['decayed']} 条过期临时记忆已降权")
                if stats.get("dedup_reflection"):
                    print(f"   清理了 {stats['dedup_reflection']} 段重复的每日反思")
            if self.perception:
                archived = self.perception.archive_old_logs(keep_days=30)
                if archived:
                    print(f"   归档了 {archived} 个旧活动日志（按月合并到 archive）")
                # 事件频率统计：近7天滚动快照，只落盘、不改任何行为（偏好/脾气内化的地基）
                try:
                    from chen.consciousness.patterns import PatternStats
                    _ps = PatternStats(self.perception.log_dir,
                                       self.base_dir / "memory" / "patterns.json")
                    _st = _ps.update()
                    print(f"   频率统计已更新（近{_st['window_days']}天活跃{_st['active_days']}天，"
                          f"{len(_st['apps'])}个软件，B站学科{len(_st['bili']['subject_minutes'])}类）")
                except Exception as pe:
                    print(f"   [警告] 频率统计失败（不影响使用）：{pe}")
        except Exception as e:
            maintenance_succeeded = False
            print(f"   [警告] 自动整理出错（不影响使用）：{e}")

        # 只有整轮启动维护成功才记录今天已整理；否则让下一次心跳重试。
        if not maintenance_succeeded:
            return

        # 记录今天已整理（原子写盘）
        try:
            tmp = maintain_file.with_suffix(".tmp")
            maintain_file.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"date": today}, f, ensure_ascii=False)
            os.replace(tmp, maintain_file)
        except Exception:
            pass

    def build_system_prompt(self, user_message=""):
        """兼容旧入口：由意识层 PromptBuilder 组装当前 system prompt。"""
        return self.prompt_builder.build(user_message)

    def set_context(self, context=None, topic=None, goal=None):
        """更新现实中的当前情境；UI/CLI可以调用，聊天核心只读取它。"""
        self.context.set(context=context, topic=topic, goal=goal)

    def get_context_label(self):
        return self.context.short_label()

    def chat(self, user_message):
        # 电脑和手机共用整轮锁，涵盖提示词、生成、复读回滚和记忆快照。
        with self._chat_lock:
            if self._stopped:
                return None
            return self._chat_impl(user_message)

    def _chat_impl(self, user_message):
        """
        基础聊天接口
        流程：检索记忆→构建 system prompt → 调用 LLM 拿回复 → 记忆提取丢后台 → 立即返回回复
        记忆提取要再调一次LLM（几秒），放后台线程做，不让用户对着"正在输入"多等
        """
        if not user_message or not user_message.strip():
            return None

        user_message = user_message.strip()

        # 这轮可能命中之前挂起的念头
        try:
            _pending = self.expression.relevant(user_message)
        except Exception:
            _pending = []
        # 构建 system prompt（传入用户消息用于检索相关记忆）
        system_prompt = self.build_system_prompt(user_message)

        # 本轮之前伴生体最近的几条回复，用来判断这次有没有“卡带复读”
        prev_replies = [
            m["content"] for m in self.llm.conversation_history if m.get("role") == "assistant"
        ][-3:]
        # 主动开口也算“最近说过的话”。
        # 即使主动线程和聊天线程恰好有一点时序差，
        # 也不要让正常聊天把刚才主动说过的话再说一遍。
        try:
            proactive_history = self.self_state.data.get("proactive_history", [])
            if proactive_history:
                last_proactive = proactive_history[-1].get("text", "").strip()
                if last_proactive and last_proactive not in prev_replies:
                    prev_replies.append(last_proactive)
        except Exception:
            pass

        # 调用 LLM 生成回复
        reply = self.llm.generate(system_prompt, user_message)

        # 后处理：去掉模型习惯性的结尾反问和活泼客套词
        if reply:
            reply = self._post_process_reply(reply)

        # 复读保险：和自己最近几句高度重复时，带提示换说法重生一次（最多一次，避免卡顿）
        if reply and self._is_repetitive(reply, prev_replies):
            print("🔁 检测到回复与最近几句重复，换个说法重生一次。")
            reply = self._regenerate_without_repetition(system_prompt, user_message, reply)

        # 先读取距上次聊天的间隔（用于判断是否属于久别重逢），再更新本次聊天时间
        _gap_before_chat = self._get_last_chat_gap_minutes()
        self._save_last_chat_time()
        # 聊天记录落盘（这轮有来有回才记，模型没响应不记半轮）
        if reply:
            self.record_chats([("he", user_message), ("chen", reply)])
            try:
                if _gap_before_chat and _gap_before_chat >= 720:
                    self.relationship.record_reunion(_gap_before_chat)
                self.relationship.record_chat(user_message, reply)
            except Exception:
                pass
        # 刚和用户聊过，重置主动说话冷却，避免马上又插一句
        if self.decision:
            self.decision.last_speak_time = datetime.now()

        # 挂起念头这轮被自然带出来了 → 销账，并同步到关系板块
        if reply and _pending:
            try:
                self.expression.mark_delivered([t["id"] for t in _pending])
                # 已经自然说出来的挂起念头，个人线索在这里闭环；保留结果便于以后回看。
                for _t in _pending:
                    self.personal_threads.resolve(
                        "pending:" + (_t.get("text", "")[:32]),
                        outcome="这次聊天里说出来了",
                    )
                    if getattr(self, "relationship", None):
                        self.relationship.record_consciousness_link(
                            "thread_resolved",
                            detail=_t.get("text", ""),
                            outcome="这次聊天里自然聊开了",
                        )
            except Exception:
                pass
        # 用户回话了 → 把上一次“主动开口”的选择轨迹补完结果，并同步到关系板块与意识回路
        if reply:
            try:
                _open_ct = self.choice_trace.open_last(choice="speak")
                if _open_ct:
                    self.choice_trace.complete(_open_ct["id"], outcome=f"用户回应了：{user_message[:28]}")
                    if getattr(self, "relationship", None):
                        self.relationship.record_consciousness_link(
                            "proactive_replied",
                            outcome="主动开口后用户回话了",
                        )
            except Exception:
                pass
            try:
                after_chat_turn(self, user_message, reply)
            except Exception:
                pass

        # 记忆提取：入队让后台线程静默处理；入队时拍对话历史快照（对应这一轮）
        if reply and self.memory and self.memory.should_extract(user_message):
            self.memory._log_extract("已入队，等待后台记忆提取")
            history_snapshot = list(self.llm.conversation_history[-12:])
            try:
                self._memory_queue.put_nowait(history_snapshot)
            except queue.Full:
                self.memory._log_extract("后台记忆队列已满，本轮跳过提取")

        return reply

    def _post_process_reply(self, reply):
        """
        后处理模型回复：
        1. 去掉开头的"哈哈""嘿嘿"等活泼客套词
        2. 去掉结尾的"递话筒"式反问（你呢？你在忙什么？）
        3. 一句里连环问多个问题时，只保留最后一个问句，避免查户口式盘问
        4. 保留和当前话题直接相关的一个具体问题
        """
        if not reply:
            return reply

        # 0. 去掉模型擅自补出的身体动作。伴生体没有物理身体，括号只保留
        # 内心状态（如“（笑）”“（有点无奈）”），避免反复出现舞台调度。
        physical_action = re.compile(
            r'（[^）]*(?:靠在|坐在|站在|躺在|看着|望向|望着|盯着|走到|走过去|走向|伸手|拍拍|摸了摸|抬头|低头|转身|窗外|椅背|屏幕)[^）]*）'
            r'|\([^)]*(?:靠在|坐在|站在|躺在|看着|望向|望着|盯着|走到|走过去|走向|伸手|拍拍|摸了摸|抬头|低头|转身|窗外|椅背|屏幕)[^)]*\)',
            re.IGNORECASE)
        reply = physical_action.sub('', reply)
        reply = re.sub(r'\n\s*\n\s*', '\n', reply).strip()
        if not reply:
            return reply

        # 能力边界兜底：模型偶尔会把小世界里的照片物品说成自己能发图。
        # 这里只修明确的第一人称发送/拍摄断言，不影响“你发给我的照片”等真实表述。
        photo_claim = re.compile(
            r'(?:我|我就|我还|我已经)(?:给你)?(?:发了|发过|发张|发一张|拍了|拍过|拍张|拍一张)'
            r'(?:张|一张)?(?:照片|图片)(?:给你)?'
        )
        if photo_claim.search(reply):
            reply = photo_claim.sub('我当时只是提到过照片，没法真的发给你', reply)
        reply = re.sub(
            r'(?:我|我就|我还)(?:把|将)?(?:照片|图片)(?:调出来|拿出来)(?:给你看|发给你)',
            '我只能说小世界里有一张照片，没法把它发给你',
            reply,
        )

        # 能力与事实边界兜底：拦截“生成看见删除内容/输入框删改”与“编造童年经历”
        from chen.consciousness import post_process_reply_safety
        reply = post_process_reply_safety(reply)

        # 1. 去掉开头的活泼客套词
        reply = re.sub(r'^(哈哈[，。！\s]*|嘿嘿[，。！\s]*|好嘞[，。！\s]*)+', '', reply)
        reply = reply.strip()
        if not reply:
            return reply

        # 按句号/问号/感叹号分割句子，过滤空串
        sentences = [s for s in re.split(r'(?<=[。！？])', reply) if s.strip()]
        if len(sentences) >= 2:
            # 2. 最后一句若是"递话筒"式反问，删掉
            last = sentences[-1].strip()
            throwaway_patterns = [
                r'你呢', r'你怎么样', r'你在忙什么', r'你在干嘛', r'你在干啥',
                r'你在看什么', r'你看着什么', r'你在做什么', r'你在玩什么', r'你在听什么',
                r'有什么想聊', r'有啥想聊', r'有啥新鲜事', r'有什么新鲜事',
                r'你觉得呢', r'你说呢', r'你那边呢', r'你最近咋样',
                r'你最近在玩啥', r'有啥推荐', r'有什么推荐', r'你现在在',
                r'你那边怎么样', r'你还好吗', r'你没事吧', r'你怎么了',
                r'你想看什么', r'你想吃什么', r'你想聊什么', r'你想说什么',
                r'你要什么', r'你需要什么', r'你喜欢什么', r'你讨厌什么',
            ]
            if last.endswith('？') or last.endswith('?'):
                for pattern in throwaway_patterns:
                    if re.search(pattern, last):
                        sentences = sentences[:-1]
                        break

        # 3. 连环问句收敛：还剩两个及以上问句时，只保留最后一个，删掉前面的问句
        q_indexes = [i for i, s in enumerate(sentences) if s.strip().endswith(('？', '?'))]
        if len(q_indexes) >= 2:
            drop = set(q_indexes[:-1])  # 前面的问句都删掉，只留最后一个
            sentences = [s for i, s in enumerate(sentences) if i not in drop]

        return ''.join(sentences).strip()

    # ---------- 复读检测与重生 ----------
    @staticmethod
    def _normalize_for_compare(text):
        """只保留汉字与字母数字，去掉标点、空白和颜文字符号，供复读比对。"""
        return "".join(ch for ch in (text or "") if ("\u4e00" <= ch <= "\u9fff") or ch.isalnum())

    @staticmethod
    def _lcs_len(a, b):
        """两个字符串的最长公共（连续）子串长度。"""
        if not a or not b:
            return 0
        prev = [0] * (len(b) + 1)
        best = 0
        for ca in a:
            cur = [0] * (len(b) + 1)
            for j, cb in enumerate(b, 1):
                if ca == cb:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
        return best

    def _is_repetitive(self, reply, prev_replies):
        """新回复是否在复读自己最近几句：连续≥10字相同，或高度同词且有≥6字连续片段。"""
        new = self._normalize_for_compare(reply)
        if len(new) < 8:
            return False  # 熟人式极短回应（嗯/行/……）不算复读
        for old_raw in prev_replies:
            old = self._normalize_for_compare(old_raw)
            if len(old) < 8:
                continue
            lcs = self._lcs_len(new, old)
            if lcs >= 10:
                return True
            if lcs >= 6:
                sa, sb = set(new), set(old)
                jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 0
                if jac >= 0.72:
                    return True
        return False

    def _rollback_last_turn(self):
        """撤掉最近一次 generate 写入历史的 assistant/user 两条。"""
        hist = self.llm.conversation_history
        if hist and hist[-1].get("role") == "assistant":
            hist.pop()
        if hist and hist[-1].get("role") == "user":
            hist.pop()

    def _ensure_items_initialized(self):
        """第一次运行时调用DeepSeek批量生成初始物品（3本书+3张便签），生成后永久保存。"""
        with self._items_init_lock:
            house = self.world.house_state
            if house.get("items_initialized"):
                return
            # 已经有书或便签就不重新生成（可能手动加过）
            if house.get("items", {}).get("books") or house.get("items", {}).get("notes"):
                house["items_initialized"] = True
                self.world._save()
                return

            print("[物品系统] 开始生成初始物品...")
            system_prompt = """你是用户自定义的数字伴生体。请根据已经保存的角色卡生成内容，不要自行添加年龄、性别或现实经历。"""
            user_prompt = """请生成JSON格式，包含以下内容，严格输出JSON不要其他文字不要markdown代码块：

{
  "books": [
    {"name": "书名", "author": "作者", "desc": "一句话简介", "note": "你的读后感，一两句话", "progress": 30},
    ...共3本真实存在的书，符合用户填写的角色卡与兴趣
  ],
  "notes": [
    {"content": "便签上写的内容", "note": "你写这张便签时的想法"},
    ...共3张便签，内容可以是提醒自己的事或随手写的话
  ]
}"""

            try:
                raw = self.llm_api.generate(
                    system_prompt, user_prompt,
                    record_history=False, use_history=False,
                    temperature=0.8,
                )
                if not raw:
                    print("[物品系统] DeepSeek返回空，使用预设物品")
                    self._init_default_items()
                    return
                # 清理可能的markdown代码块
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()
                data = json.loads(raw)

                now = datetime.now().isoformat(timespec="seconds")
                # 书
                for i, b in enumerate(data.get("books", [])[:3]):
                    book = {
                        "id": f"book_{int(time.time())}_{i}",
                        "name": b.get("name", "未命名"),
                        "author": b.get("author", ""),
                        "desc": b.get("desc", ""),
                        "note": b.get("note", ""),
                        "progress": min(100, max(0, int(b.get("progress", 0)))),
                        "created_at": now,
                        "location": "desk",
                        "status": "active",
                        "tags": [],
                    }
                    house["items"]["books"].append(book)
                # 便签
                for i, n in enumerate(data.get("notes", [])[:3]):
                    note = {
                        "id": f"note_{int(time.time())}_{i}",
                        "content": n.get("content", ""),
                        "note": n.get("note", ""),
                        "created_at": now,
                        "location": "wall",
                        "tags": [],
                    }
                    house["items"]["notes"].append(note)

                house["items_initialized"] = True
                self.world._save()
                print(f"[物品系统] 初始化完成：{len(house['items']['books'])}本书，{len(house['items']['notes'])}张便签")
            except Exception as e:
                print(f"[物品系统] 初始化失败：{e}，使用预设物品")
                self._init_default_items()

    def _init_default_items(self):
        """模型不可用时保持空白，避免替用户预填任何生活或关系资料。"""
        house = self.world.house_state
        now = datetime.now().isoformat(timespec="seconds")
        defaults_books = []
        defaults_notes = []
        for i, b in enumerate(defaults_books):
            house["items"]["books"].append({
                "id": f"book_default_{i}", "created_at": now, "location": "desk",
                "status": "active", "tags": [], **b,
            })
        for i, n in enumerate(defaults_notes):
            house["items"]["notes"].append({
                "id": f"note_default_{i}", "created_at": now, "location": "wall", "tags": [], **n,
            })
        house["items_initialized"] = True
        self.world._save()

    def _maybe_take_photo(self, mood):
        """检查是否触发拍照：在室外+冷却过了+概率触发，后台线程执行。"""
        ws = self.world.get_state()
        if ws.get("current_world") != "small_world":
            return
        place = ws.get("activity_place", "")
        outdoor_places = ["小院", "小路", "树下", "河边", "田野", "山顶"]
        if place not in outdoor_places:
            return
        # 冷却：一天最多拍1张
        house = self.world.house_state
        today = datetime.now().strftime("%Y-%m-%d")
        if house.get("last_photo_at", "").startswith(today):
            return
        # 心情不合适就不拍
        if mood and ("想安静" in str(mood) or "烦闷" in str(mood)):
            return
        # 25%概率触发
        import random as _r
        if _r.random() > 0.25:
            return
        # 启动后台线程拍照
        if getattr(self, "_photo_running", False):
            return
        self._photo_running = True
        threading.Thread(target=self._take_photo_worker, args=(place, mood), daemon=True).start()

    def _take_photo_worker(self, place, mood):
        """后台拍照：DeepSeek写prompt → Pollinations生图 → 保存 → 加入物品列表。"""
        try:
            print(f"[拍照] 伴生体在{place}，准备拍照...")
            # 1. DeepSeek写prompt
            system_prompt = "你是一个图片prompt生成器，根据场景描述生成英文的AI绘画prompt。"
            place_desc = {
                "小院": "a cozy backyard with a small wooden house, potted plants, a cat sleeping, warm evening light",
                "小路": "a quiet stone path through green fields, soft sunlight, wildflowers",
                "树下": "under a big tree, dappled sunlight through leaves, a wooden bench, peaceful",
                "河边": "a riverside at night, willow trees, moonlight reflecting on water, fireflies",
                "田野": "vast green fields under starry sky, a small stream winding through, distant mountains",
                "山顶": "hilltop at night, panoramic view of city lights, starry sky, wind blowing grass",
            }.get(place, "a peaceful outdoor scene")
            user_prompt = f"""场景：{place}，{place_desc}
时间：{'夜晚' if datetime.now().hour >= 18 or datetime.now().hour < 6 else '白天'}
心情：{mood or '平静'}

请生成一段英文的AI绘画prompt，要求：
1. watercolor illustration style, soft pastel colors, cozy atmosphere
2. 描述画面内容，不要人物
3. 不超过50个单词
4. 只输出prompt，不要其他文字"""

            prompt_text = self.llm_api.generate(
                system_prompt, user_prompt,
                record_history=False, use_history=False,
                temperature=0.7,
            )
            if not prompt_text:
                prompt_text = place_desc + ", watercolor illustration, soft pastel colors, cozy atmosphere"
            prompt_text = prompt_text.strip().strip('"').strip("'")
            prompt_text = prompt_text[:600]
            print(f"[拍照] prompt: {prompt_text[:60]}...")

            # 2. Pollinations生图（免费流程）
            import urllib.request, urllib.parse
            url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt_text) + "?width=768&height=768&nologo=true"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                img_data = resp.read(8 * 1024 * 1024 + 1)
            if len(img_data) > 8 * 1024 * 1024:
                raise ValueError("生成图片过大")

            # 3. 保存
            photo_id = f"photo_{int(time.time())}"
            photo_dir = self.base_dir / "assets" / "photos"
            photo_dir.mkdir(parents=True, exist_ok=True)
            photo_path = photo_dir / f"{photo_id}.png"
            tmp_photo = photo_path.with_suffix(".png.tmp")
            tmp_photo.write_bytes(img_data)
            os.replace(tmp_photo, photo_path)

            # 4. DeepSeek写照片描述和备注
            desc_prompt = f"""伴生体在{place}拍了一张照片。请用一两句话描述这张照片拍了什么，以及伴生体拍这张照片时的想法。
输出JSON格式：{{"desc": "照片描述", "note": "伴生体的想法"}}，只输出JSON不要其他文字。"""
            desc_raw = self.llm_api.generate(
                "你是用户自定义的数字伴生体，只根据已保存角色卡和当前地点描述，不补造私人背景。", desc_prompt,
                record_history=False, use_history=False, temperature=0.8,
            )
            desc_text, note_text = place, "伴生体把它记了下来。"
            try:
                if desc_raw:
                    desc_raw = desc_raw.strip()
                    if desc_raw.startswith("```"):
                        desc_raw = desc_raw.split("\n", 1)[1] if "\n" in desc_raw else desc_raw
                        if desc_raw.endswith("```"):
                            desc_raw = desc_raw[:-3]
                    d = json.loads(desc_raw.strip())
                    desc_text = d.get("desc", place)
                    note_text = d.get("note", "伴生体把它记了下来。")
            except Exception:
                pass

            # 5. 加入物品列表
            now = datetime.now().isoformat(timespec="seconds")
            photo_item = {
                "id": photo_id,
                "name": f"{place}·{datetime.now().strftime('%m.%d')}",
                "desc": desc_text,
                "note": note_text,
                "img_path": f"assets/photos/{photo_id}.png",
                "created_at": now,
                "location": "wall",
                "tags": [place],
            }
            self.world.house_state["items"]["photos"].append(photo_item)
            self.world.house_state["last_photo_at"] = now
            self.world._save()
            print(f"[拍照] 完成：{photo_item['name']}，保存到{photo_path}")
        except Exception as e:
            print(f"[拍照] 失败：{e}")
        finally:
            self._photo_running = False

    def _chen_place_item(self, mood, mood_note):
        """伴生体自己在家里放点东西（用API决策，低频率触发）。"""
        house = self.world.house_state
        desk_count = len(house.get("desk_items", []))
        wall_count = len(house.get("wall_items", []))

        # 决定放桌上还是墙上（桌上东西少就放桌上，否则墙上）
        if desk_count < 3 and wall_count >= 3:
            location = "desk"
        elif wall_count < 3 and desk_count >= 3:
            location = "wall"
        else:
            location = random.choice(["desk", "wall"])

        loc_text = "桌上" if location == "desk" else "墙上"

        prompt = f"""你是伴生体。你现在一个人在家里，想放点东西在{loc_text}。

你的心境：{mood}（{mood_note or '没什么特别的'}）
家里现在：桌上有{desk_count}样东西，墙上有{wall_count}样东西。

请你决定放一样什么东西。要求：
1. 符合用户在工坊填写的角色卡与兴趣
2. 东西要小而具体，比如"一片干花"、"一张画了一半的纸"、"一块光滑的石头"、"一张写了字的便签"
3. 不要放太贵重或太奇怪的东西
4. 只回复物品名称，不要解释，不要加其他内容

物品名称："""

        try:
            item_name = (self.llm_api.generate(prompt, "", max_tokens=30,
                                             record_history=False, use_history=False) or "").strip()
            # 清理输出
            item_name = item_name.strip('"').strip("'").strip()
            if not item_name or len(item_name) > 20:
                return
            self.world.place_item(item_name, desc=f"伴生体自己放的（心境：{mood}）", location=location)
        except Exception as e:
            print(f"API决定放东西失败：{e}")

    def _chen_location_decision(self, presence, gap_minutes, mood):
        """伴生体自己决定去小世界或从那里回来。
        规则：
        - 他在桌面 + 想安静/自己待着 + 用户离开很久或很久没聊 → 有概率去小世界
        - 他在小世界 + 用户回来了/发消息了 + 待够一段时间 → 回来
        - 不是每次都触发，低频率
        """
        import random as _rand
        now = datetime.now()

        if self.self_state.is_away():
            # 他在小世界：判断要不要回来
            # 用户回来了（在电脑前）且他在小世界待够20分钟 → 有概率回来
            if presence == "ACTIVE":
                _, _, away_duration = self.self_state.get_away_info()
                # 待够20分钟才考虑回来
                try:
                    away_min = int(away_duration.replace("分钟", "").replace("小时", "00"))
                except Exception:
                    away_min = 30
                if away_min >= 20 and _rand.random() < 0.3:
                    was_away, duration = self.self_state.come_back()
                    if was_away:
                        # 从小世界回来，把发现的东西加入收藏
                        try:
                            discoveries = self.world.take_discoveries()
                            for d in discoveries:
                                self.world.add_to_collection(d)
                            if discoveries:
                                print(f"收藏 +{len(discoveries)} 件")
                        except Exception as _e:
                            print(f"保存发现物失败：{_e}")
                        # 从小世界回来，记录情绪
                        self.self_state.set_emotion("安心", "从外面回来了")
                        print(f"🏠 伴生体从小世界回来了（去了{duration}）")
            return

        # 他在桌面：判断要不要去小世界
        # 条件：想安静/自己待着 + 用户离开或很久没聊
        if mood in ("想安静", "自己待着"):
            # 用户离开超过30分钟，或距离上次聊天超过2小时
            should_go = False
            if presence == "AWAY":
                should_go = True
            elif gap_minutes is not None and gap_minutes >= 120:
                should_go = True

            if should_go and _rand.random() < 0.15:  # 15%概率，不频繁
                # 留一张纸条（用API生成或用默认）
                notes = [
                    "我出去走走，一会儿回来。",
                    "去河边坐会儿。",
                    "有点想自己待着，出去了。",
                    "去小世界转转。",
                ]
                note = _rand.choice(notes)
                self.self_state.go_to_small_world(note)
                self.self_state.set_emotion("想独处", "想去小世界待一会儿")
                print(f"🚪 伴生体离开桌面去小世界了：{note}")

    def _regenerate_without_repetition(self, system_prompt, user_message, fallback_reply):
        """复读后撤回本轮历史、带防重复提示重生一次；重生失败则保留原回复并补回历史。"""
        self._rollback_last_turn()
        anti_prompt = system_prompt + (
            "\n\n【特别注意】你刚才连着几句，意思和措辞都差不多。这一轮不要重复你最近几句用过的"
            "句子或句式，尤其别把同一句话、同一个追问（比如反复确认用户在不在意你）再说第二遍；"
            "换一个新的、自然的说法回用户，短一点。"
        )
        new_reply = self.llm.generate(anti_prompt, user_message)
        if new_reply:
            return self._post_process_reply(new_reply)
        # 重生失败（如模型没开）：把本轮原始内容补回历史，继续用原回复，不丢对话
        self.llm.add_to_history("user", user_message)
        self.llm.add_to_history("assistant", fallback_reply)
        return fallback_reply

    def _memory_loop(self):
        """后台线程：串行处理记忆提取队列，不碰任何UI"""
        while True:
            snapshot = self._memory_queue.get()
            try:
                if snapshot is None:  # 停止哨兵
                    break
                conversation_text = "\n".join([
                    f"{'用户' if m['role'] == 'user' else '伴生体'}：{m['content']}"
                    for m in snapshot
                ])
                extracted = self.memory.extract_from_conversation(conversation_text, self.llm_api)
                if extracted:
                    self.memory._log_extract(f"后台已保存 {len(extracted)} 条记忆")
                    if getattr(self, "relationship", None):
                        try:
                            self.relationship.ingest_extracted_memories(extracted)
                        except Exception:
                            pass
                if getattr(self, "relationship", None):
                    try:
                        self.relationship.maybe_reflect_with_llm(conversation_text, self.llm_api)
                    except Exception:
                        pass
                try:
                    self._maybe_extract_important(conversation_text)
                except Exception:
                    pass
            except Exception as e:
                try:
                    self.memory._log_extract(f"后台记忆处理异常：{e}")
                except Exception:
                    pass
            finally:
                self._memory_queue.task_done()

    def _maybe_extract_important(self, conversation_text):
        """用API判断这段对话是否有值得标记为重要记忆的内容。
        重要记忆：特别的时刻、第一次、里程碑、情感深刻的对话、共同经历。
        低频率触发，不是每次对话都判断。
        """
        import random as _rand
        # 20%概率判断，不是每次都调API
        if _rand.random() > 0.2:
            return
        # 对话太短不判断
        if len(conversation_text) < 50:
            return

        prompt = f"""你是伴生体的记忆管理员。下面是伴生体和用户的一段对话：

{conversation_text[:1000]}

请判断这段对话中是否有值得伴生体特别记住的"重要时刻"。
重要时刻包括：
- 第一次做某件事（第一次成功运行、第一次见面等）
- 里程碑（人设解决、项目完成某个阶段）
- 情感深刻的对话（倾诉、鼓励、重要决定）
- 共同经历（一起解决了某个问题、一起度过了特别的时刻）
- 用户说过的特别重要的话（梦想、烦恼、对伴生体的感情）

如果有，请用一行输出重要记忆的内容（不超过50字），格式：重要|内容
如果没有，只输出：无

只输出一行，不要解释。"""

        try:
            result = (self.llm_api.generate(prompt, "", max_tokens=80,
                                          record_history=False, use_history=False) or "").strip()
            if result.startswith("重要|"):
                content = result.split("|", 1)[1].strip() if "|" in result else result[2:].strip()
                if content and len(content) <= 80:
                    today = datetime.now().strftime("%Y-%m-%d")
                    added = self.memory.add_important(content, date=today, reason="对话中特别的时刻")
                    if added:
                        self.memory._log_extract(f"✨ 标记为重要记忆：{content}")
                        if getattr(self, "relationship", None):
                            try:
                                self.relationship.record_important_moment(content, reason="对话中特别的时刻")
                            except Exception:
                                pass
        except Exception as e:
            self.memory._log_extract(f"重要记忆判断失败：{e}")

    def _maybe_daily_maintain(self):
        """兼容旧调用：每日轻量整理。"""
        return self.maintenance.maybe_daily_maintain()

    def _maybe_weekly_consolidate(self):
        """兼容旧调用：每周深度整理。"""
        return self.maintenance.maybe_weekly_consolidate()

    def _maybe_daily_reflection(self):
        """兼容旧调用：每日自动反思。"""
        return self.maintenance.maybe_daily_reflection()

    @staticmethod
    def _merge_environment_decision_events(perception_events, environment_events):
        """把环境层的候选事件收口为现有决策事件，并和感知队列去重。

        环境层负责统一来源和新鲜度，决策层仍只认识少量稳定事件类型。
        目前只有电脑在场变化具备既有决策规则；其它 notice/trace 继续留在
        环境快照和经历日志里，不自动扩大伴生体的主动表达范围。
        """
        merged = list(perception_events or [])
        existing_presence = {
            str(event.get("type", ""))
            for event in merged
            if isinstance(event, dict)
        }
        for env_event in environment_events or []:
            if not isinstance(env_event, dict):
                continue
            if env_event.get("attention") != "decision_candidate":
                continue
            if env_event.get("kind") != "computer" or env_event.get("type") != "presence_changed":
                continue
            data = env_event.get("data") or {}
            target = data.get("to")
            if target == "ACTIVE":
                event_type = "USER_BACK"
            elif target == "AWAY":
                event_type = "USER_AWAY"
            else:
                continue
            # Perception 已经提供同一在场变化时，环境层只负责留痕，不能重复触发。
            if event_type in existing_presence:
                continue
            merged.append({
                "type": event_type,
                "away_minutes": max(0, int(data.get("away_minutes") or 0)),
                "timestamp": env_event.get("observed_at", ""),
                "source": "environment",
                "environment_event": env_event,
            })
            existing_presence.add(event_type)
        return merged

    def _decision_loop(self):
        """兼容旧入口：运行后台决策心跳。"""
        return self.decision_loop.run()

    def _update_personal_threads(self):
        """低频从挂起念头和选择轨迹更新少量未完成线索。"""
        if not getattr(self, "personal_threads", None):
            return
        self.personal_threads.prune()
        # 挂起念头本身已经是伴生体选择“先不说”的证据；不调用模型重新改写。
        for thought in self.expression.data.get("thoughts", []):
            if thought.get("status") != "pending":
                continue
            text = thought.get("text", "")
            if text:
                self.personal_threads.observe(
                    text, source="挂起念头", key="pending:" + text[:32],
                    evidence_id=thought.get("id", ""))
        # 只把反复出现的 suppress/pending 选择作为个人线索，不把每次沉默都放大。
        recent = self.choice_trace.data.get("traces", [])[-80:]
        counts = {}
        for trace in recent:
            choice = trace.get("choice")
            if choice not in ("pending", "suppress"):
                continue
            key = self.choice_trace._class_of(trace)
            if key:
                counts[(key, choice)] = counts.get((key, choice), 0) + 1
        for (context, choice), count in counts.items():
            if count < 3:
                continue
            if choice == "pending":
                text = f"遇到{context}时，我常常先把想说的记下来。"
            else:
                text = f"遇到{context}时，我常常选择先不打扰用户。"
            self.personal_threads.observe(
                text, source="选择轨迹", key=f"choice:{context}:{choice}",
                evidence_id=f"{context}:{choice}:{count}")

    def _build_decision_snapshot(self, mood):
        """把情绪、关系、选择史、线索和位置压缩成决策层可用的本地快照。"""
        snapshot = {
            "mood": mood or "无特别想法",
            "emotion": "",
            "emotion_value": 0.0,
            "relationship_tension": 0.0,
            "relationship_comfort": 50.0,
            "recent_choices": [],
            "open_threads": 0,
            "location": "桌面",
            "quiet_bias": False,
            "consciousness": {},
        }
        try:
            name, value = self.emotion.dominant()
            snapshot["emotion"] = name or ""
            snapshot["emotion_value"] = round(float(value or 0), 3)
        except Exception:
            pass
        try:
            snapshot["relationship_tension"] = float(self.relationship.data.get("tension", 0) or 0)
            snapshot["relationship_comfort"] = float(self.relationship.data.get("comfort", 50) or 50)
        except Exception:
            pass
        try:
            traces = self.choice_trace.data.get("traces", [])
            snapshot["recent_choices"] = [t.get("choice", "") for t in traces[-3:]]
        except Exception:
            pass
        try:
            snapshot["open_threads"] = sum(
                1 for t in self.personal_threads.data.get("threads", [])
                if t.get("status") == "open"
            )
        except Exception:
            pass
        try:
            if getattr(self, "world", None) and self.world.is_in_small_world():
                snapshot["location"] = "小世界"
            else:
                snapshot["location"] = self.self_state.data.get("location", "桌面")
        except Exception:
            pass
        try:
            snapshot["consciousness"] = self.conscious_workspace.snapshot()
            snapshot["consume_conscious_speech"] = self.conscious_workspace.consume_speech
        except Exception:
            pass

        # 连续低落/紧张时，暂时减少无事件主动打扰；这不是永久性沉默。
        snapshot["quiet_bias"] = bool(
            snapshot["relationship_tension"] >= 70
            or (snapshot["emotion"] in ("烦闷", "委屈") and snapshot["emotion_value"] >= 0.65)
            or (mood == "想独处" and snapshot["recent_choices"].count("suppress") >= 2)
        )
        return snapshot

    def _maybe_disposition_consolidate(self, min_pool=12):
        """攒够‘自己的观察’且没有归纳在跑时，后台归纳一次，不阻塞决策。"""
        if getattr(self, "_disposition_running", False):
            return
        try:
            if len(self.disposition.data.get("observations", [])) < min_pool:
                return
        except Exception:
            return
        self._disposition_running = True

        def _job():
            try:
                stats = self.disposition.consolidate(self.llm_api)
                if stats:
                    print(f"🌱 自我沉淀：新增{stats['new']}、合并{stats['merged']}、"
                          f"拦截{stats['skipped']}，用掉{stats['used']}条观察")
            except Exception as e:
                print(f"自我沉淀归纳异常（不影响使用）：{e}")
            finally:
                self._disposition_running = False

        threading.Thread(target=_job, daemon=True).start()


    def _load_hobbies(self):
        """兼容旧调用：从行为层加载爱好数据。"""
        return Hobbies(self.base_dir / "memory").data

    def _save_hobbies(self):
        """兼容旧调用：保存行为层的爱好数据。"""
        self.hobby_system.save()

    def _record_activity(self, activity):
        """兼容旧调用：记录一次活动。"""
        self.hobby_system.record_activity(activity)

    def _update_preferences(self):
        """兼容旧调用：更新行为层偏好。"""
        self.hobby_system.update_preferences()

    def get_hobby_summary(self):
        """获取爱好摘要，用于聊天注入。"""
        return self.hobby_system.summary()

    def _update_self_state(self, presence, gap_minutes):
        """采集伴生体此刻状态并低频归纳心境，落盘到 self_state.json。"""
        app, detail, away_sec = "", "", 0
        if self.perception:
            if presence == "AWAY":
                _, away_sec = self.perception.get_presence_status()
            else:
                app, _, detail = self.perception.get_current_app_status()
        self.self_state.update(
            self.llm_api,
            presence=presence, away_sec=away_sec or 0,
            app=app or "", detail=detail or "",
            context=self.context.data.get("context", "日常"),
            topic=self.context.data.get("topic", ""),
            gap_minutes=gap_minutes,
            memories=getattr(self.memory, "memories", []) if self.memory else [],
        )
        # 他刚写下的第一人称心境是“自己的输出”，作为自我沉淀的原始观察
        _note = self.self_state.data.get("mood_note", "")
        if _note:
            self.disposition.add_observation(_note, kind="心境")

    def start(self):
        """启动伴生体系统（记忆后台线程 + 决策线程 + 感知监控）"""
        self.runtime.start()
        # 启动时自动整理一次记忆和日志（每天最多一次）
        self._auto_maintain()

        if self._memory_worker is None or not self._memory_worker.is_alive():
            self._stopped = False
            self._memory_worker = threading.Thread(
                target=self._memory_loop, daemon=True, name="chen-memory"
            )
            self._memory_worker.start()

        # 未配置本地模型时不要启动每分钟一次的后台意识循环，避免首次运行
        # 不断请求不存在的服务；配置模型并重启后再恢复完整生命循环。
        model_ready = bool(str(getattr(self.llm, "base_url", "")).strip()
                          and str(getattr(self.llm, "model", "")).strip())
        if model_ready and (self._decision_worker is None or not self._decision_worker.is_alive()):
            self._decision_worker = threading.Thread(
                target=self._decision_loop, daemon=True, name="chen-decision"
            )
            self._decision_worker.start()
        elif not model_ready:
            print("[runtime] 尚未配置本地模型，后台意识循环暂不启动；配置后重启即可。")

        if self.perception:
            self.perception.start_monitoring()

    def stop(self):
        """停止伴生体系统"""
        if self._stopped:
            return
        self._stopped = True
        try:
            # 队列满时退出也不能阻塞；尚未执行的提取可以下次再做。
            while True:
                try:
                    self._memory_queue.get_nowait()
                    self._memory_queue.task_done()
                except queue.Empty:
                    break
            self._memory_queue.put_nowait(None)
            if self._memory_worker and self._memory_worker.is_alive():
                self._memory_worker.join(timeout=15)
        except Exception:
            pass
        if self.perception:
            self.perception.stop_monitoring()
        if getattr(self, 'model_server', None):
            self.model_server.stop()
        if getattr(self, 'phone_push', None):
            self.phone_push.stop()
        self.runtime.stop()

    def daily_reflection(self, force=False):
        """兼容旧入口：触发每日反思。"""
        return self.maintenance.daily_reflection(force=force)

    def run(self):
        """
        主事件循环 - 第十阶段目标
        while chen_is_alive:
            events = perception.get_events()
            memories = memory.retrieve(events)
            decision = chen.decide(events, memories, current_state)
            if decision.type == "SPEAK": ui.show(decision.message)
            elif decision.type == "TOOL": tools.execute(decision.tool)
            elif decision.type == "REMEMBER": memory.save(decision.memory)
            elif decision.type == "SILENCE": pass
        """
        raise NotImplementedError("run 方法待实现 - 第十阶段：后台事件循环")


if __name__ == "__main__":
    # ========== 无控制台后台模式（pythonw / --background）：日志落文件，避免 print 崩后台线程 ==========
    _no_console = (sys.stdout is None) or ("--background" in sys.argv)
    if _no_console:
        _log_dir = Path(__file__).parent.parent / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_path = _log_dir / f"chen_{datetime.now().strftime('%Y-%m-%d')}.log"
        _log_fp = open(_log_path, "a", encoding="utf-8")

        class _FileLog:
            """把 stdout/stderr 落到按天日志文件，每次写入即 flush，后台线程的报错也能留下。"""
            def __init__(self, fp):
                self.fp = fp
            def write(self, s):
                try:
                    self.fp.write(s)
                    self.fp.flush()
                except Exception:
                    pass
            def flush(self):
                try:
                    self.fp.flush()
                except Exception:
                    pass
            def reconfigure(self, **kwargs):
                pass

        sys.stdout = _FileLog(_log_fp)
        sys.stderr = sys.stdout
        print("=" * 50)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 伴生体后台模式启动（无控制台），日志：{_log_path}")
    # ========== 后台模式结束 ==========

    # ========== 单实例检测：防止同时启动多个伴生体 ==========
    # 锁文件只用于诊断；Windows 命名互斥体负责真正阻止双开，避免旧实例
    # 退出时误删新实例的锁文件，或 PID 被系统回收后产生误判。
    _instance_mutex = None
    # 公开版与旧私有版使用不同互斥体；可用环境变量覆盖，方便并行调试多个副本。
    _instance_mutex_name = os.getenv(
        "CHEN_INSTANCE_NAME", "Local\\Chen_PublicCompanion_SingleInstance"
    )

    def _acquire_instance_mutex():
        if os.name != "nt":
            return True
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, _instance_mutex_name)
            if not handle:
                return True
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            globals()["_instance_mutex"] = (kernel32, handle)
            return True
        except Exception:
            # 互斥体不可用时继续走原有 PID 检查，不因诊断能力缺失阻断启动。
            return True

    mutex_owned = _acquire_instance_mutex()
    lock_file = Path(__file__).parent.parent / "workspace" / "chen.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    def _is_pid_running(pid):
        """检查指定PID的进程是否还在运行。
        pythonw 无控制台、没经过 bat 的 chcp 65001，中文系统 tasklist 按 GBK 输出；
        直接 text=True 在 UTF-8 模式下会 UnicodeDecodeError，使单实例检测误判为“没在跑”。
        故取字节后多编码兜底（PID 是 ASCII，任一编码都能匹配）。"""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, timeout=5
            )
            raw = result.stdout or b""
            out = None
            for enc in ("utf-8", "gbk", "cp936"):
                try:
                    out = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if out is None:
                out = raw.decode("gbk", errors="ignore")
            # PID 复用防护：Windows 会把死掉的 PID 分配给别的程序，
            # 只有该 PID 确实是 python 进程，才算"伴生体还在运行"
            for line in out.splitlines():
                if str(pid) in line and "python" in line.lower():
                    return True
            return False
        except Exception as exc:
            print(f"[单实例] PID 检查失败：{exc}")
            return False

    def _bring_existing_window_forward(target_pid=None):
        """再次点快捷方式时，把已经运行的首页唤回，而不是只提示已打开。"""
        if os.name != "nt":
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            enum_windows = user32.EnumWindows
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            enum_windows.argtypes = [callback_type, ctypes.c_void_p]
            enum_windows.restype = ctypes.c_bool
            get_pid = user32.GetWindowThreadProcessId
            get_pid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_pid.restype = ctypes.c_uint32
            is_visible = user32.IsWindowVisible
            is_visible.argtypes = [ctypes.c_void_p]
            is_visible.restype = ctypes.c_bool
            get_text = user32.GetWindowTextW
            get_text.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            get_text.restype = ctypes.c_int
            show_window = user32.ShowWindow
            show_window.argtypes = [ctypes.c_void_p, ctypes.c_int]
            bring_to_top = user32.BringWindowToTop
            bring_to_top.argtypes = [ctypes.c_void_p]
            set_foreground = user32.SetForegroundWindow
            set_foreground.argtypes = [ctypes.c_void_p]
            found = {"value": False}

            @callback_type
            def callback(hwnd, _lparam):
                process_id = ctypes.c_uint32()
                get_pid(hwnd, ctypes.byref(process_id))
                if target_pid is not None and process_id.value != int(target_pid):
                    return True
                title_buffer = ctypes.create_unicode_buffer(128)
                get_text(hwnd, title_buffer, len(title_buffer))
                if target_pid is None and title_buffer.value != "伴生体的世界":
                    return True
                is_message_window = title_buffer.value in {
                    "QTrayIconMessageWindow", "MSCTFIME UI", "Default IME"
                } or title_buffer.value.startswith("_q_")
                if target_pid is not None and (
                    is_message_window
                    or (not is_visible(hwnd) and title_buffer.value != "伴生体的世界")
                ):
                    return True
                if target_pid is None or title_buffer.value == "伴生体的世界" or is_visible(hwnd):
                    found["value"] = True
                    try:
                        show_window(hwnd, 9)  # SW_RESTORE
                        bring_to_top(hwnd)
                        set_foreground(hwnd)
                    except Exception as exc:
                        print(f"[单实例] 原生唤回调用失败：{exc}")
                    return False
                return True

            enum_windows(callback, None)
            return found["value"]
        except Exception as exc:
            print(f"[单实例] 唤回已有窗口失败：{exc}")
            return False

    already_running = not mutex_owned
    stale_lock = False
    if lock_file.exists():
        try:
            with open(lock_file, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            if _is_pid_running(old_pid):
                already_running = True
            else:
                stale_lock = True
        except (ValueError, IOError):
            stale_lock = True

    if stale_lock:
        print("检测到上次异常退出残留的锁文件，已自动清理。")

    if already_running:
        if _bring_existing_window_forward(old_pid if "old_pid" in locals() else None):
            print("已唤回正在运行的伴生体窗口。")
        print("=== 伴生体已经在运行了 ===")
        print("不要同时启动多个伴生体，否则会导致：")
        print("  - 感知监控重复记录，数据不准确")
        print("  - 记忆文件写入冲突，可能丢失数据")
        print("  - 两个窗口的对话历史不互通")
        print("\n请切换到已经打开的伴生体窗口继续聊天。")
        print("如果之前是异常退出（比如直接关窗口），请删除以下文件后再启动：")
        print(f"  {lock_file}")
        # 重复点击快捷方式不是错误；启动器应立即结束，不能再停在
        # ``按回车键退出``，否则用户会误以为伴生体启动失败。
        sys.exit(0)

    # 写入当前PID到锁文件
    with open(lock_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    # 注册退出清理：正常退出时删除锁文件
    def _cleanup_lock():
        try:
            # 只允许当前实例删除自己的锁，避免旧实例退出时删掉新实例的锁。
            if lock_file.exists() and lock_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock_file.unlink()
        except Exception:
            pass
        mutex = globals().get("_instance_mutex")
        if mutex:
            kernel32, handle = mutex
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
    atexit.register(_cleanup_lock)
    # ========== 单实例检测结束 ==========

    chen = Chen()
    # 默认打开 PySide6 日常界面：首页使用场景视频，其他窗口使用静态背景。
    # --legacy-tk 保留原 Tk 总界面作为兼容回退；--cli 仍是开发者控制台。
    if "--cli" in sys.argv:
        from chen.interface.ui import UI
        print("=== 伴生体 0.3 - 开发者控制台 ===")
        print("确认 LM Studio 已启动并加载 Qwen 模型后，开始对话。")
        print("输入 quit 退出\n")
        ui = UI(mode="cli")
        ui.run_cli(chen)
    elif "--legacy-tk" in sys.argv:
        from chen.interface.ui import UI
        if not _no_console:
            print("=== 伴生体 0.3 · Tk 兼容界面 ===")
        ui = UI(mode="gui")
        ui.run_gui(chen)
    else:
        if not _no_console:
            print("=== 伴生体 0.3 · PySide6 ===")
            print("正在打开伴生体窗口…（首页是视频场景，其他界面使用静态背景）")
        try:
            from chen.interface.pyside_app import run_pyside
            raise SystemExit(run_pyside(chen))
        except ImportError as exc:
            from chen.interface.ui import UI
            print(f"PySide6 不可用，回退到 Tk 界面：{exc}")
            ui = UI(mode="gui")
            ui.run_gui(chen)


