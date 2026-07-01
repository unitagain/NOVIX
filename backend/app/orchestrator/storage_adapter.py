# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  统一存储适配器 - 为 ContextSelectEngine 提供统一接口
  Unified Storage Adapter - Exposes a unified interface for ContextSelectEngine
  to access various storage components (cards, canon, drafts) seamlessly.
"""

from typing import List, Optional, Any, Dict


class UnifiedStorageAdapter:
    """
    统一存储适配器 - 为 SelectEngine 适配多个存储组件

    Provides a unified interface to query cards, canon facts, and draft data
    required by the context selection engine. Acts as a facade to isolate
    SelectEngine from direct storage component dependencies.

    Attributes:
        card (CardStorage): 卡片存储 / Character and world card storage.
        canon (CanonStorage): 事实表存储 / Canon facts and timeline storage.
        draft (DraftStorage): 草稿存储 / Draft and scene brief storage.
    """

    def __init__(self, card_storage, canon_storage, draft_storage):
        """
        初始化存储适配器 / Initialize the unified adapter.

        Args:
            card_storage: 卡片存储实例 / Character/world card storage instance.
            canon_storage: 事实表存储实例 / Canon storage instance.
            draft_storage: 草稿存储实例 / Draft storage instance.
        """
        self.card = card_storage
        self.canon = canon_storage
        self.draft = draft_storage

    # ========================================================================
    # 卡片查询接口 / Card Query Interface
    # ========================================================================

    async def get_style_card(self, project_id: str) -> Optional[Dict]:
        """获取项目的写作风格卡片 / Get the style card for the project."""
        return await self.card.get_style_card(project_id)

    async def list_character_cards(self, project_id: str) -> List[str]:
        """列出所有角色卡片名称 / List all character card names."""
        return await self.card.list_character_cards(project_id)

    async def get_character_card(self, project_id: str, name: str) -> Optional[Dict]:
        """获取特定角色卡片 / Get a specific character card by name."""
        return await self.card.get_character_card(project_id, name)

    async def list_world_cards(self, project_id: str) -> List[str]:
        """列出所有世界观卡片名称 / List all world card names."""
        return await self.card.list_world_cards(project_id)

    async def get_world_card(self, project_id: str, name: str) -> Optional[Dict]:
        """获取特定世界观卡片 / Get a specific world card by name."""
        return await self.card.get_world_card(project_id, name)

    # ========================================================================
    # 事实表查询接口 / Canon Query Interface
    # ========================================================================

    async def get_all_facts(self, project_id: str) -> List[Any]:
        """获取所有事实 / Get all canonical facts for the project."""
        return await self.canon.get_all_facts(project_id)

    def get_relations_path(self, project_id: str):
        """返回 canon/relations.jsonl 的路径（轻量关系图数据源，Phase 4）。

        Returns the path to the relation-graph source file. The file may not exist
        yet (relations are appended incrementally by the Archivist); callers should
        tolerate a missing file (RelationGraph.load handles it gracefully).
        """
        return self.canon.get_project_path(project_id) / "canon" / "relations.jsonl"

    def get_embeddings_cache_path(self, project_id: str):
        """返回 canon/embeddings_cache.jsonl 的路径（语义向量缓存，Phase 4）。

        Returns the path to the content-addressed embedding cache. Lets the select
        engine embed each fact/card/chunk once and reuse the vector across queries
        (embed-once), instead of re-embedding every candidate on every query.
        """
        return self.canon.get_project_path(project_id) / "canon" / "embeddings_cache.jsonl"

    # ========================================================================
    # 草稿查询接口 / Draft Query Interface
    # ========================================================================

    async def get_scene_brief(self, project_id: str, chapter: str) -> Optional[Dict]:
        """获取场景简要 / Get the scene brief for a chapter."""
        return await self.draft.get_scene_brief(project_id, chapter)

    async def get_review(self, project_id: str, chapter: str) -> Optional[Dict]:
        """获取章节评阅 / Get the review for a chapter."""
        return await self.draft.get_review(project_id, chapter)

    # ========================================================================
    # 文本检索接口 / Text Search Interface
    # ========================================================================

    async def search_text_chunks(self, project_id: str, query: str, limit: int = 5) -> List[Dict]:
        """
        搜索文本片段 / Search for text chunks matching the query.

        Args:
            project_id: 项目ID / Project identifier.
            query: 搜索查询 / Search query string.
            limit: 返回的最大结果数 / Maximum number of results to return.

        Returns:
            匹配的文本片段列表 / List of matching text chunks.
        """
        if hasattr(self.draft, "search_text_chunks"):
            return await self.draft.search_text_chunks(project_id, query, limit=limit)
        return []
