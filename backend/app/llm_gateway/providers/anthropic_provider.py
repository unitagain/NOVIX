# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Anthropic (Claude) LLM提供商适配器
  Anthropic (Claude) Provider - Implements BaseLLMProvider for Claude API
"""

import json

from typing import AsyncGenerator, List, Dict, Any, Optional
from app.llm_gateway.providers.base import BaseLLMProvider
from app.utils.anthropic_client import create_async_anthropic_client
from app.error_contract import record_degradation


def _prompt_caching_enabled() -> bool:
    """读取 config.retrieval.prompt_caching（Phase 6，默认 True；缺省视为开，与 config.yaml 一致）。

    能力门控：仅 Anthropic 生效（把 system 标为 ephemeral 可缓存块）；其余 provider 不调用本函数。
    """
    try:
        from app.config import config

        return bool((config.get("retrieval", {}) or {}).get("prompt_caching", True))
    except Exception:
        return False


def _anthropic_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized = []
    for tool in tools or []:
        function = tool.get("function") if tool.get("type") == "function" else tool
        function = dict(function or {})
        name = str(function.get("name") or "")
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "description": str(function.get("description") or ""),
                "input_schema": dict(function.get("parameters") or function.get("input_schema") or {}),
            }
        )
    return normalized


def _anthropic_tool_choice(value: Any) -> Any:
    if value is None or isinstance(value, dict) and value.get("type") in {"auto", "any", "tool", "none"}:
        return value
    if isinstance(value, str):
        return {"type": value} if value in {"auto", "any", "none"} else {"type": "tool", "name": value}
    if isinstance(value, dict):
        function = value.get("function") or {}
        name = str(function.get("name") or value.get("name") or "")
        return {"type": "tool", "name": name} if name else None
    return None


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic API提供商 / Anthropic API provider for Claude models

    Implements the LLM provider interface for Anthropic's Claude models.
    Handles system message extraction and proper message formatting for Claude.

    Attributes:
        client (AsyncAnthropic): 异步 Anthropic 客户端 / Async Anthropic client instance.
    """

    def __init__(
        self, api_key: str, model: str = "claude-3-5-sonnet-20241022", max_tokens: int = 8000, temperature: float = 0.7
    ):
        """
        初始化 Anthropic提供商 / Initialize Anthropic provider

        Args:
            api_key: Anthropic API密钥 / Anthropic API key.
            model: Claude模型名称，默认 claude-3-5-sonnet-20241022 / Claude model name.
            max_tokens: 最大生成token数 / Maximum tokens to generate.
            temperature: 生成温度 / Generation temperature.
        """
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = create_async_anthropic_client(api_key=api_key)

    def _request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        *,
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[Any],
        thinking: Optional[Any],
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        system_message = None
        filtered_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                filtered_messages.append(msg)

        resolved_max_tokens = max_tokens or self.max_tokens
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": filtered_messages,
            "max_tokens": resolved_max_tokens,
        }
        if not isinstance(thinking, dict):
            kwargs["temperature"] = self.temperature if temperature is None else temperature
        if system_message:
            if _prompt_caching_enabled():
                kwargs["system"] = [{"type": "text", "text": system_message, "cache_control": {"type": "ephemeral"}}]
            else:
                kwargs["system"] = system_message
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)
            normalized_choice = _anthropic_tool_choice(tool_choice)
            if normalized_choice is not None:
                kwargs["tool_choice"] = normalized_choice
        if isinstance(thinking, dict):
            kwargs["thinking"] = thinking
            budget = int(thinking.get("budget_tokens") or 0)
            if budget and resolved_max_tokens <= budget:
                kwargs["max_tokens"] = budget + 1024
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

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
        发送聊天请求到 Anthropic / Send chat request to Anthropic.

        抽取 system 为独立参数；支持 Claude 工具调用（tools/tool_choice）与 thinking（均可选、默认关）。
        response_format 对 Claude 无直接对应，忽略。多内容块（text/tool_use/thinking）已健壮解析。
        """
        kwargs = self._request_kwargs(
            messages,
            temperature,
            max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            thinking=thinking,
            extra_body=extra_body,
        )

        response = await self.client.messages.create(**kwargs)

        # 健壮解析多内容块：text 拼为正文，tool_use 转 tool_calls，thinking 块抽出供透明化
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif block_type == "thinking":
                thinking_parts.append(getattr(block, "thinking", "") or getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": getattr(block, "id", None),
                        "type": "function",
                        "name": getattr(block, "name", None),
                        "arguments": json.dumps(getattr(block, "input", {}) or {}, ensure_ascii=False),
                    }
                )

        # Phase 13：提取 prompt caching 命中（仅 Anthropic 返回 cache_*_input_tokens），记缓存命中率指标。
        usage = self._normalize_usage(response.usage)
        cache_creation = int(usage.get("cache_creation_tokens") or 0)
        cache_read = int(usage.get("cache_read_tokens") or 0)
        try:
            from app.utils.cache_metrics import record_cache

            record_cache(cache_read, cache_creation, int(response.usage.input_tokens or 0))
        except Exception as exc:
            record_degradation("anthropic_cache_metrics", exc)

        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls or None,
            "thinking": "".join(thinking_parts) or None,
            "usage": usage,
            "model": response.model,
            "finish_reason": response.stop_reason,
        }

    def get_provider_name(self) -> str:
        """获取提供商名称 / Get provider name."""
        return "anthropic"

    def supports_agentic_stream(self) -> bool:
        return True

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
        """Yield normalized Claude text, thinking, tool input, usage, and finish events."""
        kwargs = self._request_kwargs(
            messages,
            temperature,
            max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            thinking=thinking,
        )
        stream_manager = self.client.messages.stream(**kwargs)
        async with stream_manager as stream:
            indexes: Dict[int, Dict[str, str]] = {}
            async for event in stream:
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "message_start":
                    usage = getattr(getattr(event, "message", None), "usage", None)
                    if usage is not None:
                        yield {"type": "usage", "usage": self._normalize_usage(usage)}
                elif event_type == "content_block_start":
                    index = int(getattr(event, "index", 0) or 0)
                    block = getattr(event, "content_block", None)
                    if str(getattr(block, "type", "") or "") == "tool_use":
                        row = indexes.setdefault(index, {"id": "", "name": ""})
                        row["id"] = str(getattr(block, "id", "") or "")
                        row["name"] = str(getattr(block, "name", "") or "")
                        yield {
                            "type": "tool_call_delta",
                            "index": index,
                            "id": row["id"],
                            "name": row["name"],
                            "arguments": "",
                        }
                elif event_type == "content_block_delta":
                    index = int(getattr(event, "index", 0) or 0)
                    delta = getattr(event, "delta", None)
                    delta_type = str(getattr(delta, "type", "") or "")
                    if delta_type == "text_delta":
                        yield {"type": "content_delta", "content": str(getattr(delta, "text", "") or "")}
                    elif delta_type == "thinking_delta":
                        yield {
                            "type": "thinking_delta",
                            "content": str(getattr(delta, "thinking", "") or ""),
                        }
                    elif delta_type == "input_json_delta":
                        yield {
                            "type": "tool_call_delta",
                            "index": index,
                            "id": "",
                            "name": "",
                            "arguments": str(getattr(delta, "partial_json", "") or ""),
                        }
                elif event_type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        yield {"type": "usage", "usage": self._normalize_usage(usage)}
                    stop_reason = str(getattr(getattr(event, "delta", None), "stop_reason", "") or "")
                    if stop_reason:
                        yield {"type": "finish", "finish_reason": stop_reason}

    @staticmethod
    def _normalize_usage(usage: Any) -> Dict[str, Any]:
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        completion = int(getattr(usage, "output_tokens", 0) or 0)
        row = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        if hasattr(usage, "cache_creation_input_tokens"):
            row["cache_creation_tokens"] = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if hasattr(usage, "cache_read_input_tokens"):
            row["cache_read_tokens"] = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        return row
