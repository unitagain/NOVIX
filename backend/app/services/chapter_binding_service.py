# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  章节实体绑定服务 - 为每个章节构建与卡片之间的实体关联，支持角色/世界观的命名和别名匹配。
  Chapter entity binding service - builds and maintains per-chapter associations between draft text and entity cards (characters, world entities, rules).
"""

from __future__ import annotations

import time
import re
from typing import Any, Dict, List, Optional, Tuple

from app.storage.cards import CardStorage
from app.error_contract import safe_error_code
from app.storage.drafts import DraftStorage
from app.storage.bindings import ChapterBindingStorage
from app.services.evidence_service import evidence_service
from app.services.text_chunk_service import (
    text_chunk_service,
    _extract_terms,
    _bm25_score,
    _estimate_doc_len,
    _count_term,
)
from app.utils.chapter_id import ChapterIDValidator, normalize_chapter_id


class ChapterBindingService:
    """
    章节实体绑定管理器 - 为章节构建并维护实体关联。

    Manages per-chapter entity bindings (characters, world entities, world rules).
    Uses BM25 scoring and heuristic matching to identify entity mentions in chapter text.

    Attributes:
        NAME_STOPWORDS: 通用代词/指代词 (Personal pronouns, generic references)
        GENERIC_TERMS: 通用词汇 (Generic terms like city, kingdom that should be de-weighted)
        BM25_THRESHOLD: BM25 匹配阈值 (Threshold for BM25-based matches)
    """

    NAME_STOPWORDS = {
        "我",
        "你",
        "他",
        "她",
        "它",
        "我们",
        "你们",
        "他们",
        "她们",
        "它们",
        "这里",
        "那里",
        "此处",
        "那边",
        "自己",
        "大家",
        "众人",
        "某人",
        "某某",
    }

    GENERIC_TERMS = {
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

    BM25_THRESHOLD = 0.9
    BM25_THRESHOLD_GENERIC = 1.4
    RULE_THRESHOLD = 1.0

    def __init__(
        self,
        data_dir: Optional[str] = None,
        max_examples: int = 2,
        snippet_radius: int = 12,
        min_name_length: int = 2,
    ) -> None:
        self.card_storage = CardStorage(data_dir)
        self.draft_storage = DraftStorage(data_dir)
        self.binding_storage = ChapterBindingStorage(data_dir)
        self.max_examples = max_examples
        self.snippet_radius = snippet_radius
        self.min_name_length = min_name_length

    async def build_bindings(self, project_id: str, chapter: str, force: bool = False) -> Dict[str, Any]:
        """
        为章节构建实体绑定并持久化存储。

        Build entity bindings for a chapter and persist to storage.
        Identifies which characters, world entities, and rules are mentioned in the chapter text.

        Args:
            project_id: 目标项目 ID / Target project id.
            chapter: 章节 ID / Chapter id.
            force: 即使存在也强制重建 / Force rebuild even if existing.

        Returns:
            绑定数据结构 / Binding payload with characters, world_entities, world_rules, and sources.

        Raises:
            Exception: 数据存储操作异常 / If storage operations fail (caught and returns empty payload).
        """
        canonical = normalize_chapter_id(chapter) or str(chapter).strip()
        if not force:
            existing = await self.binding_storage.read_bindings(project_id, canonical)
            if existing:
                return existing

        draft_path = self.draft_storage.get_latest_draft_file(project_id, canonical)
        if not draft_path or not draft_path.exists():
            empty = self._empty_payload(canonical)
            await self.binding_storage.write_bindings(project_id, canonical, empty)
            return empty

        try:
            text = await self.draft_storage.read_text(draft_path)
        except Exception:
            empty = self._empty_payload(canonical)
            await self.binding_storage.write_bindings(project_id, canonical, empty)
            return empty

        chunks = self._build_chunks(text)
        recent_entities = await self.get_seed_entities(project_id, canonical, window=2)
        character_candidates = await self._build_character_candidates(project_id)
        world_entity_candidates = await self._build_world_entity_candidates(project_id)
        world_rule_candidates = await self._build_world_rule_candidates(project_id)

        char_hits, char_sources = self._match_entities(
            text=text,
            chunks=chunks,
            candidates=character_candidates,
            recent_entities=recent_entities,
            kind="character",
        )
        world_hits, world_sources = self._match_entities(
            text=text,
            chunks=chunks,
            candidates=world_entity_candidates,
            recent_entities=recent_entities,
            kind="world_entity",
        )
        rule_hits, rule_sources = self._match_rules(text=text, chunks=chunks, rules=world_rule_candidates)

        payload = {
            "chapter": canonical,
            "characters": char_hits,
            "world_entities": world_hits,
            "world_rules": rule_hits,
            "sources": char_sources + world_sources + rule_sources,
            "draft_path": draft_path.relative_to(self.draft_storage.get_project_path(project_id)).as_posix(),
            "built_at": time.time(),
        }
        await self.binding_storage.write_bindings(project_id, canonical, payload)
        return payload

    async def build_bindings_batch(
        self,
        project_id: str,
        chapters: Optional[List[str]] = None,
        force: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build bindings for multiple chapters.

        Args:
            project_id: Target project id.
            chapters: Optional chapter list. If empty, rebuild all chapters.
            force: Force rebuild even if existing.

        Returns:
            List of per-chapter rebuild results.
        """
        chapter_list = await self._resolve_chapters(project_id, chapters)
        results: List[Dict[str, Any]] = []
        for chapter in chapter_list:
            try:
                binding = await self.build_bindings(project_id, chapter, force=force)
                results.append({"chapter": chapter, "success": True, "binding": binding})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": safe_error_code(exc)})
        return results

    async def read_bindings(self, project_id: str, chapter: str) -> Optional[Dict[str, Any]]:
        """Read bindings for a chapter.

        Args:
            project_id: Target project id.
            chapter: Chapter id.

        Returns:
            Binding payload if present.
        """
        canonical = normalize_chapter_id(chapter) or str(chapter).strip()
        return await self.binding_storage.read_bindings(project_id, canonical)

    async def write_bindings(self, project_id: str, chapter: str, data: Dict[str, Any]) -> None:
        """Persist bindings payload for a chapter."""
        canonical = normalize_chapter_id(chapter) or str(chapter).strip()
        await self.binding_storage.write_bindings(project_id, canonical, data)

    async def get_seed_entities(
        self,
        project_id: str,
        chapter: str,
        window: int = 2,
        ensure_built: bool = False,
        include_world_rules: bool = False,
    ) -> List[str]:
        """Return seed entities from recent chapter bindings.

        Args:
            project_id: Target project id.
            chapter: Current chapter id.
            window: Number of previous chapters to inspect.
            ensure_built: Build missing bindings for recent chapters.
            include_world_rules: Include world rule names as seeds.

        Returns:
            Unique list of entity names.
        """
        recent = await self.get_recent_chapters(project_id, chapter, window=window)

        seeds: List[str] = []
        for ch in recent:
            bindings = await self.binding_storage.read_bindings(project_id, ch)
            if not bindings and ensure_built:
                try:
                    bindings = await self.build_bindings(project_id, ch, force=False)
                except Exception:
                    bindings = None
            if not bindings:
                continue
            seeds.extend(bindings.get("characters") or [])
            seeds.extend(bindings.get("world_entities") or [])
            if include_world_rules:
                for rule_id in bindings.get("world_rules") or []:
                    rule_id_text = str(rule_id or "").strip()
                    if not rule_id_text.startswith("world_rule:"):
                        continue
                    tail = rule_id_text[len("world_rule:") :]
                    parts = tail.rsplit(":", 1)
                    if not parts:
                        continue
                    name = (parts[0] or "").strip()
                    if name:
                        seeds.append(name)

        return list(dict.fromkeys([s for s in seeds if s]))

    async def get_chapters_for_entities(
        self,
        project_id: str,
        entity_names: List[str],
        limit: int = 6,
    ) -> List[str]:
        """Return chapter ids that contain any of the given entities."""
        names = [str(name or "").strip() for name in (entity_names or []) if str(name or "").strip()]
        if not names:
            return []
        name_set = set(names)
        chapters = await self._resolve_chapters(project_id)
        matched: List[str] = []
        for chapter in chapters:
            bindings = await self.binding_storage.read_bindings(project_id, chapter)
            if not bindings:
                continue
            characters = set(bindings.get("characters") or [])
            worlds = set(bindings.get("world_entities") or [])
            if name_set.intersection(characters) or name_set.intersection(worlds):
                matched.append(chapter)
        if not matched:
            return []
        return matched[-limit:]

    async def extract_entities_from_text(self, project_id: str, text: str) -> Dict[str, List[str]]:
        """Extract character/world entity names mentioned in a short text."""
        cleaned = str(text or "").strip()
        if not cleaned:
            return {"characters": [], "world_entities": []}
        chunks = self._build_chunks(cleaned)
        character_candidates = await self._build_character_candidates(project_id)
        world_candidates = await self._build_world_entity_candidates(project_id)
        character_hits, _ = self._match_entities(
            text=cleaned,
            chunks=chunks,
            candidates=character_candidates,
            recent_entities=[],
            kind="character",
        )
        world_hits, _ = self._match_entities(
            text=cleaned,
            chunks=chunks,
            candidates=world_candidates,
            recent_entities=[],
            kind="world_entity",
        )
        return {
            "characters": character_hits,
            "world_entities": world_hits,
        }

    def extract_loose_mentions(self, text: str, limit: int = 6) -> List[str]:
        """Extract likely entity-like mentions from text (even if no cards exist).

        用途：用于 UI 展示与检索补充，避免“查询设定展示的人名与指令不符”的困惑。
        注意：该方法不保证命中真实卡片，仅提供“疑似实体提及”。
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return []

        limit = max(int(limit or 0), 0)
        if limit <= 0:
            return []

        prefix_terms = (
            "邻居",
            "同学",
            "校长",
            "老师",
            "学姐",
            "学长",
            "同事",
            "朋友",
            "闺蜜",
            "干妈",
            "干爹",
            "母亲",
            "父亲",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "妻子",
            "丈夫",
        )
        suffix_terms = (
            "常常",
            "经常",
            "总是",
            "一直",
            "争抢",
            "抢走",
            "保护",
            "免受",
            "喜欢",
            "讨厌",
            "羡慕",
            "同情",
            "惆怅",
            "儿子",
            "女儿",
            "孩子",
            "宝宝",
        )
        # 常见非名字词：避免把“外貌/身材/第一次出场”等误识别为实体。
        non_name_terms = {
            "第一次",
            "出场",
            "外貌",
            "身材",
            "可爱",
            "动人",
            "美人",
            "太子",
            "重点",
            "色气",
            "描写",
            "刻画",
            "新增",
            "引出",
            "众人",
            "尤其",
            "完美",
            "面庞",
            "吸引",
            "折服",
            "母女",
            "父子",
            "章节",
            "指令",
            "设定",
            "事实",
            "摘要",
            "正文",
            "角色",
            "世界观",
            "文风",
            "编辑",
            "主笔",
            "修改",
            "扩写",
            "常常",
            "经常",
            "总是",
            "一直",
        }

        def normalize_token(token: str) -> str:
            token = str(token or "").strip()
            if not token:
                return ""

            # Remove common role prefixes.
            for prefix in prefix_terms:
                if token.startswith(prefix) and len(token) > len(prefix) + 1:
                    token = token[len(prefix) :].strip()
                    break

            # Handle partial prefix capture like "居X..." (from "邻居X...").
            if token.startswith("居") and len(token) >= 4:
                token = token[1:].strip()

            # Remove trailing behaviour/role suffixes.
            changed = True
            while changed and token:
                changed = False
                for suffix in suffix_terms:
                    if token.endswith(suffix) and len(token) > len(suffix) + 1:
                        token = token[: -len(suffix)].strip()
                        changed = True
                        break

            return token

        def is_candidate(token: str) -> bool:
            token = normalize_token(token)
            if not token:
                return False
            if token in non_name_terms:
                return False
            if len(token) < 2 or len(token) > 6:
                return False
            # 明显的功能词/语气词
            if any(ch in token for ch in ["的", "了", "在", "与", "和", "而", "并", "及", "或"]):
                return False
            return True

        mentions: List[str] = []

        # 1) 优先提取中文引号/英文引号包裹的实体
        for token in re.findall(r"[“\"']([\u4e00-\u9fff]{2,12})[”\"']", cleaned):
            token = normalize_token(token)
            if is_candidate(token) and token not in mentions:
                mentions.append(token)
                if len(mentions) >= limit:
                    return mentions[:limit]

        # 2) 提取“X和Y / X与Y / X、Y / X及Y”形式的并列实体
        for left, right in re.findall(r"([\u4e00-\u9fff]{2,12})(?:和|与|、|及)([\u4e00-\u9fff]{2,12})", cleaned):
            for token in (left, right):
                token = normalize_token(token)
                if is_candidate(token) and token not in mentions:
                    mentions.append(token)
                    if len(mentions) >= limit:
                        return mentions[:limit]

        return mentions[:limit]

    async def get_recent_chapters(
        self,
        project_id: str,
        chapter: str,
        window: int = 2,
        include_current: bool = False,
    ) -> List[str]:
        """Return the previous N chapter ids relative to a target chapter id.

        This helper is intentionally tolerant:
        - If the target chapter doesn't exist yet (new chapter), we still return
          the latest existing chapters before it (or simply the tail of the list).

        Args:
            project_id: Target project id.
            chapter: Current/target chapter id.
            window: Number of previous chapters to return.
            include_current: Whether to include the target chapter itself when present.

        Returns:
            List of recent chapter ids (sorted, older -> newer).
        """
        window = max(int(window or 0), 0)
        if window <= 0:
            return []

        canonical = normalize_chapter_id(chapter) or str(chapter).strip()
        chapters = await self.draft_storage.list_chapters(project_id)
        if not chapters:
            return []

        if canonical in chapters:
            index = chapters.index(canonical)
            end = index + 1 if include_current else index
            start = max(0, end - window)
            return chapters[start:end]

        # New chapter not yet created: best-effort pick chapters that precede it.
        target_weight = ChapterIDValidator.calculate_weight(canonical)
        if target_weight <= 0:
            return chapters[max(0, len(chapters) - window) :]

        prev = [ch for ch in chapters if ChapterIDValidator.calculate_weight(ch) < target_weight]
        if not prev:
            return chapters[: min(window, len(chapters))]
        return prev[max(0, len(prev) - window) :]

    def _build_chunks(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        return text_chunk_service.split_text_to_chunks(text)

    async def _build_character_candidates(self, project_id: str) -> List[Dict[str, Any]]:
        names = await self.card_storage.list_character_cards(project_id)
        alias_cache: Dict[str, List[str]] = {}
        candidates: List[Dict[str, Any]] = []
        for name in names:
            name = str(name or "").strip()
            if not self._is_valid_name(name, min_length=1):
                continue
            aliases = []
            aliases.extend(self._extract_aliases_from_name(name))
            aliases.extend(await self._load_character_aliases(project_id, name, alias_cache))
            candidates.append(
                {
                    "name": name,
                    "aliases": aliases,
                    "type": "character",
                }
            )
        return candidates

    async def _load_character_aliases(
        self,
        project_id: str,
        name: str,
        cache: Dict[str, List[str]],
    ) -> List[str]:
        if name in cache:
            return cache[name]
        aliases = []
        try:
            file_path = self.card_storage.get_project_path(project_id) / "cards" / "characters" / f"{name}.yaml"
            if file_path.exists():
                data = await self.card_storage.read_yaml(file_path)
                raw = data.get("aliases") or []
                if isinstance(raw, str):
                    raw = [raw]
                if isinstance(raw, list):
                    aliases = [str(item).strip() for item in raw if str(item).strip()]
        except Exception:
            aliases = []
        aliases = list(dict.fromkeys(aliases))
        cache[name] = aliases
        return aliases

    async def _build_world_entity_candidates(self, project_id: str) -> List[Dict[str, Any]]:
        await evidence_service.build_cards_index(project_id, force=False)
        items = await evidence_service.index_storage.read_items(project_id, evidence_service.CARDS_INDEX)
        alias_cache: Dict[str, List[str]] = {}
        candidates: List[Dict[str, Any]] = []
        seen = set()

        for item in items:
            if item.type != "world_entity":
                continue
            name = self._extract_world_entity_name(item)
            if not self._is_valid_name(name):
                continue
            if name in seen:
                continue
            seen.add(name)
            card_name = (item.source or {}).get("card") or name
            aliases = []
            aliases.extend(self._extract_aliases_from_name(name))
            aliases.extend(self._extract_aliases_from_text(item.text or ""))
            aliases.extend(await self._load_world_aliases(project_id, card_name, alias_cache))
            candidates.append(
                {
                    "name": name,
                    "aliases": self._normalize_aliases(aliases, name),
                    "type": "world_entity",
                    "source_id": item.id,
                }
            )
        return candidates

    async def _build_world_rule_candidates(self, project_id: str) -> List[Dict[str, Any]]:
        await evidence_service.build_cards_index(project_id, force=False)
        items = await evidence_service.index_storage.read_items(project_id, evidence_service.CARDS_INDEX)
        rules: List[Dict[str, Any]] = []
        for item in items:
            if item.type != "world_rule":
                continue
            text = str(item.text or "").strip()
            if not text:
                continue
            rules.append(
                {
                    "id": item.id,
                    "text": text,
                    "source": item.source or {},
                    "entities": item.entities or [],
                }
            )
        return rules

    def _match_entities(
        self,
        text: str,
        chunks: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        recent_entities: List[str],
        kind: str,
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        hits: List[Tuple[str, float]] = []
        sources: List[Dict[str, Any]] = []
        cleaned_text = text or ""
        recent_set = set(recent_entities or [])

        for candidate in candidates:
            name = candidate.get("name") or ""
            if not name:
                continue
            score_data = self._score_candidate(cleaned_text, chunks, candidate, recent_set)
            if not score_data.get("matched"):
                continue
            hits.append((name, score_data["score"]))
            sources.append(
                {
                    "entity": name,
                    "type": kind,
                    "count": score_data.get("count", 0),
                    "score": round(score_data.get("score", 0.0), 4),
                    "matched_aliases": score_data.get("matched_aliases", []),
                    "examples": score_data.get("examples", [])[: self.max_examples],
                }
            )

        hits.sort(key=lambda item: (-item[1], item[0]))
        return [name for name, _score in hits], sources

    def _match_rules(
        self,
        text: str,
        chunks: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        hits: List[Tuple[str, float]] = []
        sources: List[Dict[str, Any]] = []
        cleaned_text = text or ""

        for rule in rules:
            rule_text = rule.get("text") or ""
            if not rule_text:
                continue
            score, examples = self._score_rule(cleaned_text, chunks, rule_text)
            if score <= 0:
                continue
            hits.append((rule["id"], score))
            sources.append(
                {
                    "rule_id": rule["id"],
                    "type": "world_rule",
                    "score": round(score, 4),
                    "examples": examples[: self.max_examples],
                    "text": rule_text,
                }
            )

        hits.sort(key=lambda item: (-item[1], item[0]))
        return [rule_id for rule_id, _score in hits], sources

    def _score_candidate(
        self,
        text: str,
        chunks: List[Dict[str, Any]],
        candidate: Dict[str, Any],
        recent_set: set,
    ) -> Dict[str, Any]:
        aliases = [candidate.get("name") or ""]
        aliases.extend(candidate.get("aliases") or [])
        min_alias_len = 1 if candidate.get("type") == "character" else None
        aliases = self._normalize_aliases(aliases, candidate.get("name") or "", min_length=min_alias_len)
        total_count = 0
        matched_aliases: List[str] = []
        examples: List[str] = []

        for alias in aliases:
            count, alias_examples = self._find_occurrences(text, alias)
            if count <= 0:
                continue
            total_count += count
            matched_aliases.append(alias)
            examples.extend(alias_examples)

        best_score = 0.0
        if total_count == 0:
            for alias in aliases:
                score, example, term_hits = self._best_bm25_match(alias, chunks)
                threshold = self.BM25_THRESHOLD_GENERIC if self._is_generic_name(alias) else self.BM25_THRESHOLD
                if term_hits < self._min_term_hits(alias):
                    continue
                if score >= threshold and score > best_score:
                    best_score = score
                    if example:
                        examples.append(example)
                    matched_aliases = [alias]

        score = total_count * 2.0 + best_score
        if candidate.get("name") in recent_set:
            score += 0.8

        matched = total_count > 0 or best_score >= (
            self.BM25_THRESHOLD_GENERIC if self._is_generic_name(candidate.get("name") or "") else self.BM25_THRESHOLD
        )
        return {
            "matched": matched,
            "score": score,
            "count": total_count,
            "matched_aliases": matched_aliases,
            "examples": examples,
        }

    def _score_rule(
        self,
        text: str,
        chunks: List[Dict[str, Any]],
        rule_text: str,
    ) -> Tuple[float, List[str]]:
        terms = _extract_terms(rule_text)
        if not terms:
            return 0.0, []
        min_hits = 2 if len(terms) <= 4 else 3
        if self._term_overlap(text, terms) < min_hits:
            return 0.0, []

        best_score, example, _hits = self._best_bm25_match(rule_text, chunks)
        if rule_text and rule_text in text:
            best_score += 0.8
            example = example or self._make_chunk_snippet(rule_text)

        if best_score < self.RULE_THRESHOLD:
            return 0.0, []
        examples = [example] if example else []
        return best_score, examples

    def _best_bm25_match(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
    ) -> Tuple[float, Optional[str], int]:
        terms = _extract_terms(query)
        if not terms or not chunks:
            return 0.0, None, 0

        df = {term: 0 for term in terms}
        total_docs = max(len(chunks), 1)
        for chunk in chunks:
            chunk_text = chunk.get("text") or ""
            for term in terms:
                if _count_term(chunk_text, term) > 0:
                    df[term] += 1

        avgdl = sum(_estimate_doc_len(chunk.get("text") or "") for chunk in chunks) / total_docs
        best_score = 0.0
        best_chunk = None
        best_hits = 0

        for chunk in chunks:
            chunk_text = chunk.get("text") or ""
            doc_len = _estimate_doc_len(chunk_text)
            score = _bm25_score(chunk_text, terms, df, total_docs, avgdl, doc_len)
            if score <= 0:
                continue
            term_hits = sum(1 for term in terms if _count_term(chunk_text, term) > 0)
            if score > best_score:
                best_score = score
                best_chunk = chunk_text
                best_hits = term_hits

        example = self._make_chunk_snippet(best_chunk) if best_chunk else None
        return best_score, example, best_hits

    def _term_overlap(self, text: str, terms: List[str]) -> int:
        count = 0
        for term in terms:
            if term and term in text:
                count += 1
        return count

    def _extract_world_entity_name(self, item: Any) -> str:
        text = str(getattr(item, "text", "") or "").strip()
        if text:
            for sep in [":", "\uff1a"]:
                if sep in text:
                    return text.split(sep)[0].strip()
        source = getattr(item, "source", {}) or {}
        return str(source.get("card") or text or "").strip()

    def _extract_aliases_from_name(self, name: str) -> List[str]:
        aliases = []
        for pattern in [r"\(([^)]+)\)", r"（([^）]+)）"]:
            for match in re.findall(pattern, name or ""):
                aliases.append(match.strip())
        return self._normalize_aliases(aliases, name)

    def _extract_aliases_from_text(self, text: str) -> List[str]:
        aliases = []
        if not text:
            return []
        for pattern in [
            r"(?:又称|亦称|也称|别称|别名|简称)[:：]?\s*([^\s，。；;,.、]{2,20})",
        ]:
            for match in re.findall(pattern, text):
                aliases.append(match.strip())
        return aliases

    async def _load_world_aliases(
        self,
        project_id: str,
        card_name: str,
        cache: Dict[str, List[str]],
    ) -> List[str]:
        if not card_name:
            return []
        if card_name in cache:
            return cache[card_name]
        file_path = self.card_storage.get_project_path(project_id) / "cards" / "world" / f"{card_name}.yaml"
        if not file_path.exists():
            cache[card_name] = []
            return []
        try:
            data = await self.card_storage.read_yaml(file_path)
        except Exception:
            cache[card_name] = []
            return []
        aliases = []
        for key in ["aliases", "alias", "aka", "nicknames"]:
            value = data.get(key)
            if isinstance(value, list):
                aliases.extend([str(item).strip() for item in value if item])
            elif isinstance(value, str):
                aliases.extend(re.split(r"[、,/;；]", value))
        aliases.extend(self._extract_aliases_from_text(str(data.get("description") or "")))
        cache[card_name] = self._normalize_aliases(aliases, card_name)
        return cache[card_name]

    def _normalize_aliases(self, aliases: List[str], name: str, min_length: Optional[int] = None) -> List[str]:
        try:
            required_len = int(min_length) if min_length is not None else int(self.min_name_length)
        except Exception:
            required_len = 2

        seen = set()
        result = []
        for alias in aliases:
            alias = str(alias or "").strip()
            if not alias:
                continue
            if len(alias) < required_len:
                continue
            if alias in seen:
                continue
            seen.add(alias)
            result.append(alias)
        return result

    def _make_chunk_snippet(self, text: Optional[str]) -> str:
        if not text:
            return ""
        snippet = text.strip()[: max(self.snippet_radius * 4, 80)]
        return re.sub(r"\s+", " ", snippet).strip()

    def _find_occurrences(self, text: str, name: str) -> Tuple[int, List[str]]:
        if not text or not name:
            return 0, []
        examples: List[str] = []
        count = 0
        start = 0
        while True:
            idx = text.find(name, start)
            if idx == -1:
                break
            count += 1
            if len(examples) < self.max_examples:
                examples.append(self._make_snippet(text, idx, len(name)))
            start = idx + len(name)
        return count, examples

    def _make_snippet(self, text: str, start: int, length: int) -> str:
        left = max(0, start - self.snippet_radius)
        right = min(len(text), start + length + self.snippet_radius)
        snippet = text[left:right].replace("\n", " ").strip()
        return re.sub(r"\s+", " ", snippet)

    def _is_generic_name(self, name: str) -> bool:
        return name in self.GENERIC_TERMS

    def _min_term_hits(self, alias: str) -> int:
        terms = _extract_terms(alias)
        if len(terms) <= 2:
            return 1
        if len(terms) <= 4:
            return 2
        return 3

    def _is_valid_name(self, name: str, min_length: Optional[int] = None) -> bool:
        name = str(name or "").strip()
        if not name:
            return False

        try:
            required_len = int(min_length) if min_length is not None else int(self.min_name_length)
        except Exception:
            required_len = 2

        if len(name) < required_len:
            return False

        if any(ch.isspace() for ch in name):
            return False

        if re.fullmatch(r"v\d+c\d+(?:[ei]\d+)?", name, flags=re.IGNORECASE):
            return False

        if name in self.NAME_STOPWORDS:
            return False

        if name in self.GENERIC_TERMS:
            return False

        # Avoid treating pure ASCII tokens as entity names (e.g. "json", "http", "id").
        if re.fullmatch(r"[a-z0-9_\\-]+", name, flags=re.IGNORECASE):
            return False

        if len(name) > 40:
            return False

        return True

    def _empty_payload(self, chapter: str) -> Dict[str, Any]:
        return {
            "chapter": chapter,
            "characters": [],
            "world_entities": [],
            "world_rules": [],
            "sources": [],
            "draft_path": "",
            "built_at": time.time(),
        }

    async def _resolve_chapters(
        self,
        project_id: str,
        chapters: Optional[List[str]] = None,
    ) -> List[str]:
        if not chapters:
            chapter_list = await self.draft_storage.list_chapters(project_id)
        else:
            chapter_list = [str(ch).strip() for ch in chapters if str(ch).strip()]
        chapter_list = [normalize_chapter_id(ch) or ch for ch in chapter_list]
        chapter_list = [ch for ch in chapter_list if ch]
        return ChapterIDValidator.sort_chapters(list(dict.fromkeys(chapter_list)))


chapter_binding_service = ChapterBindingService()
