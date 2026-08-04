# -*- coding: utf-8 -*-
"""P8 context assembly policy.

This module separates UI conversation history from model context assembly. It
does not replace the mature runtime path; it emits explicit policy metadata so
fresh-context-first behavior can be measured before deeper rewrites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ContextAssemblyPlan:
    """Explain how one model turn should assemble context."""

    strategy: str
    fresh_context_first: bool = True
    ui_history_policy: str = "preserve_full_conversation"
    model_context_policy: str = "jit_from_project_files"
    compact_policy: str = "fallback_when_history_or_budget_exceeds_threshold"
    cache_policy: str = "stable_prefix_cache_when_full_canon"
    budget: Dict[str, Any] = field(default_factory=dict)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    degradation: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "fresh_context_first": self.fresh_context_first,
            "ui_history_policy": self.ui_history_policy,
            "model_context_policy": self.model_context_policy,
            "compact_policy": self.compact_policy,
            "cache_policy": self.cache_policy,
            "budget": dict(self.budget),
            "sources": list(self.sources),
            "degradation": list(self.degradation),
        }


def choose_context_strategy(
    *,
    estimated_canon_items: int = 0,
    estimated_context_tokens: int = 0,
    context_budget_tokens: int = 16000,
    small_project_fact_limit: int = 120,
) -> str:
    """Choose full-canon or JIT retrieval policy using explicit thresholds."""

    if estimated_context_tokens > 0 and context_budget_tokens > 0:
        if estimated_context_tokens <= int(context_budget_tokens * 0.55):
            return "full_canon"
    if estimated_canon_items and estimated_canon_items <= small_project_fact_limit:
        return "full_canon"
    return "jit_retrieval"


def build_context_assembly_plan(
    *,
    route_path: str,
    estimated_canon_items: int = 0,
    estimated_context_tokens: int = 0,
    context_budget_tokens: int = 16000,
    has_compact_summary: bool = False,
    source_types: List[str] | None = None,
) -> ContextAssemblyPlan:
    """Build P8 context assembly metadata for ContextPlan and eval."""

    strategy = choose_context_strategy(
        estimated_canon_items=estimated_canon_items,
        estimated_context_tokens=estimated_context_tokens,
        context_budget_tokens=context_budget_tokens,
    )
    sources = [
        {"type": "project_files", "role": "truth_source", "fresh": True},
        {"type": "conversation_history", "role": "ui_history", "model_scope": "recent_window"},
    ]
    if has_compact_summary:
        sources.append({"type": "session_compact", "role": "fallback_summary", "fresh": False})
    for source_type in source_types or []:
        sources.append({"type": str(source_type), "role": "route_context", "fresh": True})

    return ContextAssemblyPlan(
        strategy=strategy,
        budget={
            "estimated_canon_items": int(estimated_canon_items or 0),
            "estimated_context_tokens": int(estimated_context_tokens or 0),
            "context_budget_tokens": int(context_budget_tokens or 0),
        },
        sources=sources,
        degradation=[],
    )
