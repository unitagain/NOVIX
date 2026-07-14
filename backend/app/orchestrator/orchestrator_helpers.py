"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Helper utilities for orchestrator context/debug workflows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.context_engine.token_accounting import count_text_tokens
from app.utils.chapter_id import ChapterIDValidator


def normalize_chapter_id(chapter_id: str) -> str:
    """Normalize chapter id into canonical internal format."""
    if not chapter_id:
        return chapter_id
    normalized = str(chapter_id).strip().upper()
    if not normalized:
        return chapter_id
    if normalized.startswith("CH"):
        normalized = "C" + normalized[2:]
    if ChapterIDValidator.validate(normalized):
        if normalized.startswith("C"):
            return f"V1{normalized}"
        return normalized
    return str(chapter_id).strip()


def full_canon_eligible(
    n_facts: int, n_cards: int, fact_tokens: int, *, max_facts: int, max_cards: int, max_tokens: int
) -> bool:
    """Phase 13 · 全 canon 注入资格：数量 + token 双门槛（防少量超长事实撑爆全注入预算）。"""
    return n_facts <= max_facts and n_cards <= max_cards and fact_tokens <= max_tokens


def estimate_context_tokens(context_package: Dict[str, Any]) -> int:
    """Estimate token usage for context package."""
    total = 0
    for key in ["full_facts", "summary_with_events", "summary_only", "title_only", "volume_summaries"]:
        for item in context_package.get(key, []) or []:
            total += count_text_tokens(str(item)).upper_bound_tokens
    return total


def trim_context_package(context_package: Dict[str, Any], max_tokens: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Trim low-priority context lists to fit max token budget."""
    trimmed = dict(context_package or {})
    for key in ["full_facts", "summary_with_events", "summary_only", "title_only", "volume_summaries"]:
        trimmed[key] = list(trimmed.get(key, []) or [])

    before = estimate_context_tokens(trimmed)
    if before <= max_tokens:
        return trimmed, {"trimmed": False, "before": before, "after": before}

    if max_tokens <= 0:
        for key in ["summary_with_events", "summary_only", "title_only", "volume_summaries"]:
            trimmed[key] = []
        return trimmed, {"trimmed": True, "before": before, "after": estimate_context_tokens(trimmed)}

    removal_order = ["title_only", "volume_summaries", "summary_only", "summary_with_events"]
    while estimate_context_tokens(trimmed) > max_tokens:
        removed_any = False
        for key in removal_order:
            if trimmed[key]:
                trimmed[key].pop()
                removed_any = True
                if estimate_context_tokens(trimmed) <= max_tokens:
                    break
        if not removed_any:
            break

    after = estimate_context_tokens(trimmed)
    return trimmed, {"trimmed": True, "before": before, "after": after}


def extract_scene_brief_names(scene_brief: Any, limit: int = 3) -> List[str]:
    """Extract unique character names from scene brief."""
    names: List[str] = []
    items = getattr(scene_brief, "characters", []) or []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
        if name:
            names.append(name)
    unique: List[str] = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique[:limit]


def extract_top_sources(evidence_groups: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    """Extract top non-memory evidence source snippets for debug payload."""
    items: List[Dict[str, Any]] = []
    for group in evidence_groups or []:
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "memory":
                continue
            items.append(item)
    items.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

    top_sources: List[Dict[str, Any]] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        source = item.get("source") or {}
        source_summary: Dict[str, Any] = {}
        for key in ["chapter", "draft", "path", "paragraph", "field", "fact_id", "card", "introduced_in"]:
            if source.get(key) is not None:
                source_summary[key] = source.get(key)
        top_sources.append(
            {
                "type": item.get("type") or "",
                "score": float(item.get("score") or 0),
                "snippet": text[:80],
                "source": source_summary,
            }
        )
        if len(top_sources) >= limit:
            break
    return top_sources


def build_context_debug(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build debug context payload for frontend observability."""
    if not payload:
        return None
    return {
        "working_memory": payload.get("working_memory"),
        "gaps": payload.get("gaps"),
        "unresolved_gaps": payload.get("unresolved_gaps"),
        "seed_entities": payload.get("seed_entities"),
        "seed_window": payload.get("seed_window"),
        "retrieval_requests": payload.get("retrieval_requests"),
        "evidence_pack": payload.get("evidence_pack"),
        "research_trace": payload.get("research_trace"),
        "research_stop_reason": payload.get("research_stop_reason"),
        "sufficiency_report": payload.get("sufficiency_report"),
    }
