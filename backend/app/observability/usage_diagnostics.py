"""Privacy-safe local usage signals for backend policy decisions."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from app.observability.runtime_metrics import RuntimeMetrics, runtime_metrics


_TOOL_BUCKETS = {
    "lookup_card": "card",
    "query_canon": "canon",
    "query_relations": "canon",
    "read_chapter": "prose",
    "search_prose": "prose",
    "write_content": "write",
    "edit_lines": "edit",
}
_SOURCE_BUCKETS = {"style", "card", "canon", "memory", "summary", "draft", "prose", "user_message"}
_RUN_STATES = {"completed", "incomplete", "cancelled", "failed", "degraded"}


def tool_bucket(name: str) -> str:
    return _TOOL_BUCKETS.get(str(name or ""), "other")


def source_bucket(name: str) -> str:
    normalized = str(name or "").strip().lower()
    normalized = {
        "cards": "card",
        "character_card": "card",
        "world_card": "card",
        "style_card": "style",
        "summaries": "summary",
        "chapter_summary": "summary",
        "relations": "canon",
        "fact": "canon",
        "text_chunk": "prose",
    }.get(normalized, normalized)
    return normalized if normalized in _SOURCE_BUCKETS else "other"


def record_agent_run(*, status: str, iterations: int, tool_calls: int, elapsed_ms: int) -> None:
    normalized = str(status or "failed").lower()
    if normalized not in _RUN_STATES:
        normalized = "failed"
    runtime_metrics.increment(f"usage.agent.status.{normalized}")
    runtime_metrics.observe("usage.agent.iterations", max(0, int(iterations)))
    runtime_metrics.observe("usage.agent.tool_calls", max(0, int(tool_calls)))
    runtime_metrics.observe("usage.agent.elapsed_ms", max(0, int(elapsed_ms)))


def record_tool_call(name: str) -> None:
    runtime_metrics.increment(f"usage.tool.{tool_bucket(name)}")


def record_fallback(reason_category: str) -> None:
    category = str(reason_category or "unknown").strip().lower()
    if category not in {
        "iteration_limit",
        "deadline",
        "capability",
        "no_tool_calls",
        "provider_failure",
        "tool_failure",
        "routing",
        "unknown",
    }:
        category = "unknown"
    runtime_metrics.increment("usage.agent.fallback")
    runtime_metrics.increment(f"usage.agent.fallback_reason.{category}")


def record_source_usage(source_type: str, action: str) -> None:
    normalized_action = str(action or "used").lower()
    if normalized_action not in {"available", "selected", "requested", "used", "omitted"}:
        normalized_action = "used"
    runtime_metrics.increment(f"usage.source.{source_bucket(source_type)}.{normalized_action}")


def record_provider_usage(usage: Dict[str, Any] | None) -> None:
    row = usage or {}
    runtime_metrics.increment("usage.provider.requests")
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_read_tokens", "cache_creation_tokens"):
        if key in row and row.get(key) is not None:
            runtime_metrics.increment(f"usage.provider.{key}", float(row.get(key) or 0))
    if row.get("cache_read_tokens") is None and row.get("cache_creation_tokens") is None:
        runtime_metrics.increment("usage.provider.cache_usage_unavailable")


def record_ttft(milliseconds: float) -> None:
    runtime_metrics.observe("usage.provider.ttft_ms", max(0.0, float(milliseconds)))


def record_edit_assembly(*, draft_tokens: int, pushed_tokens: int, projected: bool) -> None:
    original = max(0, int(draft_tokens))
    pushed = max(0, int(pushed_tokens))
    runtime_metrics.observe("usage.edit.draft_tokens", original)
    runtime_metrics.observe("usage.edit.pushed_tokens", pushed)
    runtime_metrics.observe("usage.edit.projection_ratio", pushed / max(1, original))
    if projected:
        runtime_metrics.increment("usage.edit.projected")


def record_edit_miss() -> None:
    runtime_metrics.increment("usage.edit.unique_match_miss")


def record_governance(metrics: Dict[str, Any]) -> None:
    runtime_metrics.observe("usage.memory.review_backlog", float(metrics.get("review_backlog") or 0))
    runtime_metrics.observe(
        "usage.memory.review_median_age_seconds", float(metrics.get("review_median_age_seconds") or 0)
    )
    runtime_metrics.observe("usage.memory.auto_active_count", float(metrics.get("auto_active_count") or 0))


def record_memory_recall(result_count: int) -> None:
    runtime_metrics.increment("usage.memory.lexical_queries")
    if int(result_count or 0) <= 0:
        runtime_metrics.increment("usage.memory.lexical_empty")


def memory_semantic_recall_decision(
    *,
    lexical_queries: int,
    labeled_semantic_misses: int,
    minimum_labeled_misses: int = 20,
) -> Dict[str, Any]:
    """Keep semantic recall disabled until manually verified lexical misses exist."""
    queries = max(0, int(lexical_queries))
    misses = max(0, int(labeled_semantic_misses))
    minimum = max(1, int(minimum_labeled_misses))
    ready = queries > 0 and misses >= minimum
    return {
        "status": "experiment_allowed" if ready else "insufficient_evidence",
        "default_enabled": False,
        "lexical_queries": queries,
        "labeled_semantic_misses": misses,
        "minimum_labeled_misses": minimum,
    }


def record_source_rows(rows: Iterable[Dict[str, Any]], *, action: str = "used") -> None:
    for row in rows:
        record_source_usage(str(row.get("type") or "other"), action)


def build_usage_diagnostics(metrics: RuntimeMetrics = runtime_metrics, *, minimum_turns: int = 10) -> Dict[str, Any]:
    return build_usage_diagnostics_from_snapshot(
        metrics.snapshot(),
        minimum_turns=minimum_turns,
        budget=metrics.budget_report(),
    )


def build_usage_diagnostics_from_snapshot(
    snapshot: Dict[str, Any],
    *,
    minimum_turns: int = 10,
    budget: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a content-free report from an in-process or health-endpoint snapshot."""
    counters = snapshot.get("counters") or {}
    terminal = {
        state: int(counters.get(f"usage.agent.status.{state}") or 0)
        for state in sorted(_RUN_STATES)
    }
    runs = sum(terminal.values())
    return {
        "schema_version": 1,
        "classification": "content_free_local_metrics",
        "status": "ready" if runs >= max(1, int(minimum_turns)) else "insufficient_evidence",
        "sample_turns": runs,
        "minimum_turns": max(1, int(minimum_turns)),
        "terminal_states": terminal,
        "fallback_count": int(counters.get("usage.agent.fallback") or 0),
        "agent_iterations": (snapshot.get("histograms") or {}).get("usage.agent.iterations", {}),
        "tool_calls_per_turn": (snapshot.get("histograms") or {}).get("usage.agent.tool_calls", {}),
        "ttft_ms": (snapshot.get("histograms") or {}).get("usage.provider.ttft_ms", {}),
        "review_backlog": (snapshot.get("histograms") or {}).get("usage.memory.review_backlog", {}),
        "edit_projection_ratio": (snapshot.get("histograms") or {}).get("usage.edit.projection_ratio", {}),
        "edit_unique_match_miss": int(counters.get("usage.edit.unique_match_miss") or 0),
        "memory_lexical_queries": int(counters.get("usage.memory.lexical_queries") or 0),
        "memory_lexical_empty": int(counters.get("usage.memory.lexical_empty") or 0),
        "cache_usage_available": int(counters.get("usage.provider.cache_usage_unavailable") or 0)
        < int(counters.get("usage.provider.requests") or 0),
        "budget": dict(budget or {}),
    }
