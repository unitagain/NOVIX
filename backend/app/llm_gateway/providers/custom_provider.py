"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

OpenAI-compatible custom provider adapter.
"""

from typing import List, Dict, Any, Optional, AsyncGenerator
from app.llm_gateway.providers.base import (
    BaseLLMProvider,
    normalize_openai_usage,
    normalize_tool_calls,
    _extract_reasoning_delta,
    stream_openai_events,
)
from app.utils.openai_client import create_async_openai_client


class CustomProvider(BaseLLMProvider):
    """Custom OpenAI-compatible API provider / 自定义 OpenAI 兼容 API 提供商"""

    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 8000, temperature: float = 0.7):
        super().__init__(api_key, model, max_tokens, temperature)
        # Ensure base_url is valid, if empty default to None (which defaults to standard OpenAI)
        # But for 'custom', user likely provides a specific URL.
        # If user leaves it blank but uses 'custom', it behaves like standard OpenAI?
        # Better to pass it explicitely.
        self.client = create_async_openai_client(api_key=api_key, base_url=base_url if base_url else None)

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
        Send chat request to Custom Provider / 发送聊天请求到自定义提供商

        新增可选能力（默认关、旧行为不变）：tools / tool_choice / response_format / thinking / extra_body。
        """
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
        if response_format is not None:
            params["response_format"] = response_format
        body = dict(extra_body) if extra_body else {}
        if isinstance(thinking, dict):
            body.update(thinking)
        if body:
            params["extra_body"] = body

        response = await self.client.chat.completions.create(**params)

        if not hasattr(response, "choices") or not response.choices:
            raise ValueError(
                f"API returned unexpected response (no 'choices'). "
                f"Response type: {type(response).__name__}, value: {str(response)[:200]}"
            )

        message = response.choices[0].message
        return {
            "content": message.content,
            "tool_calls": normalize_tool_calls(message),
            "usage": normalize_openai_usage(response.usage),
            "model": getattr(response, "model", self.model),
            "finish_reason": response.choices[0].finish_reason,
        }

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        on_thinking: Optional[Any] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat response token by token
        流式输出聊天响应

        推理模型（如 deepseek-reasoner / 兼容端点）会在 delta.reasoning_content 给出思考增量；
        若提供 on_thinking 则把思考增量旁路推出（正文仍逐 token yield，行为不变）。
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            stream=True,
        )

        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if on_thinking is not None:
                reasoning = _extract_reasoning_delta(delta)
                if reasoning:
                    await on_thinking(reasoning)
            if delta.content:
                yield delta.content

    def get_provider_name(self) -> str:
        """Get provider name / 获取提供商名称"""
        return "custom"

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
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
        if isinstance(thinking, dict):
            params["extra_body"] = dict(thinking)
        async for event in stream_openai_events(self.client, params):
            yield event
