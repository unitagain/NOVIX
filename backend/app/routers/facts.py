# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  事实树路由 - 提供分卷/章节/事实的树形结构 API 端点，用于事实百科浏览和查询。
  Facts tree router - Provides volume/chapter/fact hierarchical API endpoints for Facts Encyclopedia browsing and query.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional
import re

from fastapi import APIRouter

from app.dependencies import get_canon_storage, get_draft_storage, get_volume_storage
from app.utils.chapter_id import ChapterIDValidator


class SummaryView:
    """Lightweight summary view for legacy compatibility."""

    def __init__(
        self,
        chapter: str,
        volume_id: str,
        title: str,
        brief_summary: str,
        new_facts: Optional[List[Any]] = None,
    ) -> None:
        self.chapter = chapter
        self.volume_id = volume_id
        self.title = title
        self.brief_summary = brief_summary
        self.new_facts = new_facts or []


router = APIRouter(prefix="/projects/{project_id}/facts", tags=["facts"])
canon_storage = get_canon_storage()
draft_storage = get_draft_storage()
volume_storage = get_volume_storage()


def _volume_sort_key(volume_id: str) -> int:
    if volume_id and volume_id.upper().startswith("V"):
        num = volume_id[1:]
        if num.isdigit():
            return int(num)
    return 0


def _derive_title(text: str, max_len: int = 24) -> str:
    if not text:
        return ""
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rstrip() + "..."


def _normalize_chapter_id(raw: str) -> str:
    if not raw:
        return "V1C0"
    cleaned = raw.strip()
    if not cleaned:
        return "V1C0"

    normalized = cleaned.upper()
    if normalized.lower().startswith("ch"):
        normalized = "C" + normalized[2:]

    if ChapterIDValidator.validate(normalized):
        if normalized.startswith("C"):
            return f"V1{normalized}"
        return normalized

    lowered = cleaned.lower()
    match = re.match(r"^vol(\d+)[\._-]?c(\d+)$", lowered)
    if match:
        return f"V{match.group(1)}C{match.group(2)}".upper()

    match = re.match(r"^(\d+)[\._-]?c(\d+)$", lowered)
    if match:
        return f"V{match.group(1)}C{match.group(2)}".upper()

    match = re.match(r"^c(\d+)$", lowered)
    if match:
        return f"V1C{match.group(1)}".upper()

    return "V1C0"


def _display_fact_id(fact_id: str, index: int) -> str:
    if not fact_id:
        return f"F{index + 1:02d}"
    cleaned = fact_id.strip()
    if re.fullmatch(r"[a-fA-F0-9]{10,}", cleaned) or re.fullmatch(r"[a-fA-F0-9-]{16,}", cleaned):
        return f"F{index + 1:02d}"
    return cleaned


def _normalize_text(text_value: str) -> str:
    if not text_value:
        return ""
    cleaned = re.sub(r"\s+", "", str(text_value)).lower()
    return cleaned


def _normalize_summary_fact(
    chapter_id: str,
    item: Any,
    index: int,
) -> Dict[str, Any]:
    if isinstance(item, dict):
        statement = item.get("statement") or item.get("content") or item.get("text") or ""
        title = item.get("title") or item.get("name") or _derive_title(statement)
        content = item.get("content") or statement
    else:
        statement = str(item) if item is not None else ""
        title = _derive_title(statement)
        content = statement

    return {
        "id": f"S{chapter_id}-{index + 1}",
        "display_id": f"S{index + 1:02d}",
        "title": title,
        "content": content,
        "statement": statement,
        "source": chapter_id,
        "introduced_in": chapter_id,
        "confidence": 1.0,
        "origin": "summary",
    }


async def _load_legacy_summaries(
    project_id: str,
    existing: Dict[str, SummaryView],
) -> Dict[str, SummaryView]:
    summaries_dir = draft_storage.get_project_path(project_id) / "summaries"
    if not summaries_dir.exists():
        return existing

    for file_path in summaries_dir.glob("*_summary.yaml"):
        chapter_id = file_path.stem.replace("_summary", "")
        normalized_id = _normalize_chapter_id(chapter_id)
        if normalized_id in existing:
            continue
        try:
            data = await draft_storage.read_yaml(file_path)
        except Exception:
            continue

        chapter = data.get("chapter") or chapter_id
        chapter = _normalize_chapter_id(chapter)
        volume_id = data.get("volume_id") or ChapterIDValidator.extract_volume_id(chapter) or "V1"
        title = data.get("title") or data.get("chapter_title") or data.get("name") or ""
        brief_summary = data.get("brief_summary") or data.get("summary") or data.get("brief") or ""
        new_facts = data.get("new_facts") or data.get("facts") or []
        if isinstance(new_facts, str):
            new_facts = [new_facts]

        existing[chapter] = SummaryView(
            chapter=chapter,
            volume_id=volume_id,
            title=title,
            brief_summary=brief_summary,
            new_facts=new_facts,
        )

    return existing


