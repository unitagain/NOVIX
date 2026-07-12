# -*- coding: utf-8 -*-
"""P8 contextual prefix helpers.

Canonical facts already support context_prefix. P8 makes prefix generation and
coverage measurement consistent across canon, summary, draft and memory items.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _get(payload: Any, key: str, default: Any = "") -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _compact_parts(parts: Iterable[Any], *, max_chars: int = 240) -> str:
    cleaned: List[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    prefix = " | ".join(cleaned)
    if len(prefix) > max_chars:
        return prefix[: max(0, max_chars - 3)].rstrip() + "..."
    return prefix


def build_contextual_prefix(item_type: str, payload: Any) -> str:
    """Build a short retrieval prefix with type, location, scope and source."""

    kind = str(item_type or _get(payload, "type") or _get(payload, "kind") or "context").strip().lower()
    source = _get(payload, "source") or _get(payload, "chapter") or _get(payload, "chapter_id")
    introduced = _get(payload, "introduced_in")
    scope = _get(payload, "scope") or _get(payload, "project_id")
    title = _get(payload, "title") or _get(payload, "slug") or _get(payload, "id")
    existing = _get(payload, "context_prefix") or _get(payload, "context")

    if kind in {"canon", "fact"}:
        parts = ["type:canon_fact", f"source:{source or introduced}", f"scope:{scope}", existing]
    elif kind in {"summary", "chapter_summary"}:
        parts = ["type:summary", f"chapter:{source}", f"title:{title}", existing]
    elif kind in {"draft", "chapter", "prose"}:
        parts = ["type:draft", f"chapter:{source}", f"title:{title}", existing]
    elif kind in {"memory", "creative_memory"}:
        memory_type = _get(payload, "memory_type") or _get(payload, "type")
        parts = ["type:memory", f"memory_type:{memory_type}", f"scope:{scope}", f"source:{source}", existing]
    else:
        parts = [f"type:{kind}", f"source:{source}", f"scope:{scope}", existing]
    return _compact_parts(parts)


def ensure_contextual_prefix(item_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with context_prefix populated when missing."""

    item = dict(payload or {})
    if not str(item.get("context_prefix") or "").strip():
        item["context_prefix"] = build_contextual_prefix(item_type, item)
    return item


def prefix_coverage(items: Iterable[Any]) -> Dict[str, Any]:
    """Measure contextual prefix coverage for eval and governance dashboards."""

    rows = list(items or [])
    total = len(rows)
    with_prefix = 0
    by_type: Dict[str, Dict[str, int]] = {}
    for item in rows:
        kind = str(_get(item, "type") or _get(item, "kind") or "unknown")
        has_prefix = bool(str(_get(item, "context_prefix") or _get(item, "context") or "").strip())
        bucket = by_type.setdefault(kind, {"total": 0, "with_prefix": 0})
        bucket["total"] += 1
        if has_prefix:
            with_prefix += 1
            bucket["with_prefix"] += 1
    by_type_rate = {
        key: {**value, "coverage": value["with_prefix"] / value["total"] if value["total"] else 1.0}
        for key, value in by_type.items()
    }
    return {
        "total": total,
        "with_prefix": with_prefix,
        "missing": total - with_prefix,
        "coverage": with_prefix / total if total else 1.0,
        "by_type": by_type_rate,
    }
