# -*- coding: utf-8 -*-
"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Helper functions for working memory compilation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.schemas.draft import SceneBrief
from app.services.evidence_service import evidence_service


def _build_focus_terms(scene_brief: Optional[SceneBrief], goal_text: str) -> List[str]:
    terms: List[str] = []
    terms.extend(_extract_terms(goal_text))

    characters = getattr(scene_brief, "characters", []) or []
    for item in characters[:6]:
        name = ""
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
        if name:
            terms.append(name)

    return list(dict.fromkeys([t for t in terms if t]))


def _focus_score_text(text: str, focus_terms: List[str]) -> int:
    if not (text and focus_terms):
        return 0
    return _term_overlap(text, focus_terms)


def _select_focus_facts(facts: List[Any], focus_terms: List[str], limit: int = 12) -> List[str]:
    raw = [str(item).strip() for item in (facts or []) if str(item).strip()]
    if not raw:
        return []
    limit = max(int(limit or 0), 0)
    if limit <= 0:
        return []

    if not focus_terms:
        return raw[:limit]

    scored: List[tuple[int, str]] = []
    for fact in raw:
        scored.append((_focus_score_text(fact, focus_terms), fact))
    scored.sort(key=lambda x: (x[0], len(x[1])), reverse=True)

    focused = [fact for score, fact in scored if score > 0]
    if focused:
        return focused[:limit]

    # If nothing overlaps, keep a small prefix instead of dumping everything.
    return raw[: min(limit, 5)]


