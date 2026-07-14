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


def _extract_reasoning_delta(delta: Any) -> Optional[str]:
    """从 OpenAI 兼容流式 delta 中抽取推理/思考增量（reasoning_content）。

    推理模型（deepseek-reasoner 及兼容端点）把思考过程放在非标准字段 reasoning_content；
    优先取属性，回退到 pydantic model_extra。无则返回 None。
    Extract the reasoning/thinking delta from an OpenAI-style streaming delta;
    reasoning models expose it on the non-standard ``reasoning_content`` field.
    """
    if delta is None:
        return None
    reasoning = getattr(delta, "reasoning_content", None)
    if not reasoning:
        extra = getattr(delta, "model_extra", None)
        if isinstance(extra, dict):
            reasoning = extra.get("reasoning_content")
    return reasoning or None


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


def normalize_openai_usage(usage: Any) -> Optional[Dict[str, Any]]:
    """Normalize OpenAI-compatible usage while preserving unavailable cache fields."""
    if usage is None:
        return None
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(details, "cached_tokens", None) if details is not None else None
    if cache_read is None:
        cache_read = getattr(usage, "prompt_cache_hit_tokens", None)
    cache_creation = getattr(usage, "prompt_cache_miss_tokens", None)
    row: Dict[str, Any] = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    if cache_read is not None:
        row["cache_read_tokens"] = int(cache_read or 0)
    if cache_creation is not None:
        row["cache_creation_tokens"] = int(cache_creation or 0)
    return row


async def stream_openai_events(client: Any, params: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
    """Yield normalized content/reasoning/tool deltas from an OpenAI-compatible client."""
    response = await client.chat.completions.create(**params, stream=True)
    async for chunk in response:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            normalized_usage = normalize_openai_usage(usage) or {}
            yield {
                "type": "usage",
                "usage": normalized_usage,
            }
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        reasoning = _extract_reasoning_delta(delta)
        if reasoning:
            yield {"type": "thinking_delta", "content": str(reasoning)}
        content = getattr(delta, "content", None) if delta is not None else None
        if content:
            yield {"type": "content_delta", "content": str(content)}
        for call in getattr(delta, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            yield {
                "type": "tool_call_delta",
                "index": int(getattr(call, "index", 0) or 0),
                "id": str(getattr(call, "id", "") or ""),
                "name": str(getattr(function, "name", "") or "") if function is not None else "",
                "arguments": str(getattr(function, "arguments", "") or "") if function is not None else "",
            }
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason:
            yield {"type": "finish", "finish_reason": str(finish_reason)}


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
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        on_thinking: Optional[Any] = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式输出聊天响应，逐 token 返回 / Stream chat response token by token

        Default implementation falls back to non-streaming. Subclasses should
        override for true streaming support.

        Args:
            messages: 消息列表 / Message list.
            temperature: 覆盖温度 / Override temperature.
            max_tokens: 覆盖token数 / Override max tokens.
            on_thinking: 可选 async 回调，接收推理/思考增量（reasoning_content）。
                默认 None；支持推理模型的 provider 在收到推理增量时调用它（正文仍走 yield）。
                Optional async callback receiving reasoning/thinking deltas; content
                still flows via yield, so the default path is unchanged.

        Yields:
            从大模型返回的字符串片段 / String chunks as they arrive from the LLM.
        """
        # Default implementation: fall back to non-streaming and yield full content
        # Subclasses should override this for true streaming
        response = await self.chat(messages, temperature, max_tokens)
        yield response.get("content", "")

    def supports_agentic_stream(self) -> bool:
        """Whether the adapter can stream content and tool-call deltas safely."""
        return False

    async def stream_chat_events(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        thinking: Optional[Any] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Structured stream fallback used by the agent runtime.

        Providers without tool-delta support return one explicit non-native event;
        callers must report the degradation instead of manufacturing token chunks.
        """
        response = await self.chat(
            messages,
            temperature,
            max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            thinking=thinking,
        )
        yield {"type": "response", "response": response, "native": False}

    @abstractmethod
    def get_provider_name(self) -> str:
        """获取提供商名称 / Get provider name (e.g., 'openai', 'anthropic')."""
        pass
