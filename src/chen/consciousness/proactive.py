"""伴生体主动表达的延迟、去重和投递协调。"""

from datetime import datetime, timedelta
from difflib import SequenceMatcher
import queue


class ProactiveSpeech:
    """依赖 Chen 的公开能力完成主动表达，不拥有独立状态文件。"""

    def __init__(self, chen):
        self.chen = chen

    @staticmethod
    def salience_of(reason):
        """判定开口意图显著度。"""
        reason = reason or ""
        if any(k in reason for k in ("关怀", "深夜", "饭点", "提醒", "休息", "回来", "USER_BACK")):
            return "strong"
        if any(k in reason for k in ("翻念头", "APP_FOCUS", "软件", "打开")):
            return "weak"
        return "normal"

    def deliver(self, text, reason=""):
        """真正投递主动表达，并沉淀到意识、聊天和接口队列。"""
        chen = self.chen
        print(f"\n[决策触发] 伴生体主动开口 ({reason})：{text}")
        chen.llm.add_to_history("assistant", text)
        chen.record_chat("chen", text)
        chen.self_state.record_proactive(text, reason or "")
        chen.disposition.add_observation(text, kind="主动开口")
        try:
            chen.proactive_speech_queue.put_nowait(text)
        except queue.Full:
            # 主循环暂时忙时不阻塞决策线程。
            pass
        try:
            if chen.phone_push.enabled and chen.perception and \
                    chen.perception.get_presence_status()[0] == "AWAY":
                chen.phone_push.push(text)
        except Exception:
            pass
        chen._proactive_history.append((datetime.now().isoformat(timespec="seconds"), text))
        del chen._proactive_history[:-20]

    def tick_pending(self, presence, dec_app):
        """检查到期意图，合适时投递，不合适则延迟或丢弃。"""
        chen = self.chen
        if not chen._pending_intents:
            return
        now = datetime.now()
        for intent in list(chen._pending_intents):
            if (now - intent["deliver_at"]).total_seconds() > 1800:
                chen._pending_intents.remove(intent)
                print(f"[意图搁置] 压太久了，没说：{intent['text'][:30]}")
                continue
            if now < intent["deliver_at"]:
                continue
            try:
                gap = chen._minutes_since_last_chat()
                if gap is not None and gap < 2:
                    if intent["salience"] == "weak":
                        chen._pending_intents.remove(intent)
                        print(f"[意图搁置] 用户正在聊，轻的算了：{intent['text'][:30]}")
                        continue
                    intent["deliver_at"] = now + timedelta(seconds=90)
                    continue
            except Exception:
                pass
            fun_apps = ("B站", "星穹铁道", "明日方舟", "Steam", "我的世界", "星露谷物语", "泰拉瑞亚")
            if intent["salience"] == "weak" and presence == "ACTIVE" and any(
                    app in (dec_app or "") for app in fun_apps):
                intent["deliver_at"] = now + timedelta(minutes=3)
                continue
            try:
                for _timestamp, old_text in chen._proactive_history[-5:]:
                    if SequenceMatcher(None, intent["text"], old_text).ratio() > 0.85:
                        chen._pending_intents.remove(intent)
                        print(f"[意图搁置] 跟刚说过的太像：{intent['text'][:30]}")
                        break
                else:
                    chen._pending_intents.remove(intent)
                    self.deliver(intent["text"], intent["reason"] + "（酝酿后）")
            except Exception:
                chen._pending_intents.remove(intent)
                self.deliver(intent["text"], intent["reason"] + "（酝酿后）")
