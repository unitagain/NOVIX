"""Single-writer terminal contract for VibeWriting turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List

CHANGE_TYPES = {"conversation", "prose_edit", "chapter_write", "plot_edit"}
FACT_OPERATIONS = {"none", "merge", "replace_chapter"}
FACT_CHANGE_TYPES = {"chapter_write", "plot_edit"}


def _compact_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_turn_effect(
    payload: Any,
    *,
    changed: bool | None = None,
    had_draft: bool | None = None,
) -> Dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    change_type = str(data.get("change_type") or "").strip().lower()
    if change_type not in CHANGE_TYPES:
        if changed:
            change_type = "prose_edit" if had_draft else "chapter_write"
        else:
            change_type = "conversation"
    if changed is False:
        change_type = "conversation"
    elif changed is True and change_type == "conversation":
        change_type = "prose_edit" if had_draft else "chapter_write"

    operation = str(data.get("fact_operation") or "").strip().lower()
    if operation not in FACT_OPERATIONS:
        operation = "replace_chapter" if change_type == "chapter_write" else "merge" if change_type == "plot_edit" else "none"
    if change_type not in FACT_CHANGE_TYPES:
        operation = "none"

    facts: List[Dict[str, str]] = []
    if operation != "none":
        for item in list(data.get("fact_candidates") or [])[:5]:
            if not isinstance(item, dict):
                continue
            statement = _compact_text(item.get("statement"), 500)
            evidence = _compact_text(item.get("evidence"), 500)
            if not statement or not evidence:
                continue
            facts.append(
                {
                    "statement": statement,
                    "evidence": evidence,
                    "category": _compact_text(item.get("category"), 80) or "story_fact",
                }
            )

    return {
        "change_type": change_type,
        "fact_operation": operation,
        "chapter_summary": _compact_text(data.get("chapter_summary"), 1200),
        "fact_candidates": facts,
        "message": _compact_text(data.get("message"), 500),
    }


def evidence_exists(content: str, evidence: str) -> bool:
    body = str(content or "")
    quote = str(evidence or "").strip()
    if not body or not quote:
        return False
    if quote in body:
        return True
    normalize = lambda value: re.sub(r"\s+", "", str(value or ""))
    return bool(normalize(quote) and normalize(quote) in normalize(body))


def validated_fact_candidates(effect: Dict[str, Any], content: str) -> List[Dict[str, str]]:
    return [
        dict(item)
        for item in list(effect.get("fact_candidates") or [])
        if isinstance(item, dict) and evidence_exists(content, str(item.get("evidence") or ""))
    ]
