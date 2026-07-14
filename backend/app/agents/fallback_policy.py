"""Typed, content-free fallback reasons and resumable agent context."""

from __future__ import annotations

from typing import Any, Dict


_REASON_CATEGORIES = {
    "max_iterations": "iteration_limit",
    "turn_deadline_exceeded": "deadline",
    "timeout": "deadline",
    "no_provider": "capability",
    "no_tool_calls": "no_tool_calls",
    "provider_failure": "provider_failure",
    "provider_agentic_stream_unavailable": "capability",
}


def fallback_category(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized in _REASON_CATEGORIES:
        return _REASON_CATEGORIES[normalized]
    if "deadline" in normalized or "timeout" in normalized:
        return "deadline"
    if "provider" in normalized or "circuit" in normalized or "rate_limit" in normalized:
        return "provider_failure"
    if "tool" in normalized:
        return "tool_failure"
    return "unknown"


def build_fallback_context(
    *,
    reason: str,
    agent_run: Dict[str, Any] | None,
    context_supply: Dict[str, Any] | None,
) -> Dict[str, Any]:
    run = agent_run or {}
    tool_results = list(run.get("tool_results") or [])
    return {
        "schema_version": 1,
        "reason": str(reason or "unknown"),
        "category": fallback_category(reason),
        "iterations": int(run.get("iterations") or 0),
        "tool_names": sorted({str(item.get("tool_name") or "") for item in tool_results if item.get("tool_name")}),
        "artifact_refs": sorted(
            {str(item.get("artifact_ref") or "") for item in tool_results if item.get("artifact_ref")}
        ),
        "source_types": sorted(set((context_supply or {}).get("used") or [])),
        "degradation_types": sorted(
            {
                str(item.get("type") or item.get("capability") or "")
                for item in list(run.get("degradations") or [])
                if item.get("type") or item.get("capability")
            }
        ),
    }
