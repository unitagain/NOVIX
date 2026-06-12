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

from typing import List, Dict, Any, Optional
from app.llm_gateway.providers.base import BaseLLMProvider
from app.utils.anthropic_client import create_async_anthropic_client


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic API提供商 / Anthropic API provider for Claude models

    Implements the LLM provider interface for Anthropic's Claude models.
    Handles system message extraction and proper message formatting for Claude.

    Attributes:
        client (AsyncAnthropic): 异步 Anthropic 客户端 / Async Anthropic client instance.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 8000,
        temperature: float = 0.7
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
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        发送聊天请求到 Anthropic / Send chat request to Anthropic

        Extracts system message if present and formats messages for Claude API.
        Handles the difference between OpenAI-style system messages and Claude's
        system parameter.

        Args:
            messages: 消息列表 / List of messages.
            temperature: 覆盖温度 / Override temperature.
            max_tokens: 覆盖token数 / Override max tokens.

        Returns:
            响应字典包含内容、使用统计等 / Response dict with content, usage, etc.
        """
        # ========================================================================
        # 提取系统消息（如存在） / Extract system message if present
        # ========================================================================
        system_message = None
        filtered_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                filtered_messages.append(msg)

        # ========================================================================
        # Anthropic API调用 / Anthropic API call
        # ========================================================================
        kwargs = {
            "model": self.model,
            "messages": filtered_messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens
        }

        # Claude expects system prompt as separate parameter, not in messages list
        if system_message:
            kwargs["system"] = system_message

        response = await self.client.messages.create(**kwargs)

        return {
            "content": response.content[0].text,
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens
            },
            "model": response.model,
            "finish_reason": response.stop_reason
        }

    def get_provider_name(self) -> str:
        """获取提供商名称 / Get provider name."""
        return "anthropic"
