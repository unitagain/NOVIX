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

from typing import List, Dict, Any, Optional
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
        # 提取 system 消息（Claude 用独立 system 参数）/ Extract system message
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
        # 开启扩展思考时不传 temperature（API 禁止 temperature/top_p/top_k）；其余照常传。
        if not isinstance(thinking, dict):
            kwargs["temperature"] = temperature or self.temperature
        if system_message:
            # Phase 6 prompt caching：开启时把 system 标为 ephemeral 可缓存块（降本降延）；否则纯字符串。
            if _prompt_caching_enabled():
                kwargs["system"] = [{"type": "text", "text": system_message, "cache_control": {"type": "ephemeral"}}]
            else:
                kwargs["system"] = system_message
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if isinstance(thinking, dict):
            kwargs["thinking"] = thinking
            # budget_tokens 必须 < max_tokens：必要时上调输出上限。
            budget = int(thinking.get("budget_tokens") or 0)
            if budget and resolved_max_tokens <= budget:
                kwargs["max_tokens"] = budget + 1024
        if extra_body:
            kwargs["extra_body"] = extra_body

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
        cache_creation = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        try:
            from app.utils.cache_metrics import record_cache

            record_cache(cache_read, cache_creation, int(response.usage.input_tokens or 0))
        except Exception as exc:
            record_degradation("anthropic_cache_metrics", exc)

        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls or None,
            "thinking": "".join(thinking_parts) or None,
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "cache_creation_tokens": cache_creation,
                "cache_read_tokens": cache_read,
            },
            "model": response.model,
            "finish_reason": response.stop_reason,
        }

    def get_provider_name(self) -> str:
        """获取提供商名称 / Get provider name."""
        return "anthropic"
