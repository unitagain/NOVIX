"""Versioned mainstream text-model catalog and reasoning capabilities.

Runtime provider model APIs remain authoritative. This catalog is a conservative,
official-doc-backed fallback for configuration UX and capability negotiation.
Verified: 2026-07-15.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

VERIFIED_AT = "2026-07-15"
REASONING_LEVELS = ("auto", "off", "minimal", "low", "medium", "high", "xhigh", "max")

FALLBACK_MODELS: Dict[str, List[str]] = {
    "openai": ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "anthropic": ["claude-sonnet-5", "claude-mythos-5", "claude-fable-5", "claude-opus-4-8", "claude-haiku-4-5"],
    "gemini": ["gemini-3.5-flash", "gemini-3.1-pro", "gemini-3.1-flash-lite", "gemini-3-flash"],
    "grok": ["grok-4.5", "grok-4.20-reasoning-latest", "grok-4.20-non-reasoning-latest"],
    "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "qwen": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash", "qwen3.5-plus"],
    "glm": ["glm-5.2", "glm-5-turbo", "glm-5", "glm-4.7-flash"],
    "kimi": ["kimi-k2.6", "kimi-k2.5", "kimi-latest"],
    "wenxin": ["ernie-5.0", "ernie-4.5-turbo-32k"],
    "aistudio": ["ernie-5.0", "ernie-5.0-thinking-preview"],
    "custom": [],
}

_NON_TEXT_MARKERS = (
    "image", "vision", "audio", "tts", "asr", "embedding", "realtime", "live", "video", "ocr", "computer-use"
)
_DEPRECATED_MARKERS = ("deprecated", "legacy")


def filter_mainstream_text_models(models: Iterable[str]) -> List[str]:
    result = []
    for raw in models:
        model = str(raw or "").strip()
        lowered = model.lower()
        if not model or any(marker in lowered for marker in _NON_TEXT_MARKERS + _DEPRECATED_MARKERS):
            continue
        result.append(model)
    return sorted(set(result))


def fallback_models(provider: str) -> List[str]:
    return list(FALLBACK_MODELS.get(str(provider or "").lower(), []))


def reasoning_capability(provider: str, model: str, *, dialect: str = "") -> Dict[str, Any]:
    provider = str(provider or "").lower()
    model = str(model or "").lower()
    base: Dict[str, Any] = {
        "supported": False,
        "levels": ["auto"],
        "default_level": "auto",
        "can_disable": True,
        "dialect": dialect or provider,
        "verified_at": VERIFIED_AT,
    }
    if provider == "openai" and model.startswith("gpt-5"):
        return {**base, "supported": True, "levels": ["auto", "off", "low", "medium", "high", "xhigh", "max"], "default_level": "medium", "dialect": "openai_chat"}
    if provider == "anthropic":
        adaptive = any(token in model for token in ("sonnet-5", "mythos", "fable-5", "opus-4-8", "opus-4-7"))
        if adaptive:
            forced = any(token in model for token in ("mythos-5", "fable-5"))
            return {**base, "supported": True, "levels": ["auto", "low", "medium", "high", "max"] if forced else ["auto", "off", "low", "medium", "high", "max"], "default_level": "high", "can_disable": not forced, "dialect": "anthropic_adaptive"}
        if "claude" in model:
            return {**base, "supported": True, "levels": ["auto", "off", "high"], "default_level": "auto", "dialect": "anthropic_budget"}
    if provider == "gemini" and model.startswith("gemini-"):
        levels = ["auto", "minimal", "low", "medium", "high"]
        if "3.1-pro" in model or "2.5-pro" in model or "2.5-flash" in model:
            levels = ["auto", "low", "medium", "high"]
        return {**base, "supported": True, "levels": levels, "default_level": "high", "dialect": "gemini_level"}
    if provider == "grok" and "non-reasoning" not in model and ("4.5" in model or "reasoning" in model):
        return {**base, "supported": True, "levels": ["auto", "low", "medium", "high"], "default_level": "high", "can_disable": False, "dialect": "openai_chat"}
    if provider == "deepseek" and ("v4" in model or "reasoner" in model):
        return {**base, "supported": True, "levels": ["auto", "off", "high", "max"], "default_level": "high", "dialect": "deepseek"}
    if provider == "qwen" and model.startswith("qwen"):
        forced = "thinking" in model or "qwq" in model
        return {**base, "supported": True, "levels": ["auto", "high"] if forced else ["auto", "off", "high"], "default_level": "auto", "can_disable": not forced, "dialect": "qwen"}
    if provider == "kimi" and model.startswith("kimi-k2"):
        forced = "2.7-code" in model
        return {**base, "supported": True, "levels": ["auto", "high"] if forced else ["auto", "off", "high"], "default_level": "auto", "can_disable": not forced, "dialect": "kimi", "preserves_reasoning": "2.6" in model or forced}
    if provider == "glm" and model.startswith("glm-"):
        return {**base, "supported": True, "levels": ["auto", "off", "high"], "default_level": "auto", "dialect": "glm"}
    if provider in {"wenxin", "aistudio"} and ("thinking" in model or "ernie-5" in model):
        return {**base, "supported": True, "levels": ["auto", "off", "high"], "default_level": "auto", "dialect": "openai_chat"}
    if provider == "custom":
        return {**base, "supported": True, "levels": ["auto", "off", "low", "medium", "high"], "default_level": "auto", "dialect": dialect or "openai_chat", "capability_source": "user_configured"}
    return base
