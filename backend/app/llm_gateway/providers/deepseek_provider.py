"""
DeepSeek Provider / DeepSeek 适配器
"""

from typing import List, Dict, Any, Optional, AsyncGenerator
from app.llm_gateway.providers.base import BaseLLMProvider, normalize_tool_calls, _extract_reasoning_delta
from app.utils.openai_client import create_async_openai_client


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek API provider (OpenAI-compatible) / DeepSeek API 提供商（兼容OpenAI）"""

    def __init__(self, api_key: str, model: str = "deepseek-chat", max_tokens: int = 8000, temperature: float = 0.7):
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = create_async_openai_client(api_key=api_key, base_url="https://api.deepseek.com/v1")

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
        Send chat request to DeepSeek / 发送聊天请求到 DeepSeek

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
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
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

        deepseek-reasoner 在 delta.reasoning_content 给出思考增量；若提供 on_thinking
        则旁路推出思考（正文仍逐 token yield，默认行为不变）。

        Yields:
            String chunks as they arrive from DeepSeek
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            stream=True,  # 启用流式输出
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
        return "deepseek"