def _build_rule_text_to_card(items: List[Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for item in items or []:
        if item.get("type") != "world_rule":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        source = item.get("source") or {}
        card = str(source.get("card") or "").strip()
        if not card:
            continue
        key = _normalize_text(text)
        if not key or key in mapping:
            continue
        mapping[key] = card
    return mapping


def _maybe_prefix_world_rule(text: str, rule_text_to_card: Dict[str, str]) -> str:
    text = str(text or "").strip()
    if not text or not rule_text_to_card:
        return _clean_text_for_memory(text)
    key = _normalize_text(text)
    card = rule_text_to_card.get(key)
    if not card:
        return _clean_text_for_memory(text)
    if text.startswith(f"{card}:") or text.startswith(f"{card}："):
        return _clean_text_for_memory(text)
    return _clean_text_for_memory(f"{card}: {text}".strip())


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", "", str(text).lower())


def _strip_field_prefix(text: str, field: str) -> str:
    text = str(text or "").strip()
    field = str(field or "").strip()
    if not (text and field):
        return text
    prefix = f"{field}:"
    if text.startswith(prefix):
        return text[len(prefix) :].lstrip()
    return text


def _format_material_text(item: Dict[str, Any]) -> str:
    text = str(item.get("text") or "").strip()
    if not text:
        return ""

    item_type = str(item.get("type") or "").strip()
    source = item.get("source") or {}
    card = str(source.get("card") or "").strip()
    field = str(source.get("field") or "").strip()

    if item_type == "world_rule" and card:
        return truncate(_clean_text_for_memory(f"{card}: {text}".strip()), 140)
    if item_type == "world_entity" and card:
        if text == card or text.startswith(f"{card}:") or text.startswith(f"{card}："):
            return truncate(_clean_text_for_memory(text), 140)
        return truncate(_clean_text_for_memory(f"{card}: {text}".strip()), 140)
    if item_type == "character" and card:
        stripped = _strip_field_prefix(text, field)
        if not stripped:
            return card
        return truncate(_clean_text_for_memory(f"{card}: {stripped}".strip()), 140)

    if item_type in {"text_chunk", "summary"}:
        return _truncate_to_boundary(_clean_text_for_memory(text), 120)

    return truncate(_clean_text_for_memory(text), 140)


def _clean_text_for_memory(text: str) -> str:
    """Clean evidence text for working memory (compact, less meta-noise)."""
    text = str(text or "").strip()
    if not text:
        return ""

    # Drop common meta annotations used in cards/briefs.
    for marker in ["\n理由:", "\r\n理由:", "理由:"]:
        if marker in text:
            text = text.split(marker, 1)[0].strip()
            break

    # Remove markdown emphasis which may leak into prose.
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)

    # Normalize ellipsis and whitespace.
    text = text.replace("…", "")
    text = re.sub(r"\.{3,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_to_boundary(text: str, max_len: int) -> str:
    text = str(text or "")
    if not text:
        return ""
    if len(text) <= max_len:
        return text

    head = text[:max_len]
    for punct in ["。", "！", "？", "；", ";", "，", "、", ".", ","]:
        idx = head.rfind(punct)
        if idx >= max(12, max_len // 3):
            return head[: idx + 1].strip()
    return truncate(text, max_len)


def _normalize_for_dedup(text: str) -> str:
    text = _clean_text_for_memory(text)
    text = re.sub(r"[\s\-—–,，。；;、:：()（）\[\]{}<>\"“”'’]", "", text).lower()
    return text


def _dedup_material_lines(lines: List[str]) -> List[str]:
    kept: List[str] = []
    kept_norms: List[str] = []

    for line in lines or []:
        line = str(line or "").strip()
        if not line:
            continue
        norm = _normalize_for_dedup(line)
        if not norm:
            continue

        duplicate_index = None
        for idx, existing in enumerate(kept_norms):
            if norm in existing or existing in norm:
                duplicate_index = idx
                break

        if duplicate_index is None:
            kept.append(line)
            kept_norms.append(norm)
            continue

        # Prefer the longer (more informative) line.
        if len(line) > len(kept[duplicate_index]):
            kept[duplicate_index] = line
            kept_norms[duplicate_index] = norm

    return kept


def _unique_gaps(gaps: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for gap in gaps:
        key = gap.get("text") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(gap)
        if len(result) >= limit:
            break
    return result


def _gap_answered(gap: Dict[str, Any], answers: List[Dict[str, Any]]) -> bool:
    gap_text = str(gap.get("text") or "").strip()
    if not gap_text:
        return False
    gap_norm = _normalize_for_dedup(gap_text)
    for item in answers or []:
        if not isinstance(item, dict):
            continue
        q_text = str(item.get("question") or item.get("text") or "").strip()
        answer_text = str(item.get("answer") or "").strip()
        if not q_text:
            continue
        if _is_invalid_answer_text(answer_text):
            continue
        if gap_text in q_text:
            return True
        q_norm = _normalize_for_dedup(q_text)
        if gap_norm and q_norm and gap_norm in q_norm:
            return True
    return False


def _answered_gap_texts_from_answers(gaps: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> set:
    answered = set()
    for gap in gaps or []:
        gap_text = str(gap.get("text") or "").strip()
        if not gap_text:
            continue
        if _gap_answered(gap, answers):
            answered.add(gap_text)
    return answered


def _unknown_gap_texts_from_answers(gaps: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> set:
    unknown = set()
    for gap in gaps or []:
        gap_text = str(gap.get("text") or "").strip()
        if not gap_text:
            continue
        gap_norm = _normalize_for_dedup(gap_text)
        for item in answers or []:
            if not isinstance(item, dict):
                continue
            q_text = str(item.get("question") or item.get("text") or "").strip()
            answer_text = str(item.get("answer") or "").strip()
            if not q_text:
                continue
            if not _is_invalid_answer_text(answer_text):
                continue
            if gap_text in q_text:
                unknown.add(gap_text)
                break
            q_norm = _normalize_for_dedup(q_text)
            if gap_norm and q_norm and gap_norm in q_norm:
                unknown.add(gap_text)
                break
    return unknown


def _answered_gap_texts_from_memory(
    gaps: List[Dict[str, Any]],
    memory_items: List[Dict[str, Any]],
    chapter: str,
) -> set:
    answered = set()
    for gap in gaps or []:
        gap_text = str(gap.get("text") or "").strip()
        if not gap_text:
            continue
        for item in memory_items or []:
            source = item.get("source") or {}
            question = str(source.get("question") or "").strip()
            question_key = str(source.get("question_key") or "").strip()
            if question_key and question_key == _make_question_key(chapter, gap.get("kind"), gap_text):
                answered.add(gap_text)
                break
            if question and gap_text in question:
                answered.add(gap_text)
                break
    return answered


async def _load_chapter_answer_memory_items(project_id: str, chapter: str) -> List[Dict[str, Any]]:
    try:
        await evidence_service.build_all(project_id, force=False)
        items = await evidence_service.index_storage.read_items(project_id, evidence_service.MEMORY_INDEX)
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for item in items or []:
        if getattr(item, "type", None) != "memory":
            continue
        source = getattr(item, "source", {}) or {}
        meta = getattr(item, "meta", {}) or {}
        if str(meta.get("kind") or "") != "user_answer":
            continue
        if str(source.get("chapter") or "") != str(chapter):
            continue
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        results.append(
            {
                "id": getattr(item, "id", ""),
                "type": "memory",
                "text": text,
                # Give persisted answers a strong weight so they reliably enter the pack.
                "score": 10.0,
                "source": source,
                "meta": meta,
            }
        )
    return results


def _query_hits(text: str, queries: List[str]) -> bool:
    for query in queries:
        if not query:
            continue
        terms = _extract_terms(query)
        if not terms:
            continue
        if _term_overlap(text, terms) > 0:
            return True
    return False


def _extract_terms(text: str) -> List[str]:
    text = (text or "").lower()
    terms: List[str] = []
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) == 1:
            terms.append(block)
            continue
        for n in (2, 3):
            if len(block) < n:
                continue
            for i in range(0, len(block) - n + 1):
                terms.append(block[i : i + n])
    terms.extend(re.findall(r"[a-z0-9]+", text))
    return list(dict.fromkeys(terms))


def _term_overlap(text: str, terms: List[str]) -> int:
    count = 0
    for term in terms:
        if term and term in text:
            count += 1
    return count


def _is_focus_related(text: str, focus_terms: List[str]) -> bool:
    if not text or not focus_terms:
        return False
    return _term_overlap(text, focus_terms) > 0


def _merge_chapter_window(primary: List[str], secondary: List[str]) -> Optional[List[str]]:
    merged: List[str] = []
    for items in (primary or [], secondary or []):
        for chapter in items:
            value = str(chapter or "").strip()
            if value and value not in merged:
                merged.append(value)
    return merged or None


def _unique_texts(items: List[Any]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _safe_score(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("score") or 0)
    except Exception:
        return 0.0


def _item_stars(item: Dict[str, Any]) -> int:
    meta = item.get("meta") or {}
    try:
        stars = int(meta.get("stars") or 1)
    except Exception:
        return 1
    return max(1, min(stars, 3))


def _should_include_material(item: Dict[str, Any]) -> bool:
    text = str(item.get("text") or "").strip()
    if not text:
        return False

    source = item.get("source") or {}
    field = str(source.get("field") or "").strip()
    item_type = str(item.get("type") or "").strip()
    stars = _item_stars(item)
    score = _safe_score(item)
    meta = item.get("meta") or {}

    if item_type == "memory" and str(meta.get("kind") or "") == "user_unknown":
        return False
    if item_type == "memory" and str(meta.get("kind") or "") == "research_trace":
        # Trace memories are for observability/debugging and should never pollute
        # the writer-facing working memory context.
        return False

    if text.startswith("aliases:") or field == "aliases":
        if item_type == "character" and stars <= 1:
            return True
        return False
    if text.startswith("category:") or field == "category" or text.startswith("类别:"):
        if item_type == "world_entity" and stars >= 2:
            return True
        return False
    if text.startswith("immutable:") or field == "immutable":
        return False
    if text.startswith("理由:"):
        return False
    if any(
        text.startswith(prefix)
        for prefix in ["description: 理由", "identity: 理由", "appearance: 理由", "motivation: 理由"]
    ):
        return False

    if item_type == "world_entity" and stars <= 1 and score < 2.5:
        return False

    if item_type == "character":
        if stars <= 1:
            return field in {"description", "aliases"}
        if stars == 2:
            return field in {"description", "identity", "motivation", "relationships", "appearance", "arc"}

    return True


def _dedup_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        item_id = item.get("id") or ""
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        result.append(item)
    return result


def _count_types(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        t = item.get("type")
        if not t:
            continue
        counts[t] = counts.get(t, 0) + 1
    return counts


def _answer_text(answer: Dict[str, Any]) -> str:
    question = str(answer.get("question") or answer.get("text") or "").strip()
    reply = str(answer.get("answer") or "").strip()
    if question and reply:
        return f"{question} -> {reply}"
    return reply or question


def _is_invalid_answer_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return True
    invalid_terms = {
        "不知道",
        "不清楚",
        "不确定",
        "无",
        "没有",
        "随便",
        "都行",
        "不会",
        "不懂",
    }
    return normalized in invalid_terms


def _sanitize_answer_items(answers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for item in answers:
        if not isinstance(item, dict):
            continue
        text = _answer_text(item)
        if not text:
            continue
        cleaned.append(item)
    return cleaned


def _answer_to_evidence_items(answers: List[Dict[str, Any]], chapter: Optional[str] = None) -> List[Dict[str, Any]]:
    import time

    items = []
    timestamp = int(time.time())
    for idx, answer in enumerate(_sanitize_answer_items(answers)):
        question_text = str(answer.get("question") or answer.get("text") or "").strip()
        reply = str(answer.get("answer") or "").strip()
        invalid = _is_invalid_answer_text(reply)
        if invalid:
            if reply:
                text = f"{question_text} -> {reply}" if question_text else reply
            else:
                text = f"{question_text} -> [用户未回答]" if question_text else "[用户未回答]"
        else:
            text = _answer_text(answer)
        if not text:
            continue
        question_key = answer.get("key") or answer.get("question_key")
        if not question_key and chapter:
            question_key = _make_question_key(chapter, answer.get("type"), question_text)
        items.append(
            {
                "id": f"memory:answer:{timestamp}:{idx + 1}",
                "type": "memory",
                "text": text,
                "score": 1.0,
                "source": {
                    "question": question_text,
                    "question_key": question_key,
                    "kind": answer.get("type"),
                },
                "meta": {"kind": "user_unknown" if invalid else "user_answer"},
            }
        )
    return items


def _make_question_key(chapter: Optional[str], q_type: Optional[str], text: Optional[str]) -> str:
    base = f"{chapter or ''}|{q_type or ''}|{text or ''}".strip()
    return _normalize_for_dedup(base)
