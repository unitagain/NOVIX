"""
LLM Gateway / 大模型网关
Unified interface with retry, rate limiting, and cost tracking
统一接口，包含重试、限流和成本追踪
"""

import asyncio
import os
import time
from typing import List, Dict, Any, Optional
from app.config import config
from app.llm_gateway.providers import (
    BaseLLMProvider,
    OpenAIProvider,
    AnthropicProvider,
    DeepSeekProvider,
    MockProvider,
)


class LLMGateway:
    """
    Unified LLM gateway with provider management
    统一的大模型网关，支持多提供商管理
    """
    
    def __init__(self):
        """Initialize gateway with configured providers / 使用配置初始化网关"""
        self.providers: Dict[str, BaseLLMProvider] = {}
        self._init_providers()
        
        # Retry configuration / 重试配置
        self.max_retries = 3
        self.retry_delays = [1, 2, 4]  # Exponential backoff / 指数退避
        
        # Cost tracking / 成本追踪
        self.total_tokens = 0
        self.total_requests = 0
    
    def _init_providers(self) -> None:
        """Initialize LLM providers from config / 从配置初始化提供商"""
        providers_config = config.get("llm", {}).get("providers", {})

        def _has_real_key(value: Optional[str]) -> bool:
            if not value:
                return False
            v = str(value).strip()
            placeholders = [
                "sk-your-",
                "sk-ant-your-",
                "your-deepseek-key-here",
            ]
            return not any(v.startswith(p) for p in placeholders)

        # Always register Mock provider (no key needed)
        self.providers["mock"] = MockProvider()
        
        # Initialize OpenAI / 初始化 OpenAI
        if "openai" in providers_config:
            openai_config = providers_config["openai"]
            if _has_real_key(openai_config.get("api_key")):
                self.providers["openai"] = OpenAIProvider(
                    api_key=openai_config["api_key"],
                    model=openai_config.get("model", "gpt-4o"),
                    max_tokens=openai_config.get("max_tokens", 8000),
                    temperature=openai_config.get("temperature", 0.7)
                )
        
        # Initialize Anthropic / 初始化 Anthropic
        if "anthropic" in providers_config:
            anthropic_config = providers_config["anthropic"]
            if _has_real_key(anthropic_config.get("api_key")):
                self.providers["anthropic"] = AnthropicProvider(
                    api_key=anthropic_config["api_key"],
                    model=anthropic_config.get("model", "claude-3-5-sonnet-20241022"),
                    max_tokens=anthropic_config.get("max_tokens", 8000),
                    temperature=anthropic_config.get("temperature", 0.7)
                )
        
        # Initialize DeepSeek / 初始化 DeepSeek
        if "deepseek" in providers_config:
            deepseek_config = providers_config["deepseek"]
            if _has_real_key(deepseek_config.get("api_key")):
                self.providers["deepseek"] = DeepSeekProvider(
                    api_key=deepseek_config["api_key"],
                    model=deepseek_config.get("model", "deepseek-chat"),
                    max_tokens=deepseek_config.get("max_tokens", 8000),
                    temperature=deepseek_config.get("temperature", 0.7)
                )
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry: bool = True
    ) -> Dict[str, Any]:
        """
        Send chat request with automatic retry
        发送聊天请求，支持自动重试
        
        Args:
            messages: List of messages / 消息列表
            provider: Provider name (openai, anthropic, deepseek) / 提供商名称
            temperature: Temperature override / 温度覆盖
            max_tokens: Max tokens override / 最大token数覆盖
            retry: Enable retry on failure / 启用失败重试
            
        Returns:
            Response dict with content, usage, etc. / 包含内容、使用量等的响应字典
            
        Raises:
            ValueError: If provider not available / 提供商不可用
            Exception: If all retries failed / 所有重试都失败
        """
        # Select provider / 选择提供商
        if provider is None:
            provider = os.getenv("NOVIX_LLM_PROVIDER") or config.get("llm", {}).get("default_provider", "openai")
        
        if provider not in self.providers:
            raise ValueError(
                f"Provider '{provider}' not available. "
                f"Available: {list(self.providers.keys())}"
            )
        
        llm_provider = self.providers[provider]
        
        # Execute with retry / 执行带重试的请求
        if retry:
            return await self._chat_with_retry(
                llm_provider,
                messages,
                temperature,
                max_tokens
            )
        else:
            return await self._execute_chat(
                llm_provider,
                messages,
                temperature,
                max_tokens
            )
    
    async def _chat_with_retry(
        self,
        provider: BaseLLMProvider,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> Dict[str, Any]:
        """
        Execute chat with exponential backoff retry
        执行聊天请求，使用指数退避重试
        
        Args:
            provider: LLM provider instance / 提供商实例
            messages: Messages list / 消息列表
            temperature: Temperature / 温度
            max_tokens: Max tokens / 最大token数
            
        Returns:
            Response dict / 响应字典
            
        Raises:
            Exception: If all retries failed / 所有重试都失败
        """
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return await self._execute_chat(
                    provider,
                    messages,
                    temperature,
                    max_tokens
                )
            except Exception as e:
                last_exception = e
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delays[attempt]
                    print(
                        f"[LLMGateway] Retry {attempt + 1}/{self.max_retries} "
                        f"after {delay}s due to: {str(e)}"
                    )
                    await asyncio.sleep(delay)
                else:
                    print(f"[LLMGateway] All retries failed: {str(e)}")
        
        raise last_exception
    
    async def _execute_chat(
        self,
        provider: BaseLLMProvider,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int]
    ) -> Dict[str, Any]:
        """
        Execute single chat request
        执行单次聊天请求
        
        Args:
            provider: LLM provider instance / 提供商实例
            messages: Messages list / 消息列表
            temperature: Temperature / 温度
            max_tokens: Max tokens / 最大token数
            
        Returns:
            Response dict / 响应字典
        """
        start_time = time.time()
        
        response = await provider.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        elapsed_time = time.time() - start_time
        
        # Track statistics / 追踪统计信息
        self.total_requests += 1
        self.total_tokens += response.get("usage", {}).get("total_tokens", 0)
        
        # Add metadata / 添加元数据
        response["provider"] = provider.get_provider_name()
        response["elapsed_time"] = elapsed_time
        
        return response
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get gateway statistics
        获取网关统计信息
        
        Returns:
            Statistics dict / 统计字典
        """
        return {
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "available_providers": list(self.providers.keys())
        }
    
    def get_provider_for_agent(self, agent_name: str) -> str:
        """
        Get configured provider for specific agent
        获取特定 Agent 的配置提供商
        
        Args:
            agent_name: Agent name (archivist, writer, reviewer, editor)
                       Agent 名称
                       
        Returns:
            Provider name / 提供商名称
        """
        env_key = f"NOVIX_AGENT_{agent_name.upper()}_PROVIDER"
        env_provider = os.getenv(env_key)
        if env_provider:
            return env_provider

        # Global override (should take precedence over config.yaml per-agent defaults)
        # 全局覆盖（应优先于 config.yaml 的 agent 默认值）
        global_provider = os.getenv("NOVIX_LLM_PROVIDER")
        if global_provider:
            return global_provider

        agents_config = config.get("agents", {})
        agent_config = agents_config.get(agent_name, {})
        return agent_config.get(
            "provider",
            config.get("llm", {}).get("default_provider", "openai")
        )
    
    def get_temperature_for_agent(self, agent_name: str) -> float:
        """
        Get configured temperature for specific agent
        获取特定 Agent 的配置温度
        
        Args:
            agent_name: Agent name / Agent 名称
            
        Returns:
            Temperature value / 温度值
        """
        agents_config = config.get("agents", {})
        agent_config = agents_config.get(agent_name, {})
        return agent_config.get("temperature", 0.7)


# Global gateway instance / 全局网关实例
_gateway_instance: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    """
    Get or create global gateway instance
    获取或创建全局网关实例
    
    Returns:
        LLM gateway instance / 网关实例
    """
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = LLMGateway()
    return _gateway_instance


def reset_gateway() -> None:
    """Reset global gateway instance so new config takes effect / 重置全局网关实例以应用新配置"""
    global _gateway_instance
    _gateway_instance = None
