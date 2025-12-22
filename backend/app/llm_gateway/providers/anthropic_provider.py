"""
Anthropic (Claude) Provider / Anthropic (Claude) 适配器
"""

from typing import List, Dict, Any, Optional
from anthropic import AsyncAnthropic
from app.llm_gateway.providers.base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Anthropic API provider / Anthropic API 提供商"""
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 8000,
        temperature: float = 0.7
    ):
        super().__init__(api_key, model, max_tokens, temperature)
        self.client = AsyncAnthropic(api_key=api_key)
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send chat request to Anthropic
        发送聊天请求到 Anthropic
        
        Args:
            messages: List of messages / 消息列表
            temperature: Override temperature / 覆盖温度
            max_tokens: Override max tokens / 覆盖最大token数
            
        Returns:
            Response dict / 响应字典
        """
        # Extract system message if present / 提取系统消息
        system_message = None
        filtered_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                filtered_messages.append(msg)
        
        # Anthropic API call / Anthropic API 调用
        kwargs = {
            "model": self.model,
            "messages": filtered_messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens
        }
        
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
        """Get provider name / 获取提供商名称"""
        return "anthropic"
