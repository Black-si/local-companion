# -*- coding: utf-8 -*-
"""公开版最小对话运行器。"""
from llm_client import LLMClient


class CompanionRuntime:
    def __init__(self, profile, system_config):
        self.profile = profile or {}
        self.system_config = system_config or {}
        self.client = LLMClient(self.system_config.get("llm", {}))
        self.history = []

    def system_prompt(self):
        name = self.profile.get("name", "你的伴生体")
        relationship = self.profile.get("relationship", "由用户定义")
        personality = self.profile.get("personality", "")
        speech = self.profile.get("speech_style", "")
        boundaries = self.profile.get("boundaries", "")
        return (f"你是{name}，与用户的关系是：{relationship}。\n"
                f"性格：{personality}\n说话方式：{speech}\n边界：{boundaries}\n"
                "保持自然交流，不要声称自己能读取用户没有提供的数据。")

    def chat(self, text):
        text = str(text or "").strip()
        if not text:
            return ""
        messages = [{"role": "system", "content": self.system_prompt()}]
        messages.extend(self.history[-12:])
        messages.append({"role": "user", "content": text})
        reply = self.client.generate(messages)
        self.history.extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ])
        self.history = self.history[-24:]
        return reply
