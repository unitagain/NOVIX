# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  LLM提供商抽象基类 - 统一接口定义
  Base LLM Provider Abstract Class - Defines unified interface for all LLM providers
  to support OpenAI, Anthropic, DeepSeek, and custom LLM backends.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator


def normalize_tool_calls(message: Any) -> Optional[List[Dict[str, Any]]]:
    """从 OpenAI 风格 message 提取 tool_calls，统一为可序列化 dict 列表；无则返回 None。
    Extract tool_calls from an OpenAI-style message into plain dicts; returns None if absent."""
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return None
    calls: List[Dict[str, Any]] = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        calls.append(
            {
                "id": getattr(tc, "id", None),
                "type": getattr(tc, "type", "function"),
                "name": getattr(fn, "name", None) if fn else None,
                "arguments": getattr(fn, "arguments", None) if fn else None,
            }
        )
    return calls


class BaseLLMProvider(ABC):
    """
    大模型提供商抽象基类 / Abstract base class for LLM providers

    Defines the interface that all LLM provider implementations must follow.
    Supports both synchronous chat and streaming modes.

    Attributes:
        api_key (str): API密钥 / API key for authentication.
        model (str): 模型名称 / Model name/identifier.
        max_tokens (int): 最大生成token数 / Maximum tokens to generate.
        temperature (float): 生成温度 / Sampling temperature (0.0-1.0).
    """

    def __init__(self, api_key: str, model: str, max_tokens: int = 8000, temperature: float = 0.7):
        """
        初始化提供商 / Initialize provider

        Args:
            api_key: API密钥 / API key for the provider.
            model: 模型名称 / Model name.
            max_tokens: 最大生成token数，默认8000 / Maximum tokens to generate (default 8000).
            temperature: 生成温度，默认0.7 / Temperature for generation (default 0.7).
        """
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[Dict[str, Any]] = None,
        thinking: Optional[Any] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        发送聊天请求到LLM提供商 / Send chat request to LLM provider

        Sends a list of messages and returns the model's response with usage statistics.

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
                     Message list in format [{"role": "user", "content": "..."}]
            temperature: 覆盖默认温度 / Override temperature setting.
            max_tokens: 覆盖默认token限制 / Override max tokens setting.

        Returns:
            响应字典包含 'content', 'usage' 等字段 / Response dict with 'content', 'usage', etc.
            Expected keys:
            - content: 生成的文本 / Generated text
            - usage: token使用情况 / Token usage dict
            - model: 模型名称 / Model name
            - finish_reason: 完成原因 / Completion reason
        """
        pass

    async def stream_chat(
        self, messages: List[Dict[str, str]], temperature: Optional[float] = None, max_tokens: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """
        流式输出聊天响应，逐 token 返回 / Stream chat response token by token

        Default implementation falls back to non-streaming. Subclasses should
        override for true streaming support.

        Args:
            messages: 消息列表 / Message list.
            temperature: 覆盖温度 / Override temperature.
            max_tokens: 覆盖token数 / Override max tokens.

        Yields:
            从大模型返回的字符串片段 / String chunks as they arrive from the LLM.
        """
        # Default implementation: fall back to non-streaming and yield full content
        # Subclasses should override this for true streaming
        response = await self.chat(messages, temperature, max_tokens)
        yield response.get("content", "")

    @abstractmethod
    def get_provider_name(self) -> str:
        """获取提供商名称 / Get provider name (e.g., 'openai', 'anthropic')."""
        pass
