# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  证据索引服务 - 跨事实表、摘要和卡片构建和搜索证据索引，支持 BM25 排序和种子实体增强。
  Evidence indexing and retrieval - Builds and searches evidence indices across facts, summaries, and cards with BM25 scoring and entity-based ranking.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from app.schemas.evidence import EvidenceItem, EvidenceIndexMeta
from app.storage.cards import CardStorage
from app.storage.canon import CanonStorage
from app.storage.drafts import DraftStorage
from app.storage.evidence_index import EvidenceIndexStorage
from app.storage.volumes import VolumeStorage
from app.services.text_chunk_service import (
    _average_doc_len,
    _bm25_score,
    _estimate_doc_len,
    _extract_terms,
    _count_term,
)


class EvidenceIndexService:
    """
    证据索引管理服务 - 跨多个数据源构建和维护证据索引。

    Manages evidence indices across facts, summaries, and character/world cards.
    Provides BM25-based search with optional quotas and entity-based relevance ranking.

    Attributes:
        FACTS_INDEX: 事实索引标识 / Facts index name
        SUMMARIES_INDEX: 摘要索引标识 / Summaries index name
        CARDS_INDEX: 卡片索引标识 / Cards (character/world) index name
        MEMORY_INDEX: 记忆索引标识 (append-only) / Memory index name (append-only)
    """

    FACTS_INDEX = "facts"
    SUMMARIES_INDEX = "summaries"
    CARDS_INDEX = "cards"
    MEMORY_INDEX = "memory"

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.card_storage = CardStorage(data_dir)
        self.canon_storage = CanonStorage(data_dir)
        self.draft_storage = DraftStorage(data_dir)
        self.volume_storage = VolumeStorage(data_dir)
        self.index_storage = EvidenceIndexStorage(data_dir)

    async def build_all(self, project_id: str, force: bool = False) -> Dict[str, EvidenceIndexMeta]:
        """Build all evidence indices.

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Mapping of index name to metadata.
        """
        return {
            self.FACTS_INDEX: await self.build_facts_index(project_id, force=force),
            self.SUMMARIES_INDEX: await self.build_summaries_index(project_id, force=force),
            self.CARDS_INDEX: await self.build_cards_index(project_id, force=force),
            self.MEMORY_INDEX: await self.build_memory_index(project_id, force=force),
        }

    async def build_facts_index(self, project_id: str, force: bool = False) -> EvidenceIndexMeta:
        """Build the facts evidence index.

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Index metadata.
        """
        latest_mtime = self._facts_mtime(project_id)
        existing = await self._maybe_use_existing(project_id, self.FACTS_INDEX, latest_mtime, force)
        if existing:
            return existing

        raw_facts = await self.canon_storage.get_all_facts_raw(project_id)
        items: List[EvidenceItem] = []
        seen = set()
        for fact in raw_facts:
            statement = str(fact.get("statement") or fact.get("content") or "").strip()
            if not statement:
                continue
            norm = _normalize_text(statement)
            if norm in seen:
                continue
            seen.add(norm)
            fact_id = str(fact.get("id") or "").strip() or "unknown"
            items.append(
                EvidenceItem(
                    id=f"fact:{fact_id}",
                    type="fact",
                    text=statement,
                    source={
                        "fact_id": fact_id,
                        "introduced_in": fact.get("introduced_in"),
                        "source": fact.get("source"),
                        "path": "canon/facts.jsonl",
                    },
                    scope="chapter",
                    entities=[],
                    meta={
                        "confidence": fact.get("confidence", 1.0),
                        "title": fact.get("title") or "",
                        "doc_len": _estimate_doc_len(statement),
                    },
                )
            )

        meta = EvidenceIndexMeta(
            index_name=self.FACTS_INDEX,
            built_at=time.time(),
            item_count=len(items),
            source_mtime=latest_mtime or None,
            details={},
        )
        await self.index_storage.write_items(project_id, self.FACTS_INDEX, items)
        await self.index_storage.write_meta(project_id, self.FACTS_INDEX, meta)
        return meta

    async def build_summaries_index(self, project_id: str, force: bool = False) -> EvidenceIndexMeta:
        """Build the summary evidence index.

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Index metadata.
        """
        latest_mtime = self._summaries_mtime(project_id)
        existing = await self._maybe_use_existing(project_id, self.SUMMARIES_INDEX, latest_mtime, force)
        if existing:
            return existing

        items: List[EvidenceItem] = []

        summaries = await self.draft_storage.list_chapter_summaries(project_id)
        for summary in summaries:
            chapter_id = summary.chapter
            items.extend(self._summary_items(chapter_id, summary))

        volume_summaries = await self.volume_storage.list_volumes(project_id)
        for volume in volume_summaries:
            volume_summary = await self.volume_storage.get_volume_summary(project_id, volume.id)
            if not volume_summary:
                continue
            summary_text = str(volume_summary.brief_summary or "").strip()
            if summary_text:
                items.append(
                    EvidenceItem(
                        id=f"summary:{volume.id}:brief",
                        type="summary",
                        text=summary_text,
                        source={
                            "volume": volume.id,
                            "path": f"volumes/{volume.id}.yaml",
                            "field": "brief_summary",
                        },
                        scope="volume",
                        entities=[],
                        meta={"doc_len": _estimate_doc_len(summary_text)},
                    )
                )

        meta = EvidenceIndexMeta(
            index_name=self.SUMMARIES_INDEX,
            built_at=time.time(),
            item_count=len(items),
            source_mtime=latest_mtime or None,
            details={},
        )
        await self.index_storage.write_items(project_id, self.SUMMARIES_INDEX, items)
        await self.index_storage.write_meta(project_id, self.SUMMARIES_INDEX, meta)
        return meta

    async def build_cards_index(self, project_id: str, force: bool = False) -> EvidenceIndexMeta:
        """Build the character/world/style card evidence index.

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Index metadata.
        """
        latest_mtime = self._cards_mtime(project_id)
        existing = await self._maybe_use_existing(project_id, self.CARDS_INDEX, latest_mtime, force)
        if existing:
            return existing

        items: List[EvidenceItem] = []

        character_names = await self.card_storage.list_character_cards(project_id)
        for name in character_names:
            items.extend(await self._character_items(project_id, name))

        world_names = await self.card_storage.list_world_cards(project_id)
        for name in world_names:
            items.extend(await self._world_items(project_id, name))

        style_card = await self.card_storage.get_style_card(project_id)
        if style_card:
            style_text = str(getattr(style_card, "style", "") or "").strip()
            if style_text:
                items.append(
                    EvidenceItem(
                        id="style:card",
                        type="style",
                        text=style_text,
                        source={"path": "cards/style.yaml"},
                        scope="global",
                        entities=[],
                        meta={"doc_len": _estimate_doc_len(style_text)},
                    )
                )

        meta = EvidenceIndexMeta(
            index_name=self.CARDS_INDEX,
            built_at=time.time(),
            item_count=len(items),
            source_mtime=latest_mtime or None,
            details={"characters": len(character_names), "world": len(world_names)},
        )
        await self.index_storage.write_items(project_id, self.CARDS_INDEX, items)
        await self.index_storage.write_meta(project_id, self.CARDS_INDEX, meta)
        return meta

    async def build_memory_index(self, project_id: str, force: bool = False) -> EvidenceIndexMeta:
        """Build the memory evidence index (append-only).

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Index metadata.
        """
        latest_mtime = self._memory_mtime(project_id)
        existing = await self._maybe_use_existing(project_id, self.MEMORY_INDEX, latest_mtime, force)
        if existing:
            return existing

        items = await self.index_storage.read_items(project_id, self.MEMORY_INDEX)
        meta = EvidenceIndexMeta(
            index_name=self.MEMORY_INDEX,
            built_at=time.time(),
            item_count=len(items),
            source_mtime=latest_mtime or None,
            details={},
        )
        await self.index_storage.write_items(project_id, self.MEMORY_INDEX, items)
        await self.index_storage.write_meta(project_id, self.MEMORY_INDEX, meta)
        return meta

    async def search(
        self,
        project_id: str,
        queries: List[str],
        types: Optional[List[str]] = None,
        quotas: Optional[Dict[str, Dict[str, int]]] = None,
        limit: int = 12,
        seed_entities: Optional[List[str]] = None,
        include_text_chunks: bool = True,
        text_chunk_chapters: Optional[List[str]] = None,
        text_chunk_exclude_chapters: Optional[List[str]] = None,
        rebuild: bool = False,
        semantic_rerank: bool = False,
        rerank_query: Optional[str] = None,
        rerank_top_k: int = 16,
        trace_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search evidence items with type quotas.

        Args:
            project_id: Target project id.
            queries: Query list derived from gaps.
            types: Evidence types to include.
            quotas: Optional per-type quotas: {"fact": {"min": 2, "max": 6}}.
            limit: Total max items.
            seed_entities: Seed entities to boost.
            include_text_chunks: Include text chunks if available.
            text_chunk_chapters: Optional chapter whitelist for text_chunk retrieval.
            text_chunk_exclude_chapters: Optional chapter blacklist for text_chunk retrieval.
            rebuild: Force rebuild indices.
            trace_meta: Optional trace metadata (e.g., round/note).

        Returns:
            Evidence pack with selected items and stats.
        """
        cleaned_queries = [q.strip() for q in (queries or []) if q and q.strip()]
        if not cleaned_queries:
            return {"items": [], "stats": {"total": 0}}

        if rebuild:
            await self.build_all(project_id, force=True)
        else:
            await self.build_all(project_id, force=False)

        types = types or [
            "fact",
            "summary",
            "character",
            "world_rule",
            "world_entity",
            "style",
            "text_chunk",
            "memory",
        ]
        seed_entities = [s for s in (seed_entities or []) if s]
        quotas = _merge_quotas(types, quotas)

        evidence_items = await self._load_evidence_items(project_id, types)
        scored = self._score_items(evidence_items, cleaned_queries, seed_entities)

        if include_text_chunks and "text_chunk" in types:
            # Text chunks are scored by their own index to avoid scanning the full corpus.
            query_text = " ".join(cleaned_queries)
            text_chunk_hits = await self.draft_storage.search_text_chunks(
                project_id=project_id,
                query=query_text,
                limit=quotas.get("text_chunk", {}).get("max", 8),
                queries=cleaned_queries,
                chapters=text_chunk_chapters,
                exclude_chapters=text_chunk_exclude_chapters,
                rebuild=rebuild,
                semantic_rerank=semantic_rerank,
                rerank_query=rerank_query or query_text,
                rerank_top_k=rerank_top_k,
            )
            scored.extend(_wrap_text_chunks(text_chunk_hits))

        selected = _apply_type_quotas(scored, quotas, limit)
        top_sources = _extract_top_sources(selected, limit=3)
        trace_meta = trace_meta or {}
        return {
            "items": selected,
            "stats": {
                "total": len(selected),
                "types": _count_types(selected),
                "queries": cleaned_queries,
                "hits": len(selected),
                "top_sources": top_sources,
                "semantic_rerank": bool(semantic_rerank and rerank_query),
                "rerank_query": rerank_query or "",
                "rerank_top_k": int(rerank_top_k or 0),
                "limit": int(limit or 0),
                "types_requested": types or [],
                "round": trace_meta.get("round"),
                "note": trace_meta.get("note") or "",
            },
        }

    async def append_memory_items(self, project_id: str, items: List[EvidenceItem]) -> None:
        """Append memory evidence items and refresh metadata.

        Args:
            project_id: Target project id.
            items: Evidence items to append.
        """
        if not items:
            return
        await self.index_storage.append_items(project_id, self.MEMORY_INDEX, items)
        meta = await self.index_storage.read_meta(project_id, self.MEMORY_INDEX)
        if not meta:
            meta = EvidenceIndexMeta(
                index_name=self.MEMORY_INDEX,
                built_at=time.time(),
                item_count=0,
                source_mtime=None,
                details={},
            )
        meta.item_count += len(items)
        meta.built_at = time.time()
        await self.index_storage.write_meta(project_id, self.MEMORY_INDEX, meta)

    async def _maybe_use_existing(
        self,
        project_id: str,
        index_name: str,
        latest_mtime: float,
        force: bool,
    ) -> Optional[EvidenceIndexMeta]:
        if force:
            return None
        meta = await self.index_storage.read_meta(project_id, index_name)
        if not meta:
            return None
        if latest_mtime <= (meta.source_mtime or 0):
            return meta
        return None

    async def _load_evidence_items(
        self,
        project_id: str,
        types: List[str],
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        if any(t in types for t in ["fact"]):
            items.extend(await self.index_storage.read_items(project_id, self.FACTS_INDEX))
        if any(t in types for t in ["summary"]):
            items.extend(await self.index_storage.read_items(project_id, self.SUMMARIES_INDEX))
        if any(t in types for t in ["character", "world", "world_rule", "world_entity", "style"]):
            items.extend(await self.index_storage.read_items(project_id, self.CARDS_INDEX))
        if any(t in types for t in ["memory"]):
            items.extend(await self.index_storage.read_items(project_id, self.MEMORY_INDEX))
        return [item for item in items if item.type in types]

    def _score_items(
        self,
        items: List[EvidenceItem],
        queries: List[str],
        seed_entities: List[str],
    ) -> List[Dict[str, Any]]:
        # Scoring uses BM25 on a shared term set to keep relevance comparable,
        # then adds a small seed bonus to prefer items tied to known entities.
        if not items:
            return []

        terms = []
        for query in queries:
            terms.extend(_extract_terms(query))
        terms = list(dict.fromkeys(terms))
        if not terms:
            return []

        df = {term: 0 for term in terms}
        for item in items:
            for term in terms:
                if _count_term(item.text or "", term) > 0:
                    df[term] += 1

        avgdl = _average_doc_len(items)
        total_docs = max(len(items), 1)

        scored: List[Dict[str, Any]] = []
        for item in items:
            doc_len = item.meta.get("doc_len") or _estimate_doc_len(item.text)
            score = _bm25_score(item.text, terms, df, total_docs, avgdl, doc_len)
            # Exact phrase bonus keeps hard constraints and specific entities prioritized.
            for query in queries:
                if query and query in (item.text or ""):
                    score += 0.8
            # Seed bonus should only boost already-relevant items. It must not
            # resurrect items with zero textual relevance, otherwise seeds will
            # inject noise into retrieval (especially for world rules/entities).
            if score <= 0:
                continue
            score += _seed_bonus(item, seed_entities)
            score += _stars_bonus(item.meta.get("stars"))
            scored.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "text": item.text,
                    "score": round(score, 6),
                    "source": item.source,
                    "meta": item.meta,
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _facts_mtime(self, project_id: str) -> float:
        facts_path = self.canon_storage.get_project_path(project_id) / "canon" / "facts.jsonl"
        return facts_path.stat().st_mtime if facts_path.exists() else 0.0

    def _summaries_mtime(self, project_id: str) -> float:
        base = self.draft_storage.get_project_path(project_id) / "summaries"
        latest = 0.0
        if base.exists():
            for path in base.glob("*_summary.yaml"):
                latest = max(latest, path.stat().st_mtime)
        volumes_dir = self.draft_storage.get_project_path(project_id) / "volumes"
        if volumes_dir.exists():
            for path in volumes_dir.glob("*.yaml"):
                latest = max(latest, path.stat().st_mtime)
        return latest

    def _cards_mtime(self, project_id: str) -> float:
        project_path = self.card_storage.get_project_path(project_id)
        latest = 0.0
        for path in (project_path / "cards" / "characters").glob("*.yaml"):
            latest = max(latest, path.stat().st_mtime)
        for path in (project_path / "cards" / "world").glob("*.yaml"):
            latest = max(latest, path.stat().st_mtime)
        style_path = project_path / "cards" / "style.yaml"
        if style_path.exists():
            latest = max(latest, style_path.stat().st_mtime)
        return latest

    def _memory_mtime(self, project_id: str) -> float:
        memory_path = self.index_storage.get_index_path(project_id, self.MEMORY_INDEX)
        return memory_path.stat().st_mtime if memory_path.exists() else 0.0

    def _summary_items(self, chapter_id: str, summary: Any) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        brief_summary = str(getattr(summary, "brief_summary", "") or "").strip()
        if brief_summary:
            items.append(
                EvidenceItem(
                    id=f"summary:{chapter_id}:brief",
                    type="summary",
                    text=brief_summary,
                    source={"chapter": chapter_id, "field": "brief_summary"},
                    scope="chapter",
                    entities=[],
                    meta={"doc_len": _estimate_doc_len(brief_summary)},
                )
            )
        key_events = getattr(summary, "key_events", []) or []
        for idx, item in enumerate(key_events):
            text = str(item).strip()
            if not text:
                continue
            items.append(
                EvidenceItem(
                    id=f"summary:{chapter_id}:event:{idx + 1}",
                    type="summary",
                    text=text,
                    source={"chapter": chapter_id, "field": "key_events", "index": idx},
                    scope="chapter",
                    entities=[],
                    meta={"doc_len": _estimate_doc_len(text)},
                )
            )
        open_loops = getattr(summary, "open_loops", []) or []
        for idx, item in enumerate(open_loops):
            text = str(item).strip()
            if not text:
                continue
            items.append(
                EvidenceItem(
                    id=f"summary:{chapter_id}:loop:{idx + 1}",
                    type="summary",
                    text=text,
                    source={"chapter": chapter_id, "field": "open_loops", "index": idx},
                    scope="chapter",
                    entities=[],
                    meta={"doc_len": _estimate_doc_len(text)},
                )
            )
        return items

    async def _character_items(self, project_id: str, name: str) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        file_path = self.card_storage.get_project_path(project_id) / "cards" / "characters" / f"{name}.yaml"
        if not file_path.exists():
            return items
        data = await self.card_storage.read_yaml(file_path)
        stars = _normalize_stars(data.get("stars"))
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list):
            aliases = []
        aliases = [str(item).strip() for item in aliases if str(item).strip()]
        aliases = list(dict.fromkeys(aliases))
        entities = list(dict.fromkeys([name] + aliases))

        description = str(data.get("description") or "").strip()
        items.extend(_attach_stars(_build_card_lines(name, "character", "description", description, entities), stars))
        items.extend(
            _attach_stars(_build_card_lines(name, "character", "identity", data.get("identity"), entities), stars)
        )
        items.extend(
            _attach_stars(_build_card_lines(name, "character", "appearance", data.get("appearance"), entities), stars)
        )
        items.extend(
            _attach_stars(_build_card_lines(name, "character", "motivation", data.get("motivation"), entities), stars)
        )
        items.extend(_attach_stars(_build_card_list(name, "character", "aliases", aliases, entities), stars))
        items.extend(
            _attach_stars(_build_card_list(name, "character", "personality", data.get("personality"), entities), stars)
        )
        items.extend(
            _attach_stars(
                _build_card_lines(name, "character", "speech_pattern", data.get("speech_pattern"), entities), stars
            )
        )
        items.extend(_attach_stars(_build_card_relationships(name, data.get("relationships"), entities), stars))
        items.extend(
            _attach_stars(_build_card_list(name, "character", "boundaries", data.get("boundaries"), entities), stars)
        )
        items.extend(_attach_stars(_build_card_lines(name, "character", "arc", data.get("arc"), entities), stars))
        return items

    async def _world_items(self, project_id: str, name: str) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        file_path = self.card_storage.get_project_path(project_id) / "cards" / "world" / f"{name}.yaml"
        if not file_path.exists():
            return items
        data = await self.card_storage.read_yaml(file_path)
        stars = _normalize_stars(data.get("stars"))
        entities = [name] if _is_entity_name(name) else []

        description = data.get("description")
        category = data.get("category")

        items.extend(_attach_stars(_build_card_lines(name, "world", "description", description, entities), stars))
        items.extend(_attach_stars(_build_card_lines(name, "world", "category", category, entities), stars))

        rule_items = _attach_stars(_extract_world_rules(name, description, entities), stars)
        entity_items = _attach_stars(_extract_world_entities(name, description, category, entities), stars)
        items.extend(rule_items)
        items.extend(entity_items)
        return items


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", "", str(text).lower())
    return cleaned


def _normalize_stars(value: Any) -> int:
    try:
        stars = int(value)
    except Exception:
        return 1
    return max(1, min(stars, 3))


def _attach_stars(items: List[EvidenceItem], stars: Any) -> List[EvidenceItem]:
    normalized = _normalize_stars(stars)
    for item in items:
        item.meta = dict(item.meta or {})
        item.meta["stars"] = normalized
    return items


def _build_card_lines(
    name: str,
    card_type: str,
    field: str,
    value: Any,
    entities: List[str],
) -> List[EvidenceItem]:
    text = str(value or "").strip()
    if not text:
        return []
    lines = _split_text_blocks(text)
    items = []
    for idx, line in enumerate(lines):
        items.append(
            EvidenceItem(
                id=f"{card_type}:{name}:{field}:{idx + 1}",
                type=card_type,
                text=f"{field}: {line}" if field else line,
                source={"card": name, "field": field},
                scope="global",
                entities=entities,
                meta={"doc_len": _estimate_doc_len(line)},
            )
        )
    return items


def _build_card_list(
    name: str,
    card_type: str,
    field: str,
    value: Any,
    entities: List[str],
) -> List[EvidenceItem]:
    if not isinstance(value, list):
        return []
    items = []
    for idx, item in enumerate(value):
        text = str(item).strip()
        if not text:
            continue
        items.append(
            EvidenceItem(
                id=f"{card_type}:{name}:{field}:{idx + 1}",
                type=card_type,
                text=f"{field}: {text}" if field else text,
                source={"card": name, "field": field, "index": idx},
                scope="global",
                entities=entities,
                meta={"doc_len": _estimate_doc_len(text)},
            )
        )
    return items


def _build_card_relationships(
    name: str,
    value: Any,
    entities: List[str],
) -> List[EvidenceItem]:
    if not isinstance(value, list):
        return []
    items = []
    for idx, rel in enumerate(value):
        if not isinstance(rel, dict):
            continue
        target = str(rel.get("target") or "").strip()
        relation = str(rel.get("relation") or "").strip()
        if not (target or relation):
            continue
        text = f"relationships: {target} {relation}".strip()
        items.append(
            EvidenceItem(
                id=f"character:{name}:relationships:{idx + 1}",
                type="character",
                text=text,
                source={"card": name, "field": "relationships", "index": idx},
                scope="global",
                entities=list(dict.fromkeys(entities + [target] if target else entities)),
                meta={"doc_len": _estimate_doc_len(text)},
            )
        )
    return items


def _split_text_blocks(text: str, max_len: int = 140) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if not parts:
        return []
    blocks: List[str] = []
    for part in parts:
        if len(part) <= max_len:
            blocks.append(part)
            continue
        blocks.extend([p for p in re.split(r"[。；;]", part) if p.strip()])
    return [b.strip() for b in blocks if b.strip()]


def _extract_world_rules(
    name: str,
    description: Any,
    entities: List[str],
) -> List[EvidenceItem]:
    # World rule extraction:
    # - Extract pattern-matched rule sentences from description only.
    # - Deduplicate by normalized text.
    items: List[EvidenceItem] = []
    rule_texts = []

    desc_text = str(description or "").strip()
    if desc_text:
        for sentence in _split_text_blocks(desc_text, max_len=200):
            if _is_rule_sentence(sentence):
                rule_texts.append(sentence)

    normalized = set()
    for text in rule_texts:
        norm = _normalize_text(text)
        if not norm or norm in normalized:
            continue
        normalized.add(norm)
        items.append(
            EvidenceItem(
                id=f"world_rule:{name}:{len(items) + 1}",
                type="world_rule",
                text=text,
                source={"card": name, "field": "description"},
                scope="global",
                entities=entities,
                meta={"doc_len": _estimate_doc_len(text)},
            )
        )
    return items


def _extract_world_entities(
    name: str,
    description: Any,
    category: Any,
    entities: List[str],
) -> List[EvidenceItem]:
    # World entity extraction:
    # - Use card name as the primary entity.
    # - Keep the first sentence of description as definition if present.
    items: List[EvidenceItem] = []
    name = str(name or "").strip()
    if name and _is_entity_name(name):
        definition = _first_sentence(str(description or "").strip())
        text = name if not definition else f"{name}: {definition}"
        items.append(
            EvidenceItem(
                id=f"world_entity:{name}:1",
                type="world_entity",
                text=text,
                source={"card": name, "field": "name"},
                scope="global",
                entities=list(dict.fromkeys(entities + [name])),
                meta={"doc_len": _estimate_doc_len(text)},
            )
        )

    category_text = str(category or "").strip()
    if category_text and _is_entity_name(category_text):
        items.append(
            EvidenceItem(
                id=f"world_entity:{name}:category",
                type="world_entity",
                text=f"类别: {category_text}",
                source={"card": name, "field": "category"},
                scope="global",
                entities=list(dict.fromkeys(entities + [category_text])),
                meta={"doc_len": _estimate_doc_len(category_text)},
            )
        )
    return items


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r"[。！？!?]", text)
    return parts[0].strip() if parts else text.strip()


def _is_rule_sentence(sentence: str) -> bool:
    if not sentence:
        return False
    patterns = [
        "必须",
        "禁止",
        "不得",
        "只能",
        "会导致",
        "代价",
        "限制",
        "不可变",
        "一旦",
        "否则",
        "需",
        "不得不",
    ]
    return any(pattern in sentence for pattern in patterns)


def _is_entity_name(name: str) -> bool:
    if not name:
        return False
    if len(name) < 2:
        return False
    if name.isdigit():
        return False
    return name not in _GENERIC_TERMS


_GENERIC_TERMS = {
    "城",
    "镇",
    "村",
    "山",
    "河",
    "湖",
    "海",
    "国",
    "帝国",
    "王国",
    "共和国",
    "联盟",
    "组织",
    "宗",
    "派",
    "门派",
    "宗门",
    "学院",
    "公司",
    "家族",
    "氏族",
    "门",
    "会",
    "世界",
}


def _seed_bonus(item: EvidenceItem, seeds: List[str]) -> float:
    if not seeds:
        return 0.0
    entities = set([e for e in item.entities or [] if e])
    text = item.text or ""
    for seed in seeds:
        if seed in entities:
            return 1.0
        if seed and seed in text:
            return 0.5
    return 0.0


def _stars_bonus(stars: Any) -> float:
    if stars is None:
        return 0.0
    normalized = _normalize_stars(stars)
    if normalized <= 1:
        return 0.0
    return (normalized - 1) * 0.35


def _merge_quotas(
    types: List[str],
    custom: Optional[Dict[str, Dict[str, int]]],
) -> Dict[str, Dict[str, int]]:
    defaults = {
        "fact": {"min": 3, "max": 8},
        "summary": {"min": 1, "max": 6},
        "text_chunk": {"min": 3, "max": 8},
        "character": {"min": 0, "max": 6},
        "world_rule": {"min": 2, "max": 6},
        "world_entity": {"min": 1, "max": 6},
        "world": {"min": 0, "max": 2},
        "style": {"min": 0, "max": 1},
        "memory": {"min": 0, "max": 4},
    }
    merged = {key: dict(value) for key, value in defaults.items() if key in types}
    for key, value in (custom or {}).items():
        if key not in merged:
            merged[key] = {}
        merged[key]["min"] = int(value.get("min", merged[key].get("min", 0)))
        if "max" in value:
            merged[key]["max"] = int(value["max"])
    return merged


def _apply_type_quotas(
    scored: List[Dict[str, Any]],
    quotas: Dict[str, Dict[str, int]],
    limit: int,
) -> List[Dict[str, Any]]:
    # Quota selection works in two phases:
    # 1) Guarantee per-type minimums using the top-ranked items of each type.
    # 2) Fill remaining slots globally by score, while respecting per-type max caps.
    limit = max(limit, 0)
    if limit == 0 or not scored:
        return []

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for item in scored:
        by_type.setdefault(item["type"], []).append(item)
    for items in by_type.values():
        items.sort(key=lambda x: x["score"], reverse=True)

    selected: List[Dict[str, Any]] = []
    counts = {t: 0 for t in quotas.keys()}
    used_ids = set()

    for t, quota in quotas.items():
        min_count = max(quota.get("min", 0), 0)
        candidates = by_type.get(t, [])
        for item in candidates[:min_count]:
            if len(selected) >= limit:
                break
            if item["id"] in used_ids:
                continue
            selected.append(item)
            used_ids.add(item["id"])
            counts[t] = counts.get(t, 0) + 1

    remaining = []
    for items in by_type.values():
        remaining.extend(items)
    remaining.sort(key=lambda x: x["score"], reverse=True)

    for item in remaining:
        if len(selected) >= limit:
            break
        if item["id"] in used_ids:
            continue
        t = item["type"]
        max_count = quotas.get(t, {}).get("max")
        if max_count is not None and counts.get(t, 0) >= max_count:
            continue
        selected.append(item)
        used_ids.add(item["id"])
        counts[t] = counts.get(t, 0) + 1

    return selected


def _wrap_text_chunks(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wrapped = []
    for item in items:
        wrapped.append(
            {
                "id": item.get("id"),
                "type": "text_chunk",
                "text": item.get("text"),
                "score": float(item.get("score") or 0),
                "source": item.get("source") or {},
                "meta": {},
            }
        )
    return wrapped


def _count_types(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return counts


def _extract_top_sources(items: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    if not items or limit <= 0:
        return []
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in items:
        source = item.get("source") or {}
        entry = {
            "type": item.get("type"),
            "chapter": source.get("chapter"),
            "path": source.get("path"),
            "field": source.get("field"),
        }
        key = (entry.get("type"), entry.get("chapter"), entry.get("path"), entry.get("field"))
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
        if len(result) >= limit:
            break
    return result


evidence_service = EvidenceIndexService()
