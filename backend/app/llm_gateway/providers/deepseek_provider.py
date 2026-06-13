"""
DeepSeek Provider / DeepSeek 适配器
"""

from typing import List, Dict, Any, Optional, AsyncGenerator
from app.llm_gateway.providers.base import BaseLLMProvider
from app.utils.openai_client import create_async_openai_client


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek API provider (OpenAI-compatible) / DeepSeek API 提供商（兼容OpenAI）"""
    
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        max_tokens: int = 8000,
        temperature: float = 0.7
    ):
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = create_async_openai_client(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1"
        )
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send chat request to DeepSeek
        发送聊天请求到 DeepSeek
        
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

        if not hasattr(response, "choices") or not response.choices:
            raise ValueError(
                f"API returned unexpected response (no 'choices'). "
                f"Response type: {type(response).__name__}, value: {str(response)[:200]}"
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
    
    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat response token by token
        流式输出聊天响应
        
        Yields:
            String chunks as they arrive from DeepSeek
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            stream=True  # 启用流式输出
        )
        
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    
    def get_provider_name(self) -> str:
        """Get provider name / 获取提供商名称"""
        return "deepseek"
