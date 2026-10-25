"""
LLM 模块 - 伴生体的大脑
负责与本地大语言模型（Qwen via LM Studio）通信
第一阶段重点：先让聊天稳定
"""

import json
import re
import threading
import requests


class LLM:
    """大语言模型接口"""

    def __init__(self, base_url="http://localhost:1234/v1", model="qwen", history_messages=12, disable_thinking=True, api_key=None):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key  # 云端API时需要，本地llama-server留空
        self.temperature = 0.7
        self.max_tokens = 500
        # 反重复采样，治“一句话卡带、反复说同一句/同一个问题”：
        # frequency_penalty 按 token 已出现次数累加惩罚（专治复读），
        # presence_penalty 只要出现过就轻微惩罚、鼓励引入新词。
        # 本地 llama.cpp / LM Studio 认这两个 OpenAI 标准字段；0 表示关闭。取值保守，过高会让中文用词生硬。
        self.frequency_penalty = 0.5
        self.presence_penalty = 0.2
        self.conversation_history = []
        # 对话历史携带条数（消息条数，12条=6轮），从config读取
        self.history_messages = history_messages
        # Qwen3 默认会先输出一段思考链，闲聊和记忆提取都不需要，默认关闭（可由config覆盖）
        self.disable_thinking = disable_thinking
        # 模型请求锁：本地显卡一次只跑一个推理，聊天/记忆提取/反思共用一把锁串行，
        # 避免后台记忆提取和前台聊天同时打给 LM Studio 互相拖慢或报错
        self._req_lock = threading.Lock()
        self.policy = None

    def _build_messages(self, system_prompt, user_message, use_history=True):
        """构建发送给 LLM 的消息列表"""
        messages = [{"role": "system", "content": system_prompt}]
        # 加入历史对话（控制长度），内部调用如记忆提取时不需要历史
        if use_history and self.history_messages > 0:
            messages.extend(self.conversation_history[-self.history_messages:])
        content = user_message
        # Qwen3 官方方式：在最后一条用户消息末尾加 /no_think，关闭本轮思考（只影响发给模型的内容，不污染界面和历史）
        if self.disable_thinking and "/no_think" not in content:
            content = content.rstrip() + " /no_think"
        messages.append({"role": "user", "content": content})
        return messages

    def generate(self, system_prompt, user_message, record_history=True, use_history=True,
                 temperature=None, max_tokens=None):
        """
        调用 LLM 生成回复
        record_history: 是否把这轮对话加入历史。记忆提取等内部调用应设为False。
        use_history: 是否把历史对话传给模型。记忆提取时应设为False，避免干扰JSON输出。
        temperature: 本轮临时温度（做梦等场景用）；None 则用配置默认。
        返回：回复文本（失败时返回 None）
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
        }
        # 双保险：从 chat template 层关闭 Qwen3 思考（LM Studio / llama.cpp 识别此参数）
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        # 整次请求持锁：保证同一时间只有一个请求在占用模型
        with self._req_lock:
            if self.api_key and self.policy:
                if not self.policy.allow_external_call():
                    print("[API] 已达到今日调用上限，跳过后台请求")
                    return None
                payload["max_tokens"] = self.policy.clamp_tokens(
                    payload["max_tokens"], self.policy.limits.max_external_tokens_per_call)
            # 等待锁之后再取历史，避免并发请求用到同一份过期快照。
            payload["messages"] = self._build_messages(system_prompt, user_message, use_history)
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
                reply = resp.json()["choices"][0]["message"]["content"]
                reply = self._strip_thinking(reply)
                # 成功后自动记录到历史（内部调用如记忆提取时不记录）
                if record_history:
                    self.add_to_history("user", user_message)
                    self.add_to_history("assistant", reply)
                return reply
            except requests.exceptions.ConnectionError:
                if self.api_key:
                    print("[API] 连不上云端API，请检查网络")
                else:
                    print("[LLM] 连不上本地模型，请确认 llama-server 已启动")
                return None
            except Exception as e:
                # 后台模型失败必须静默回退；日志使用控制台安全字符，避免
                # Windows GBK 再次编码失败，掩盖真正的网络/服务错误。
                print(f"[LLM] 调用失败：{e}")
                return None

    def clear_history(self):
        """清空对话历史（保留history_messages设置）"""
        self.conversation_history = []

    def add_to_history(self, role, content):
        """添加一条对话到历史"""
        self.conversation_history.append({"role": role, "content": content})
        cap = max(24, self.history_messages)
        if len(self.conversation_history) > cap:
            del self.conversation_history[:-cap]

    @staticmethod
    def _strip_thinking(text):
        """剥掉 Qwen3 漏网的思考链，只留正式回复"""
        if not text:
            return text
        # 成对的 <think>...</think> 整块去掉（跨行）
        text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # 残留的单个开/闭标签也去掉
        text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
        return text.strip()
