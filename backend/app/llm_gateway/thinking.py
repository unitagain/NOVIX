"""Model-aware reasoning control and provider parameter translation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.llm_gateway.model_catalog import reasoning_capability

_DEFAULT_BUDGET_TOKENS = 8000


def supports_thinking(provider_name: str, model: str) -> bool:
    return bool(reasoning_capability(provider_name, model).get("supported"))


def build_reasoning_param(
    provider_name: str,
    model: str,
    level: str = "auto",
    *,
    dialect: str = "",
) -> Optional[Dict[str, Any]]:
    capability = reasoning_capability(provider_name, model, dialect=dialect)
    if not capability.get("supported"):
        return None
    requested = str(level or "auto").lower()
    levels = list(capability.get("levels") or ["auto"])
    if requested not in levels:
        requested = str(capability.get("default_level") or "auto")
    if requested == "auto":
        return None

    actual_dialect = str(capability.get("dialect") or provider_name)
    if actual_dialect == "anthropic_adaptive":
        if requested == "off":
            return {"type": "disabled"}
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": requested}}
    if actual_dialect == "anthropic_budget":
        return {"type": "disabled"} if requested == "off" else {"type": "enabled", "budget_tokens": _DEFAULT_BUDGET_TOKENS}
    if actual_dialect == "gemini_level":
        return {"reasoning_effort": requested}
    if actual_dialect == "deepseek":
        if requested == "off":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "max" if requested == "max" else "high"}
    if actual_dialect == "qwen":
        return {"enable_thinking": requested != "off"}
    if actual_dialect == "kimi":
        return {"thinking": {"type": "disabled" if requested == "off" else "enabled"}}
    if actual_dialect == "glm":
        return {"thinking": {"type": "disabled" if requested == "off" else "enabled"}}
    if actual_dialect == "openai_responses":
        return {"reasoning": {"effort": "none" if requested == "off" else requested}}
    return {"reasoning_effort": "none" if requested == "off" else requested}


def build_thinking_param(provider_name: str, model: str) -> Optional[Dict[str, Any]]:
    """Legacy boolean compatibility: enabled maps to the model's high/default reasoning."""
    capability = reasoning_capability(provider_name, model)
    preferred = "high" if "high" in capability.get("levels", []) else capability.get("default_level", "auto")
    return build_reasoning_param(provider_name, model, str(preferred))


def reasoning_param_enabled(value: Optional[Dict[str, Any]]) -> bool:
    """Return whether a translated provider parameter actually enables reasoning."""

    if not isinstance(value, dict) or not value:
        return False
    if value.get("enable_thinking") is False:
        return False
    if str(value.get("reasoning_effort") or "").lower() in {"none", "off"}:
        return False
    if str(value.get("type") or "").lower() == "disabled":
        return False
    thinking = value.get("thinking")
    if isinstance(thinking, dict) and str(thinking.get("type") or "").lower() == "disabled":
        return False
    reasoning = value.get("reasoning")
    if isinstance(reasoning, dict) and str(reasoning.get("effort") or "").lower() in {"none", "off"}:
        return False
    return True


def drops_temperature(provider_name: str) -> bool:
    return str(provider_name or "").lower() in {"anthropic", "openai", "deepseek", "kimi"}
