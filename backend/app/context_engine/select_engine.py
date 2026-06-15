# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  上下文选择引擎 - 智能选择相关上下文项
  Context Selection Engine - Intelligently selects relevant context items for LLM calls
  Supports both deterministic selection (critical items) and retrieval-based selection
  (ranked by relevance using embeddings or BM25).
"""

from typing import List, Optional, Dict, Any
import math
from .models import ContextItem, ContextPriority, ContextType
from .text_tokenizer import calculate_overlap_score, calculate_bm25_score, build_idf_table
from app.config import config
from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContextSelectEngine:
    """
    上下文选择引擎 - 为LLM调用选择最相关的上下文项

    Selects relevant context items for writing agents based on query relevance.
    Supports both deterministic selection (always include critical items like style cards)
    and retrieval-based selection (rank by relevance using embeddings or keyword matching).

    For facts, applies logarithmic distance decay so that recently introduced facts
    score higher than distant ones, while long-term world-building facts are never
    completely discarded.

    Attributes:
        embeddings (Optional): 嵌入服务实例 / Optional embeddings service for semantic ranking.
        MAX_CANDIDATES_PER_TYPE (int): 每种类型最大候选数量 / Max candidates per item type.
    """

    def __init__(self, embeddings_service=None):
        """
        初始化上下文选择引擎 / Initialize the context selection engine.

        Args:
            embeddings_service: 可选的嵌入服务 / Optional embeddings service for semantic similarity.
        """
        self.embeddings = embeddings_service
        self._distance_alpha: float = float(config.get("context_budget", {}).get("fact_distance_alpha", 0.3))

    # ========================================================================
    # 确定性选择：必须加载的关键项 / Deterministic Selection: Critical items
    # ========================================================================

    async def deterministic_select(self, project_id: str, agent_name: str, storage: Any) -> List[ContextItem]:
        """
        确定性选择 - 加载特定智能体必须使用的项 / Deterministic selection for critical items.

        Always loads critical items (like style cards) that should be included
        regardless of query relevance. Maintains consistent voice and style.

        Args:
            project_id: 项目ID / Project identifier.
            agent_name: 智能体名称 / Agent name (archivist, writer, editor).
            storage: 统一存储适配器 / Unified storage adapter.

        Returns:
            关键上下文项列表 / List of critical ContextItems.
        """
        items = []
        always_load_map = {
            "archivist": ["style_card"],
            "writer": ["style_card", "scene_brief"],
            "editor": ["style_card"],
        }

        item_types = always_load_map.get(agent_name, [])
        for item_type in item_types:
            item = await self._load_item(project_id, item_type, storage)
            if item:
                item.priority = ContextPriority.CRITICAL
                items.append(item)
        return items

    async def _load_item(self, project_id: str, item_type: str, storage: Any) -> Optional[ContextItem]:
        """
        加载单个上下文项（如风格卡片） / Load a single context item (e.g., style card).

        Args:
            project_id: 项目ID / Project identifier.
            item_type: 项目类型 / Item type (style_card, scene_brief, etc).
            storage: 统一存储适配器 / Unified storage adapter.

        Returns:
            上下文项或None / ContextItem or None if not found.
        """
        try:
            if item_type == "style_card":
                card = await storage.get_style_card(project_id)
                if card:
                    return ContextItem(
                        id="style_card",
                        type=ContextType.STYLE_CARD,
                        content=self._format_card(card),
                        priority=ContextPriority.CRITICAL,
                    )
        except Exception as exc:
            logger.warning("Error loading %s: %s", item_type, exc)
        return None

    def _format_card(self, card: Dict[str, Any]) -> str:
        """
        格式化卡片为可读字符串 / Format card dict as readable string.

        Args:
            card: 卡片数据 / Card data dict or object.

        Returns:
            格式化的字符串 / Formatted string representation.
        """
        if hasattr(card, "model_dump"):
            try:
                payload = card.model_dump(exclude_none=True)
                if isinstance(payload, dict):
                    return "\n".join(f"{k}: {v}" for k, v in payload.items() if v)
            except Exception:
                pass
        if isinstance(card, dict):
            return "\n".join(f"{k}: {v}" for k, v in card.items() if v)
        return str(card)

    # ========================================================================
    # 检索式选择：基于查询的相关性排序 / Retrieval Selection: Query-based ranking
    # ========================================================================

    # Maximum candidates to load per item type to prevent memory bloat
    # 每种类型最大候选加载数量，防止内存膨胀
    MAX_CANDIDATES_PER_TYPE = 50

    def _get_candidate_limit(self, total_chapters: int = 0) -> int:
        """
        根据总章节数动态计算候选上限 / Dynamically compute candidate limit based on chapter count.

        Formula: min(max(50, total_chapters * 3), 500)

        | total_chapters | limit | note                          |
        |----------------|-------|-------------------------------|
        | 1-16           | 50    | short work, keep default      |
        | 50             | 150   | medium, wider coverage        |
        | 100            | 300   | long, cover major facts       |
        | 200+           | 500   | capped to avoid perf issues   |

        Args:
            total_chapters: 项目总章节数 / Total number of chapters in the project.

        Returns:
            候选上限 / Candidate limit for each item type.
        """
        if total_chapters <= 0:
            return self.MAX_CANDIDATES_PER_TYPE
        return min(max(50, total_chapters * 3), 500)

    async def retrieval_select(
        self,
        project_id: str,
        query: str,
        item_types: List[str],
        storage: Any,
        top_k: int = 5,
        current_chapter: str = "",
        total_chapters: int = 0,
    ) -> List[ContextItem]:
        """
        检索式选择 - 基于查询相关性排序项目 / Retrieval-based selection ranked by query relevance.

        Loads candidates from each item type, computes relevance scores using embeddings
        or keyword matching, and returns top-k most relevant items.

        For facts, when *current_chapter* is provided, the text relevance score is
        multiplied by a logarithmic distance decay factor so that recently introduced
        facts rank higher.

        Args:
            project_id: 项目ID / Project identifier.
            query: 搜索查询文本 / Search query text.
            item_types: 要搜索的项目类型列表 / Item types to search (character, world, fact, text_chunk).
            storage: 统一存储适配器 / Unified storage adapter.
            top_k: 返回的最大项目数 / Maximum items to return (default 5).
            current_chapter: 当前章节ID（用于事实距离衰减） /
                Current chapter ID for fact distance decay (e.g. "V1C10"). Optional.
            total_chapters: 项目总章节数（用于动态候选池上限） /
                Total chapter count for dynamic candidate limit. 0 = use default.

        Returns:
            按相关性排序的上下文项列表 / List of ContextItems sorted by relevance.
        """
        query = str(query or "").strip()
        if not query:
            return []

        top_k = max(int(top_k or 0), 0)
        if top_k <= 0:
            return []

        item_types = [str(t or "").strip().lower() for t in (item_types or []) if str(t or "").strip()]
        if not item_types:
            return []

        candidates: List[ContextItem] = []
        candidate_limit = self._get_candidate_limit(total_chapters)
        query_lower = query.lower()

        # 预加载事实文本，构建 IDF 表提升 BM25 区分度
        # Pre-load fact statements to build IDF table for better BM25 discrimination.
        idf_table = None
        fact_list = []
        if "fact" in item_types:
            try:
                fact_list = await storage.get_all_facts(project_id) or []
            except Exception as exc:
                logger.warning("Failed to load facts: %s", exc)
                fact_list = []

        # 从所有候选文本构建 IDF 表（事实通常数量最多，主导 IDF 分布）
        # Build IDF from fact statements (usually the most numerous, dominating IDF distribution).
        if fact_list:
            idf_docs = [str(getattr(f, "statement", "") or "") for f in fact_list if getattr(f, "statement", None)]
            if idf_docs:
                idf_table = build_idf_table(idf_docs)

        def score_text(text: str) -> float:
            text = str(text or "").strip()
            if not text:
                return 0.0
            try:
                overlap = calculate_overlap_score(query, text)
            except Exception:
                overlap = 0.0
            try:
                bm25 = calculate_bm25_score(query, text, idf_table=idf_table)
            except Exception:
                bm25 = 0.0
            # Hybrid lexical score: overlap provides robustness for short queries,
            # bm25 stabilizes for longer contexts.
            return float(overlap) * 0.35 + float(bm25) * 0.65

        # Character cards / 角色卡
        if "character" in item_types:
            try:
                names = await storage.list_character_cards(project_id)
            except Exception as exc:
                logger.warning("Failed to list character cards: %s", exc)
                names = []
            # 截断前按名称是否出现在 query 中排序，确保相关角色不被丢弃
            # Sort names by query relevance before truncation so related cards survive the cut
            names = sorted(
                (names or []),
                key=lambda n: (0 if str(n).lower() in query_lower else 1, n),
            )
            for name in names[:candidate_limit]:
                try:
                    card = await storage.get_character_card(project_id, name)
                except Exception:
                    card = None
                if not card:
                    continue
                content = self._format_card(card)
                s = score_text(content)
                if s <= 0:
                    continue
                candidates.append(
                    ContextItem(
                        id=f"char_{name}",
                        type=ContextType.CHARACTER_CARD,
                        content=content,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata={"name": name},
                    )
                )

        # World cards / 世界观卡
        if "world" in item_types:
            try:
                names = await storage.list_world_cards(project_id)
            except Exception as exc:
                logger.warning("Failed to list world cards: %s", exc)
                names = []
            # 截断前按名称是否出现在 query 中排序
            names = sorted(
                (names or []),
                key=lambda n: (0 if str(n).lower() in query_lower else 1, n),
            )
            for name in names[:candidate_limit]:
                try:
                    card = await storage.get_world_card(project_id, name)
                except Exception:
                    card = None
                if not card:
                    continue
                content = self._format_card(card)
                s = score_text(content)
                if s <= 0:
                    continue
                candidates.append(
                    ContextItem(
                        id=f"world_{name}",
                        type=ContextType.WORLD_CARD,
                        content=content,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata={"name": name},
                    )
                )

        # Canon facts / 事实（已在上方预加载到 fact_list）
        if "fact" in item_types:
            # 按 introduced_in 倒序排列，截断时保留最新事实而非最旧的
            # Sort by introduced_in descending so truncation keeps newest facts.
            sorted_facts = sorted(
                fact_list,
                key=lambda f: getattr(f, "introduced_in", "") or "",
                reverse=True,
            )
            for idx, fact in enumerate(sorted_facts[:candidate_limit]):
                try:
                    statement = str(getattr(fact, "statement", "") or "").strip()
                    fact_id = str(getattr(fact, "id", "") or "").strip() or f"F{idx + 1:04d}"
                    introduced_in = str(getattr(fact, "introduced_in", "") or "").strip()
                except Exception:
                    continue
                if not statement:
                    continue
                s = score_text(statement)
                if s <= 0:
                    continue
                # 距离衰减：近期事实优先，远期事实降权但不归零
                # Logarithmic distance decay: recent facts score higher,
                # distant facts are down-weighted but never zeroed out.
                s *= self._calculate_distance_decay(current_chapter, introduced_in)
                candidates.append(
                    ContextItem(
                        id=fact_id,
                        type=ContextType.FACT,
                        content=statement,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata={"introduced_in": introduced_in},
                    )
                )

        # Text chunks / 正文片段
        if "text_chunk" in item_types:
            try:
                chunks = await storage.search_text_chunks(project_id, query, limit=candidate_limit)
            except Exception as exc:
                logger.warning("Failed to search text chunks: %s", exc)
                chunks = []
            for idx, chunk in enumerate(chunks or []):
                if not isinstance(chunk, dict):
                    continue
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue
                s = score_text(text)
                if s <= 0:
                    continue
                candidates.append(
                    ContextItem(
                        id=f"text_{idx}",
                        type=ContextType.TEXT_CHUNK,
                        content=text,
                        priority=ContextPriority.LOW,
                        relevance_score=s,
                        metadata={"source": chunk.get("source") or {}, "chapter": chunk.get("chapter")},
                    )
                )

        if not candidates:
            return []

        candidates.sort(key=lambda item: float(item.relevance_score or 0.0), reverse=True)
        return candidates[:top_k]

    # ========================================================================
    # 距离衰减 / Distance Decay
    # ========================================================================

    def _calculate_distance_decay(self, current_chapter: str, introduced_in: str) -> float:
        """
        计算事实的距离衰减系数 / Calculate logarithmic distance decay for a fact.

        Uses the formula: ``decay = 1.0 / (1.0 + alpha * ln(1 + distance))``
        where *alpha* is loaded from ``config.yaml → context_budget.fact_distance_alpha``.

        Properties of this formula:
        - distance=0  → decay=1.0  (same chapter, no penalty)
        - distance=10 → decay≈0.57 (α=0.3)
        - distance=50 → decay≈0.46
        - distance=200 → decay≈0.37 (distant facts still retain ~37% weight)

        Returns 1.0 (no decay) when either chapter ID is empty, unparseable,
        or when distance_alpha is configured as 0.

        Args:
            current_chapter: 当前章节ID / Current chapter being written (e.g. "V1C10").
            introduced_in: 事实引入章节ID / Chapter where the fact was introduced.

        Returns:
            衰减系数 (0, 1] / Decay factor in range (0.0, 1.0].
        """
        alpha = self._distance_alpha
        if alpha <= 0 or not current_chapter or not introduced_in:
            return 1.0
        try:
            dist = ChapterIDValidator.calculate_distance(current_chapter, introduced_in)
            if dist <= 0:
                return 1.0
            return 1.0 / (1.0 + alpha * math.log(1 + dist))
        except Exception:
            return 1.0