@router.get("/tree")
async def get_facts_tree(project_id: str) -> Dict[str, Any]:
    """Return facts organized by volume and chapter."""
    volumes = await volume_storage.list_volumes(project_id)
    volume_map: Dict[str, Dict[str, Any]] = {}
    for volume in volumes:
        volume_summary = await volume_storage.get_volume_summary(project_id, volume.id)
        summary_text = volume_summary.brief_summary if volume_summary else volume.summary
        volume_map[volume.id] = {
            "id": volume.id,
            "title": volume.title,
            "summary": summary_text,
            "chapters": [],
        }

    summaries = await draft_storage.list_chapter_summaries(project_id)
    summary_map: Dict[str, SummaryView] = {}
    for summary in summaries:
        chapter_id = _normalize_chapter_id(summary.chapter)
        summary_map[chapter_id] = SummaryView(
            chapter=chapter_id,
            volume_id=summary.volume_id or (ChapterIDValidator.extract_volume_id(chapter_id) or "V1"),
            title=summary.title or "",
            brief_summary=summary.brief_summary or "",
            new_facts=summary.new_facts or [],
        )
    summary_map = await _load_legacy_summaries(project_id, summary_map)

    facts_raw = await canon_storage.get_all_facts_raw(project_id)
    facts_by_chapter: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fact in facts_raw:
        raw_chapter = fact.get("introduced_in") or fact.get("source") or ""
        chapter_id = _normalize_chapter_id(raw_chapter)
        fact["introduced_in"] = chapter_id
        fact["source"] = fact.get("source") or chapter_id
        facts_by_chapter[chapter_id].append(fact)

    chapter_ids = set(summary_map.keys()) | set(facts_by_chapter.keys())
    for chapter_id in chapter_ids:
        summary = summary_map.get(chapter_id)
        volume_id = summary.volume_id if summary else None
        if not volume_id:
            volume_id = ChapterIDValidator.extract_volume_id(chapter_id) or "V1"
        if volume_id not in volume_map:
            volume_map[volume_id] = {
                "id": volume_id,
                "title": f"Volume {volume_id[1:]}" if volume_id.upper().startswith("V") else volume_id,
                "summary": None,
                "chapters": [],
            }

    chapters_by_volume: Dict[str, List[str]] = defaultdict(list)
    for chapter_id in chapter_ids:
        summary = summary_map.get(chapter_id)
        volume_id = summary.volume_id if summary else None
        if not volume_id:
            volume_id = ChapterIDValidator.extract_volume_id(chapter_id) or "V1"
        chapters_by_volume[volume_id].append(chapter_id)

    for volume_id, chapter_list in chapters_by_volume.items():
        for chapter_id in ChapterIDValidator.sort_chapters(chapter_list):
            summary = summary_map.get(chapter_id)
            chapter_title = summary.title if summary and summary.title else chapter_id
            chapter_summary = summary.brief_summary if summary else ""

            facts = facts_by_chapter.get(chapter_id, [])
            mapped_facts: List[Dict[str, Any]] = []
            canon_statements = set()
            suppressed_summary_ids = set()
            for idx, fact in enumerate(facts):
                statement = fact.get("statement") or fact.get("content") or ""
                content = fact.get("content") or statement
                canon_statements.add(_normalize_text(statement))
                summary_ref = fact.get("summary_ref")
                if summary_ref:
                    suppressed_summary_ids.add(summary_ref)
                mapped_facts.append(
                    {
                        "id": fact.get("id"),
                        "display_id": _display_fact_id(fact.get("id"), idx),
                        "title": fact.get("title") or _derive_title(statement),
                        "content": content,
                        "statement": statement,
                        "source": fact.get("source"),
                        "introduced_in": fact.get("introduced_in") or chapter_id,
                        "confidence": fact.get("confidence", 1.0),
                        "origin": "canon",
                    }
                )

            summary_facts = summary.new_facts if summary else []
            for idx, item in enumerate(summary_facts or []):
                summary_fact = _normalize_summary_fact(chapter_id, item, idx)
                if summary_fact.get("id") in suppressed_summary_ids:
                    continue
                summary_statement = _normalize_text(summary_fact.get("statement"))
                if summary_statement and summary_statement in canon_statements:
                    continue
                mapped_facts.append(summary_fact)

            volume_map[volume_id]["chapters"].append(
                {
                    "id": chapter_id,
                    "title": chapter_title,
                    "summary": chapter_summary,
                    "facts": mapped_facts,
                }
            )

    sorted_volumes = [volume_map[vid] for vid in sorted(volume_map.keys(), key=_volume_sort_key)]
    for volume in sorted_volumes:
        chapters = volume.get("chapters", [])
        chapters.sort(key=lambda item: ChapterIDValidator.calculate_weight(item.get("id", "")))
        volume["chapters"] = chapters

    return {"volumes": sorted_volumes}
