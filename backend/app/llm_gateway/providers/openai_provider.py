"""
OpenAI Provider / OpenAI 适配器
"""

from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.llm_gateway.providers.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider / OpenAI API 提供商"""
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 8000,
        temperature: float = 0.7
    ):
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = AsyncOpenAI(api_key=api_key)
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send chat request to OpenAI
        发送聊天请求到 OpenAI
        
        Args:
            messages: List of messages / 消息列表
            temperature: Override temperature / 覆盖温度
            max_tokens: Override max tokens / 覆盖最大token数
            
        Returns:
            Response dict / 响应字典
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens
        )
        
        return {
            "content": response.choices[0].message.content,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            },
            "model": response.model,
            "finish_reason": response.choices[0].finish_reason
        }
    
    def get_provider_name(self) -> str:
        """Get provider name / 获取提供商名称"""
        return "openai"
