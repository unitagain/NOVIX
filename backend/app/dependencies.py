# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  依赖注入工厂 - FastAPI Depends() 工厂函数，统一管理存储实例创建
  Dependency Injection - FastAPI Depends() factories for centralized storage instance management.

设计原则 / Design Principles:
  所有Router应通过 Depends() 获取Storage实例，而非模块级实例化。
  This ensures:
  - Consistent singleton instance creation
  - Easy testing (can be overridden in tests)
  - Centralized configuration point
  - No global state coupling
"""

from functools import lru_cache
from typing import TYPE_CHECKING

from app.storage.cards import CardStorage
from app.storage.canon import CanonStorage
from app.storage.drafts import DraftStorage
from app.storage.evidence_index import EvidenceIndexStorage
from app.storage.bindings import ChapterBindingStorage
from app.storage.memory_pack import MemoryPackStorage
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.pending_actions import PendingActionStorage
from app.storage.volumes import VolumeStorage

if TYPE_CHECKING:  # 仅用于类型注解，运行时保持惰性导入，避免加重启动依赖图
    from app.storage.character_relations import CharacterRelationStorage
    from app.storage.outline import OutlineStorage


@lru_cache(maxsize=1)
def get_card_storage() -> CardStorage:
    """
    获取或创建CardStorage的单例实例

    Get or create singleton CardStorage instance.

    Returns:
        CardStorage实例 / CardStorage instance
    """
    return CardStorage()


@lru_cache(maxsize=1)
def get_canon_storage() -> CanonStorage:
    """
    获取或创建CanonStorage的单例实例

    Get or create singleton CanonStorage instance.

    Returns:
        CanonStorage实例 / CanonStorage instance
    """
    return CanonStorage()


@lru_cache(maxsize=1)
def get_draft_storage() -> DraftStorage:
    """
    获取或创建DraftStorage的单例实例

    Get or create singleton DraftStorage instance.

    Returns:
        DraftStorage实例 / DraftStorage instance
    """
    return DraftStorage()


@lru_cache(maxsize=1)
def get_outline_storage() -> "OutlineStorage":
    """获取或创建 OutlineStorage 单例（全文规划大纲，非章节）。"""
    from app.storage.outline import OutlineStorage

    return OutlineStorage()


@lru_cache(maxsize=1)
def get_character_relation_storage() -> "CharacterRelationStorage":
    """获取或创建 CharacterRelationStorage 单例（角色关系图谱，作者设定层）。"""
    from app.storage.character_relations import CharacterRelationStorage

    return CharacterRelationStorage()


@lru_cache(maxsize=1)
def get_evidence_storage() -> EvidenceIndexStorage:
    """
    获取或创建EvidenceIndexStorage的单例实例

    Get or create singleton EvidenceIndexStorage instance.

    Returns:
        EvidenceIndexStorage实例 / EvidenceIndexStorage instance
    """
    return EvidenceIndexStorage()


@lru_cache(maxsize=1)
def get_binding_storage() -> ChapterBindingStorage:
    """
    获取或创建ChapterBindingStorage的单例实例

    Get or create singleton ChapterBindingStorage instance.

    Returns:
        ChapterBindingStorage实例 / ChapterBindingStorage instance
    """
    return ChapterBindingStorage()


@lru_cache(maxsize=1)
def get_memory_pack_storage() -> MemoryPackStorage:
    """
    获取或创建MemoryPackStorage的单例实例

    Get or create singleton MemoryPackStorage instance.

    Returns:
        MemoryPackStorage实例 / MemoryPackStorage instance
    """
    return MemoryPackStorage()


@lru_cache(maxsize=1)
def get_creative_memory_storage() -> CreativeMemoryStorage:
    """
    获取或创建CreativeMemoryStorage的单例实例

    Get or create singleton CreativeMemoryStorage instance.
    """
    return CreativeMemoryStorage()


@lru_cache(maxsize=1)
def get_pending_action_storage() -> PendingActionStorage:
    """获取或创建PendingActionStorage单例实例。"""
    return PendingActionStorage()


@lru_cache(maxsize=1)
def get_volume_storage() -> VolumeStorage:
    """
    获取或创建VolumeStorage的单例实例

    Get or create singleton VolumeStorage instance.

    Returns:
        VolumeStorage实例 / VolumeStorage instance
    """
    return VolumeStorage()
