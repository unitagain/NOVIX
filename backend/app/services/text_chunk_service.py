# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  文本分块索引服务 - 对章节草稿进行分块处理，构建 BM25 索引，支持语义重排。
  Text chunk indexing and search - Splits chapter drafts into chunks, builds BM25 indices, supports semantic reranking via LLM.
"""

from __future__ import annotations

import re
import time
import math
import json
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.evidence import EvidenceItem, EvidenceIndexMeta
from app.storage.drafts import DraftStorage
from app.storage.evidence_index import EvidenceIndexStorage
from app.utils.text import normalize_newlines
from app.llm_gateway import get_gateway
from app.prompts import text_chunk_rerank_prompt
from app.services.llm_config_service import llm_config_service


class TextChunkIndexService:
    """
    文本分块索引服务 - 对章节文本构建并维护 BM25 索引。

    Indexes and searches text chunks from chapter drafts.
    Supports sliding-window chunking with configurable overlap, BM25 scoring, and optional LLM-based semantic reranking.

    Attributes:
        INDEX_NAME: 索引标识 / Index name constant
        max_paragraph_chars: 段落最大字符数 / Max characters per paragraph before windowing
        window_size: 滑动窗口大小 / Sliding window size (characters)
        window_overlap: 窗口重叠大小 / Window overlap (characters)
        min_chunk_chars: 分块最小字符数 / Minimum chunk size to index
    """

    INDEX_NAME = "text_chunks"

    def __init__(
        self,
        data_dir: Optional[str] = None,
        max_paragraph_chars: int = 800,
        window_size: int = 520,
        window_overlap: int = 160,
        min_chunk_chars: int = 40,
    ) -> None:
        self.draft_storage = DraftStorage(data_dir)
        self.index_storage = EvidenceIndexStorage(data_dir)
        self.max_paragraph_chars = max_paragraph_chars
        self.window_size = window_size
        self.window_overlap = window_overlap
        self.min_chunk_chars = min_chunk_chars

    async def build_index(self, project_id: str, force: bool = False) -> EvidenceIndexMeta:
        """Build or reuse the text chunk index.

        Args:
            project_id: Target project id.
            force: Force rebuild even if up-to-date.

        Returns:
            Index metadata.
        """
        if not force and not await self._should_rebuild(project_id):
            existing = await self.index_storage.read_meta(project_id, self.INDEX_NAME)
            if existing:
                return existing

        items, latest_mtime = await self._collect_items(project_id)
        meta = EvidenceIndexMeta(
            index_name=self.INDEX_NAME,
            built_at=time.time(),
            item_count=len(items),
            source_mtime=latest_mtime or None,
            details={"chapters": len({item.source.get("chapter") for item in items})},
        )
        await self.index_storage.write_items(project_id, self.INDEX_NAME, items)
        await self.index_storage.write_meta(project_id, self.INDEX_NAME, meta)
        return meta

    async def search(
        self,
        project_id: str,
        query: str,
        limit: int = 8,
        queries: Optional[List[str]] = None,
        chapters: Optional[List[str]] = None,
        exclude_chapters: Optional[List[str]] = None,
        rebuild: bool = False,
        semantic_rerank: bool = False,
        rerank_query: Optional[str] = None,
        rerank_top_k: int = 16,
    ) -> List[Dict[str, Any]]:
        """Search text chunks.

        Args:
            project_id: Target project id.
            query: Query string.
            limit: Max results.
            chapters: Optional chapter whitelist.
            exclude_chapters: Optional chapter blacklist.
            rebuild: Force index rebuild.

        Returns:
            List of ranked text chunk hits.
        """
        query = (query or "").strip()
        query_list = [str(q or "").strip() for q in (queries or []) if str(q or "").strip()]
        if not query_list and not query:
            return []

        if rebuild:
            await self.build_index(project_id, force=True)
        else:
            await self.build_index(project_id, force=False)

        items = await self.index_storage.read_items(project_id, self.INDEX_NAME)
        if not items:
            return []

        if chapters:
            chapters_set = set(chapters)
            items = [item for item in items if item.source.get("chapter") in chapters_set]
        if exclude_chapters:
            exclude_set = set(exclude_chapters)
            items = [item for item in items if item.source.get("chapter") not in exclude_set]

        if not query_list:
            query_list = [query]

        scored = self._bm25_search_multi(items, query_list, limit)
        if semantic_rerank and rerank_query:
            reranked = await self._rerank_with_llm(rerank_query, scored, rerank_top_k)
            if reranked is not None:
                scored = reranked

        scored.sort(key=lambda x: x["score"], reverse=True)
        if limit <= 0:
            return []
        return scored[:limit]

    def _bm25_search_multi(self, items: List[EvidenceItem], queries: List[str], limit: int) -> List[Dict[str, Any]]:
        combined: Dict[str, Dict[str, Any]] = {}
        per_query_limit = max(4, min(12, limit))
        for query in queries[:4]:
            hits = self._bm25_search(items, query, per_query_limit)
            for hit in hits:
                existing = combined.get(hit["id"])
                if not existing or hit["score"] > existing.get("score", 0):
                    combined[hit["id"]] = hit
        return list(combined.values())

    def _bm25_search(self, items: List[EvidenceItem], query: str, limit: int) -> List[Dict[str, Any]]:
        terms = _extract_terms(query)
        if not terms:
            return []

        df = {term: 0 for term in terms}
        for item in items:
            text = item.text or ""
            for term in terms:
                if _count_term(text, term) > 0:
                    df[term] += 1

        total_docs = max(len(items), 1)
        avgdl = _average_doc_len(items)

        scored: List[Dict[str, Any]] = []
        for item in items:
            doc_len = item.meta.get("doc_len") or _estimate_doc_len(item.text)
            score = _bm25_score(item.text, terms, df, total_docs, avgdl, doc_len)
            if score <= 0:
                continue
            scored.append(
                {
                    "id": item.id,
                    "text": item.text,
                    "score": round(score, 6),
                    "source": item.source,
                    "type": item.type,
                }
            )

        scored.sort(key=lambda x: x["score"], reverse=True)
        if limit <= 0:
            return []
        return scored[:limit]

    async def _rerank_with_llm(
        self,
        query: str,
        hits: List[Dict[str, Any]],
        top_k: int,
    ) -> Optional[List[Dict[str, Any]]]:
        if not hits:
            return None
        provider_id = llm_config_service.get_assignments().get("writer") or ""
        if not provider_id:
            return None

        candidates = hits[: max(3, min(top_k, len(hits)))]
        payload = []
        for item in candidates:
            text = str(item.get("text") or "").strip()
            payload.append(
                {
                    "id": item.get("id"),
                    "text": text[:220],
                }
            )
        if not payload:
            return None

        prompt = text_chunk_rerank_prompt(query=query, payload=payload)

        try:
            gateway = get_gateway()
            response = await gateway.chat(
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                provider=provider_id,
                temperature=0,
                max_tokens=600,
                retry=True,
            )
            content = str(response.get("content") or "").strip()
            scores = self._parse_rerank_scores(content)
            if not scores:
                return None
            reranked: List[Dict[str, Any]] = []
            for item in hits:
                base_score = float(item.get("score") or 0)
                rerank_score = float(scores.get(item.get("id"), 0))
                merged_score = base_score + (rerank_score * 3.0)
                updated = dict(item)
                updated["score"] = round(merged_score, 6)
                updated_meta = dict(updated.get("meta") or {})
                updated_meta["bm25_score"] = round(base_score, 6)
                updated_meta["rerank_score"] = round(rerank_score, 3)
                updated["meta"] = updated_meta
                reranked.append(updated)
            return reranked
        except Exception:
            return None

    def _parse_rerank_scores(self, text: str) -> Dict[str, float]:
        if not text:
            return {}
        data = None
        for start_char in ("[", "{"):
            start = text.find(start_char)
            if start >= 0:
                try:
                    data = json.loads(text[start:])
                    break
                except json.JSONDecodeError:
                    continue
        if data is None:
            return {}
        scores: Dict[str, float] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                try:
                    score = float(item.get("score"))
                except (TypeError, ValueError):
                    score = 0.0
                scores[item_id] = score
        elif isinstance(data, dict):
            for item_id, score in data.items():
                try:
                    scores[str(item_id)] = float(score)
                except (TypeError, ValueError):
                    continue
        return scores

    async def _should_rebuild(self, project_id: str) -> bool:
        index_path = self.index_storage.get_index_path(project_id, self.INDEX_NAME)
        if not index_path.exists():
            return True
        meta = await self.index_storage.read_meta(project_id, self.INDEX_NAME)
        if not meta:
            return True
        latest_mtime = self._latest_draft_mtime(project_id)
        return latest_mtime > (meta.source_mtime or 0)

    async def _collect_items(self, project_id: str) -> Tuple[List[EvidenceItem], float]:
        chapters = await self.draft_storage.list_chapters(project_id)
        items: List[EvidenceItem] = []
        latest_mtime = 0.0
        for chapter in chapters:
            draft_path = self.draft_storage.get_latest_draft_file(project_id, chapter)
            if not draft_path or not draft_path.exists():
                continue
            latest_mtime = max(latest_mtime, draft_path.stat().st_mtime)
            try:
                text = await self.draft_storage.read_text(draft_path)
            except (OSError, UnicodeError):
                continue
            chunks = self.split_text_to_chunks(text)
            rel_path = draft_path.relative_to(self.draft_storage.get_project_path(project_id)).as_posix()
            draft_label = "final" if draft_path.name == "final.md" else draft_path.stem.replace("draft_", "")
            for chunk in chunks:
                if len(chunk["text"]) < self.min_chunk_chars:
                    continue
                item_id = f"text:{chapter}#p{chunk['paragraph']}-w{chunk['window']}"
                doc_len = _estimate_doc_len(chunk["text"])
                items.append(
                    EvidenceItem(
                        id=item_id,
                        type="text_chunk",
                        text=chunk["text"],
                        source={
                            "chapter": chapter,
                            "draft": draft_label,
                            "path": rel_path,
                            "paragraph": chunk["paragraph"],
                            "window": chunk["window"],
                            "start": chunk["start"],
                            "end": chunk["end"],
                        },
                        scope="chapter",
                        entities=[],
                        meta={"doc_len": doc_len},
                    )
                )
        return items, latest_mtime

    def split_text_to_chunks(self, text: str) -> List[Dict[str, Any]]:
        """Split text into chunks using the current chunking strategy."""
        return self._split_text(text)

    def _split_text(self, text: str) -> List[Dict[str, Any]]:
        normalized = normalize_newlines(text)
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", normalized) if p.strip()]
        chunks: List[Dict[str, Any]] = []
        for idx, paragraph in enumerate(paragraphs):
            if len(paragraph) <= self.max_paragraph_chars:
                chunks.append(
                    {
                        "text": paragraph,
                        "paragraph": idx,
                        "window": 0,
                        "start": 0,
                        "end": len(paragraph),
                    }
                )
                continue
            for window_idx, (frag, start, end) in enumerate(self._window_split(paragraph)):
                chunks.append(
                    {
                        "text": frag,
                        "paragraph": idx,
                        "window": window_idx,
                        "start": start,
                        "end": end,
                    }
                )
        return chunks

    def _window_split(self, paragraph: str) -> List[Tuple[str, int, int]]:
        size = max(self.window_size, 1)
        overlap = min(max(self.window_overlap, 0), size - 1)
        step = max(size - overlap, 1)
        windows = []
        start = 0
        while start < len(paragraph):
            end = min(start + size, len(paragraph))
            frag = paragraph[start:end].strip()
            if frag:
                windows.append((frag, start, end))
            if end >= len(paragraph):
                break
            start += step
        return windows

    def _latest_draft_mtime(self, project_id: str) -> float:
        latest = 0.0
        project_path = self.draft_storage.get_project_path(project_id)
        drafts_dir = project_path / "drafts"
        if not drafts_dir.exists():
            return latest
        for chapter_dir in drafts_dir.iterdir():
            if not chapter_dir.is_dir():
                continue
            for file_path in chapter_dir.glob("draft_*.md"):
                latest = max(latest, file_path.stat().st_mtime)
            final_path = chapter_dir / "final.md"
            if final_path.exists():
                latest = max(latest, final_path.stat().st_mtime)
        return latest


def _extract_terms(text: str) -> List[str]:
    # Tokenization strategy:
    # - Keep ASCII words as-is.
    # - For CJK, use 2-gram/3-gram to avoid dependency on external segmenters.
    # This keeps recall reasonable for short entity-like queries.
    text = (text or "").lower()
    terms: List[str] = []

    ascii_terms = re.findall(r"[a-z0-9]+", text)
    if ascii_terms:
        terms.extend(ascii_terms)

    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) == 1:
            terms.append(block)
            continue
        for n in (2, 3):
            if len(block) < n:
                continue
            for i in range(0, len(block) - n + 1):
                terms.append(block[i : i + n])

    return list(dict.fromkeys(terms))


def _count_term(text: str, term: str) -> int:
    if not term:
        return 0
    if re.fullmatch(r"[a-z0-9]+", term):
        return len(re.findall(rf"\\b{re.escape(term)}\\b", text, flags=re.IGNORECASE))
    return _count_overlapping(text, term)


def _count_overlapping(text: str, term: str) -> int:
    count = 0
    start = 0
    while True:
        idx = text.find(term, start)
        if idx == -1:
            break
        count += 1
        start = idx + 1
    return count


def _estimate_doc_len(text: str) -> int:
    return max(len(_extract_terms(text)), 1)


def _average_doc_len(items: List[EvidenceItem]) -> float:
    if not items:
        return 1.0
    total = 0
    for item in items:
        total += item.meta.get("doc_len") or _estimate_doc_len(item.text)
    return total / max(len(items), 1)


def _bm25_score(
    text: str,
    terms: List[str],
    df: Dict[str, int],
    total_docs: int,
    avgdl: float,
    doc_len: int,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    # BM25 core:
    # - tf saturation controlled by k1
    # - length normalization controlled by b
    # - idf computed per term across the corpus
    score = 0.0
    for term in terms:
        freq = _count_term(text, term)
        if freq <= 0:
            continue
        term_df = df.get(term, 0)
        idf = _bm25_idf(total_docs, term_df)
        denom = freq + k1 * (1 - b + b * (doc_len / max(avgdl, 1.0)))
        score += idf * (freq * (k1 + 1) / max(denom, 1e-6))
    return score


def _bm25_idf(total_docs: int, doc_freq: int) -> float:
    return max(0.0, math.log((total_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0))


text_chunk_service = TextChunkIndexService()
