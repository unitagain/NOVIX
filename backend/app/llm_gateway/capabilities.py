"""Provider capability negotiation port."""

from __future__ import annotations

from typing import Any, Dict

from app.llm_gateway.providers.base import BaseLLMProvider


class CapabilityNegotiator:
    KNOWN = {
        "openai",
        "deepseek",
        "qwen",
        "kimi",
        "glm",
        "grok",
        "wenxin",
        "aistudio",
        "custom",
        "anthropic",
        "gemini",
    }
    OPENAI_COMPATIBLE = {"openai", "deepseek", "qwen", "kimi", "glm", "grok", "wenxin", "aistudio", "custom"}

    def negotiate(self, provider: BaseLLMProvider, options: Dict[str, Any]) -> Dict[str, Any]:
        name = str(provider.get_provider_name() or "").lower()
        assumed = name not in self.KNOWN
        capabilities = {
            "tools": name in self.OPENAI_COMPATIBLE or name == "anthropic" or assumed,
            "json_mode": name in self.OPENAI_COMPATIBLE or assumed,
            "thinking": name in {"anthropic", "deepseek", "qwen", "kimi"} or assumed,
            "streaming": True,
            "assumed": assumed,
        }
        actual = dict(options)
        degradation = []
        if actual.get("tools") and not capabilities["tools"]:
            raise ValueError(f"provider_capability_tools_unsupported:{name}")
        if actual.get("response_format") and not capabilities["json_mode"]:
            actual.pop("response_format", None)
            degradation.append({"capability": "json_mode", "status": "prompt_fallback", "reason": "unsupported"})
        if actual.get("thinking") and not capabilities["thinking"]:
            actual.pop("thinking", None)
            degradation.append({"capability": "thinking", "status": "disabled", "reason": "unsupported"})
        return {"options": actual, "capabilities": capabilities, "degradation": degradation}
