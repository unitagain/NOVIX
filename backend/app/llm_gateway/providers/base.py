"""
Base LLM Provider Interface / 基础大模型提供商接口
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers / 大模型提供商抽象基类"""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 8000,
        temperature: float = 0.7
    ):
        """
        Initialize provider
        
        Args:
            api_key: API key for the provider / API密钥
            model: Model name / 模型名称
            max_tokens: Maximum tokens to generate / 最大生成token数
            temperature: Temperature for generation / 生成温度
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
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send chat request to LLM provider
        发送聊天请求到大模型提供商
        
        Args:
            messages: List of messages in format [{"role": "user", "content": "..."}]
                     消息列表
            temperature: Override temperature / 覆盖温度设置
            max_tokens: Override max tokens / 覆盖最大token数
            
        Returns:
            Response dict with 'content', 'usage', etc.
            包含'content'、'usage'等的响应字典
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Get provider name / 获取提供商名称"""
        pass
