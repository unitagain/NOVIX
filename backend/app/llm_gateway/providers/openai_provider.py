"""
OpenAI Provider / OpenAI 适配器
"""

from typing import List, Dict, Any, Optional
from app.llm_gateway.providers.base import BaseLLMProvider, normalize_tool_calls
from app.utils.openai_client import create_async_openai_client


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider / OpenAI API 提供商"""

    def __init__(self, api_key: str, model: str = "gpt-4o", max_tokens: int = 8000, temperature: float = 0.7):
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = create_async_openai_client(api_key=api_key)

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
        Send chat request to OpenAI / 发送聊天请求到 OpenAI

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

    def get_provider_name(self) -> str:
        """Get provider name / 获取提供商名称"""
        return "openai"
