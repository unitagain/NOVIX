"""Single inventory for registered provider adapter capabilities and stream modes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ProviderAdapterProfile:
    provider: str
    adapter: str
    stream_mode: str
    tools: bool
    json_mode: bool
    thinking: bool
    default_base_url: str = ""

    @property
    def native_agentic_stream(self) -> bool:
        return self.stream_mode in {"native_tool_stream", "openai_compatible_tool_stream"}

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "native_agentic_stream": self.native_agentic_stream}


PROVIDER_ADAPTER_PROFILES: Dict[str, ProviderAdapterProfile] = {
    "openai": ProviderAdapterProfile("openai", "OpenAIProvider", "native_tool_stream", True, True, True),
    "anthropic": ProviderAdapterProfile(
        "anthropic", "AnthropicProvider", "native_tool_stream", True, False, True
    ),
    "deepseek": ProviderAdapterProfile(
        "deepseek",
        "DeepSeekProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        True,
        "https://api.deepseek.com/v1",
    ),
    "gemini": ProviderAdapterProfile(
        "gemini",
        "GeminiProvider",
        "non_stream_fallback",
        False,
        False,
        False,
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    "qwen": ProviderAdapterProfile(
        "qwen",
        "QwenProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        True,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "kimi": ProviderAdapterProfile(
        "kimi",
        "KimiProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        True,
        "https://api.moonshot.cn/v1",
    ),
    "glm": ProviderAdapterProfile(
        "glm",
        "GLMProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        False,
        "https://open.bigmodel.cn/api/paas/v4",
    ),
    "grok": ProviderAdapterProfile(
        "grok",
        "GrokProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        False,
        "https://api.x.ai/v1",
    ),
    "wenxin": ProviderAdapterProfile(
        "wenxin",
        "WenxinProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        False,
        "https://qianfan.baidubce.com/v2",
    ),
    "aistudio": ProviderAdapterProfile(
        "aistudio",
        "AIStudioProvider",
        "openai_compatible_tool_stream",
        True,
        True,
        False,
        "https://aistudio.baidu.com/llm/lmapi/v3",
    ),
    "custom": ProviderAdapterProfile(
        "custom", "CustomProvider", "openai_compatible_tool_stream", True, True, False
    ),
}


def provider_adapter_inventory() -> list[Dict[str, Any]]:
    return [PROVIDER_ADAPTER_PROFILES[name].to_dict() for name in sorted(PROVIDER_ADAPTER_PROFILES)]


def provider_adapter_profile(name: str) -> ProviderAdapterProfile | None:
    return PROVIDER_ADAPTER_PROFILES.get(str(name or "").strip().lower())
