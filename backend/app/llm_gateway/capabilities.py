"""Provider capability negotiation port."""

from __future__ import annotations

from typing import Any, Dict

from app.llm_gateway.provider_conformance import provider_adapter_profile
from app.llm_gateway.providers.base import BaseLLMProvider


class CapabilityNegotiator:
    def negotiate(self, provider: BaseLLMProvider, options: Dict[str, Any]) -> Dict[str, Any]:
        name = str(provider.get_provider_name() or "").lower()
        profile = provider_adapter_profile(name)
        declared = provider.declared_capabilities() if profile is None else None
        assumed = profile is None and declared is None
        capabilities = {
            "tools": bool(profile.tools if profile else (declared or {}).get("tools", False)),
            "json_mode": bool(profile.json_mode if profile else (declared or {}).get("json_mode", False)),
            "thinking": bool(profile.thinking if profile else (declared or {}).get("thinking", False)),
            "streaming": True,
            "agentic_stream": bool(
                profile.native_agentic_stream if profile else (declared or {}).get("agentic_stream", False)
            ),
            "stream_mode": profile.stream_mode if profile else str((declared or {}).get("stream_mode", "unknown")),
            "assumed": assumed,
            "declared": declared is not None,
        }
        actual = dict(options)
        degradation = []
        if actual.get("tools") and not capabilities["tools"]:
            actual.pop("tools", None)
            actual.pop("tool_choice", None)
            degradation.append({"capability": "tools", "status": "disabled", "reason": "unsupported"})
        if actual.get("response_format") and not capabilities["json_mode"]:
            actual.pop("response_format", None)
            degradation.append({"capability": "json_mode", "status": "prompt_fallback", "reason": "unsupported"})
        if actual.get("thinking") and not capabilities["thinking"]:
            actual.pop("thinking", None)
            degradation.append({"capability": "thinking", "status": "disabled", "reason": "unsupported"})
        return {"options": actual, "capabilities": capabilities, "degradation": degradation}
