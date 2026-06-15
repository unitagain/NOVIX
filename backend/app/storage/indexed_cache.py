# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  带索引的存储层 - 内存索引存储包装器，为事实、时间线、角色状态提供 O(1) 查询。
  Indexed storage cache - Memory-indexed storage wrapper for O(1) lookups of facts, timelines, and character states.
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IndexEntry:
    """索引条目"""

    line_number: int
    data: Dict[str, Any]


@dataclass
class ProjectIndex:
    """项目索引"""

    project_id: str
    # 事实索引
    facts_by_id: Dict[str, IndexEntry] = field(default_factory=dict)
    facts_by_chapter: Dict[str, List[str]] = field(default_factory=dict)  # chapter -> [fact_ids]
    # 时间线索引
    timeline_by_id: Dict[str, IndexEntry] = field(default_factory=dict)
    timeline_by_chapter: Dict[str, List[str]] = field(default_factory=dict)
    # 角色状态索引
    states_by_character: Dict[str, List[IndexEntry]] = field(default_factory=dict)
    # 元数据
    last_updated: datetime = field(default_factory=datetime.now)
    facts_count: int = 0
    timeline_count: int = 0
    states_count: int = 0


class IndexedStorageCache:
    """
    索引存储缓存

    为存储层提供内存索引，实现 O(1) 查询
    """

    def __init__(self, max_projects: int = 10):
        """
        初始化缓存

        Args:
            max_projects: 最大缓存项目数
        """
        self._indices: Dict[str, ProjectIndex] = {}
        self._max_projects = max_projects
        self._lock = asyncio.Lock()

    async def get_or_build_index(
        self,
        project_id: str,
        storage: Any,
        force_rebuild: bool = False,
    ) -> ProjectIndex:
        """
        获取或构建项目索引

        Args:
            project_id: 项目 ID
            storage: 存储实例（CanonStorage）
            force_rebuild: 是否强制重建

        Returns:
            项目索引
        """
        async with self._lock:
            if not force_rebuild and project_id in self._indices:
                return self._indices[project_id]

            # 构建新索引
            index = await self._build_index(project_id, storage)

            # LRU 淘汰
            if len(self._indices) >= self._max_projects:
                oldest = min(self._indices.values(), key=lambda x: x.last_updated)
                del self._indices[oldest.project_id]

            self._indices[project_id] = index
            return index

    async def _build_index(self, project_id: str, storage: Any) -> ProjectIndex:
        """构建项目索引"""
        index = ProjectIndex(project_id=project_id)

        # 索引事实
        try:
            facts = await storage.get_all_facts_raw(project_id)
            for line_num, fact in enumerate(facts):
                fact_id = fact.get("id", f"F{line_num:04d}")
                chapter = fact.get("introduced_in") or fact.get("source") or ""

                entry = IndexEntry(line_number=line_num, data=fact)
                index.facts_by_id[fact_id] = entry

                if chapter:
                    if chapter not in index.facts_by_chapter:
                        index.facts_by_chapter[chapter] = []
                    index.facts_by_chapter[chapter].append(fact_id)

            index.facts_count = len(facts)
        except Exception as e:
            logger.warning("Failed to index facts for %s: %s", project_id, e)

        # 索引时间线
        try:
            if hasattr(storage, "get_all_timeline_events"):
                events = await storage.get_all_timeline_events(project_id)
                for line_num, event in enumerate(events):
                    event_dict = event.model_dump() if hasattr(event, "model_dump") else dict(event)
                    event_id = event_dict.get("id", f"T{line_num:04d}")
                    chapter = event_dict.get("chapter", "")

                    entry = IndexEntry(line_number=line_num, data=event_dict)
                    index.timeline_by_id[event_id] = entry

                    if chapter:
                        if chapter not in index.timeline_by_chapter:
                            index.timeline_by_chapter[chapter] = []
                        index.timeline_by_chapter[chapter].append(event_id)

                index.timeline_count = len(events)
        except Exception as e:
            logger.debug("Failed to index timeline for %s: %s", project_id, e)

        # 索引角色状态
        try:
            if hasattr(storage, "get_all_character_states"):
                states = await storage.get_all_character_states(project_id)
                for line_num, state in enumerate(states):
                    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
                    character = state_dict.get("character_name", "")

                    entry = IndexEntry(line_number=line_num, data=state_dict)

                    if character:
                        if character not in index.states_by_character:
                            index.states_by_character[character] = []
                        index.states_by_character[character].append(entry)

                index.states_count = len(states)
        except Exception as e:
            logger.debug("Failed to index character states for %s: %s", project_id, e)

        index.last_updated = datetime.now()
        logger.debug(
            f"Built index for {project_id}: " f"{index.facts_count} facts, {index.timeline_count} timeline events"
        )

        return index

    async def invalidate(self, project_id: str) -> None:
        """使项目索引失效"""
        async with self._lock:
            if project_id in self._indices:
                del self._indices[project_id]

    async def append_fact(self, project_id: str, fact_data: Dict[str, Any]) -> None:
        """
        Incrementally append a fact to the in-memory index (O(1)).

        If no index exists for the project yet, this is a no-op (the index
        will be built lazily on the next full query). This avoids the
        invalidate-then-rebuild cycle that costs O(N) per add_fact call.

        Args:
            project_id: Project identifier.
            fact_data: Serialized fact dict (must contain 'id').
        """
        async with self._lock:
            index = self._indices.get(project_id)
            if index is None:
                return

            fact_id = fact_data.get("id", f"F{index.facts_count:04d}")
            chapter = fact_data.get("introduced_in") or fact_data.get("source") or ""

            entry = IndexEntry(line_number=index.facts_count, data=fact_data)
            index.facts_by_id[fact_id] = entry

            if chapter:
                if chapter not in index.facts_by_chapter:
                    index.facts_by_chapter[chapter] = []
                index.facts_by_chapter[chapter].append(fact_id)

            index.facts_count += 1
            index.last_updated = datetime.now()

    async def invalidate_all(self) -> None:
        """使所有索引失效"""
        async with self._lock:
            self._indices.clear()

    def get_fact_by_id(self, project_id: str, fact_id: str) -> Optional[Dict[str, Any]]:
        """
        通过 ID 获取事实（O(1)）

        Args:
            project_id: 项目 ID
            fact_id: 事实 ID

        Returns:
            事实数据或 None
        """
        index = self._indices.get(project_id)
        if not index:
            return None

        entry = index.facts_by_id.get(fact_id)
        return entry.data if entry else None

    def get_facts_by_chapter(self, project_id: str, chapter: str) -> List[Dict[str, Any]]:
        """
        通过章节获取事实（O(k)，k 为该章节的事实数）

        Args:
            project_id: 项目 ID
            chapter: 章节 ID

        Returns:
            事实列表
        """
        index = self._indices.get(project_id)
        if not index:
            return []

        fact_ids = index.facts_by_chapter.get(chapter, [])
        return [index.facts_by_id[fid].data for fid in fact_ids if fid in index.facts_by_id]

    def get_timeline_by_chapter(self, project_id: str, chapter: str) -> List[Dict[str, Any]]:
        """通过章节获取时间线事件"""
        index = self._indices.get(project_id)
        if not index:
            return []

        event_ids = index.timeline_by_chapter.get(chapter, [])
        return [index.timeline_by_id[eid].data for eid in event_ids if eid in index.timeline_by_id]

    def get_character_states(self, project_id: str, character_name: str) -> List[Dict[str, Any]]:
        """获取角色状态"""
        index = self._indices.get(project_id)
        if not index:
            return []

        entries = index.states_by_character.get(character_name, [])
        return [entry.data for entry in entries]

    def get_stats(self, project_id: str) -> Dict[str, Any]:
        """获取索引统计信息"""
        index = self._indices.get(project_id)
        if not index:
            return {"indexed": False}

        return {
            "indexed": True,
            "facts_count": index.facts_count,
            "timeline_count": index.timeline_count,
            "states_count": index.states_count,
            "chapters_with_facts": len(index.facts_by_chapter),
            "last_updated": index.last_updated.isoformat(),
        }


# 全局索引缓存实例
_index_cache: Optional[IndexedStorageCache] = None


def get_index_cache() -> IndexedStorageCache:
    """获取全局索引缓存"""
    global _index_cache
    if _index_cache is None:
        _index_cache = IndexedStorageCache()
    return _index_cache
