"""Provider adapter registry and runtime profile resolution."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.llm_gateway.providers import (
    AIStudioProvider,
    AnthropicProvider,
    CustomProvider,
    DeepSeekProvider,
    GeminiProvider,
    GLMProvider,
    GrokProvider,
    KimiProvider,
    OpenAIProvider,
    QwenProvider,
    WenxinProvider,
)
from app.llm_gateway.providers.base import BaseLLMProvider
from app.services.llm_config_service import LLMConfigService, llm_config_service
from app.utils.logger import get_logger


logger = get_logger(__name__)


class ProviderRegistry:
    """Own provider construction, caching and profile/provider resolution."""

    def __init__(self, config_service: LLMConfigService = llm_config_service) -> None:
        self.config_service = config_service
        self.providers: Dict[str, BaseLLMProvider] = {}
        self.reload()

    @classmethod
    def from_providers(cls, providers: Dict[str, BaseLLMProvider]) -> "ProviderRegistry":
        registry = cls.__new__(cls)
        registry.config_service = llm_config_service
        registry.providers = dict(providers)
        return registry

    def reload(self) -> None:
        self.providers = {}
        for profile in self.config_service.get_profiles():
            try:
                provider = self.create(profile)
                if provider:
                    self.providers[str(profile["id"])] = provider
            except Exception as exc:
                logger.error("Failed to initialize provider profile %s: %s", profile.get("name"), exc)

    def ensure_loaded(self, profile_id: str) -> bool:
        if not profile_id:
            return False
        if profile_id in self.providers:
            return True
        profile = self.config_service.get_profile_by_id(profile_id)
        if not profile:
            return False
        try:
            provider = self.create(profile)
            if not provider:
                return False
            self.providers[profile_id] = provider
            return True
        except Exception as exc:
            logger.error("Failed to lazy-load provider profile %s: %s", profile_id, exc)
            return False

    def resolve(self, profile_or_provider: Optional[str]) -> BaseLLMProvider:
        key = str(profile_or_provider or "")
        if key and key not in self.providers:
            self.ensure_loaded(key)
        if key in self.providers:
            return self.providers[key]
        for provider in self.providers.values():
            if provider.get_provider_name() == profile_or_provider:
                return provider
        raise ValueError(f"provider_profile_not_found:{key or 'missing'}")

    @staticmethod
    def create(profile: Dict[str, Any]) -> Optional[BaseLLMProvider]:
        provider_type = str(profile.get("provider") or "").lower()
        api_key = profile.get("api_key")
        model = str(profile.get("model") or "")
        max_tokens = int(profile.get("max_tokens") or 8000)
        temperature = float(profile.get("temperature") if profile.get("temperature") is not None else 0.7)
        common = {"api_key": api_key, "max_tokens": max_tokens, "temperature": temperature}

        if provider_type == "openai":
            return OpenAIProvider(model=model or "gpt-5.4-mini", **common)
        if provider_type == "anthropic":
            return AnthropicProvider(model=model or "claude-sonnet-4-6", **common)
        if provider_type == "deepseek":
            return DeepSeekProvider(model=model or "deepseek-chat", **common)
        if provider_type == "gemini":
            return GeminiProvider(model=model or "gemini-3.1-pro-preview", **common)

        compatible = {
            "qwen": (QwenProvider, "qwen3.5-plus"),
            "kimi": (KimiProvider, "kimi-k2.5"),
            "glm": (GLMProvider, "glm-5"),
            "grok": (GrokProvider, "grok-4"),
            "wenxin": (WenxinProvider, "ernie-4.5-turbo-32k"),
            "aistudio": (AIStudioProvider, "ernie-5.0-thinking-preview"),
            "custom": (CustomProvider, "custom-model"),
        }
        entry = compatible.get(provider_type)
        if not entry:
            return None
        provider_class, default_model = entry
        return provider_class(
            api_key=api_key or ("sk-custom" if provider_type == "custom" else api_key),
            base_url=profile.get("base_url") or "",
            model=model or default_model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
