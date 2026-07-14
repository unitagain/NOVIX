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
from contextvars import ContextVar
import math
from .models import ContextItem, ContextPriority, ContextType
from .text_tokenizer import calculate_overlap_score, calculate_bm25_score, build_idf_table
from .retrieval_pipeline import (
    ContextualIndexBuilder,
    RankingTraceRenderer,
    ScoreFusion,
    StorageCandidateSource,
    TemporalScopeFilter,
    VectorIndexAdapter,
)
from app.config import config
from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Reciprocal Rank Fusion constant (standard default). Larger = flatter rank weighting.
# 排名融合常数（业界默认 60）；越大则名次权重越平。
_RRF_K = 60


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

    def __init__(
        self,
        embeddings_service=None,
        *,
        reranker_service=None,
        fusion: Optional[str] = None,
        semantic_rerank: Optional[bool] = None,
        rerank_top_k: Optional[int] = None,
    ):
        """
        初始化上下文选择引擎 / Initialize the context selection engine.

        Args:
            embeddings_service: 可选的嵌入服务 / Optional embeddings service for semantic similarity.
                需实现 ``async embed(texts) -> List[List[float]]``（见 context_engine.embeddings）。
                为 None 时退化为纯词法（BM25 + 词重叠），行为与历史版本一致。
            fusion: 可选融合策略覆盖（``rrf`` / ``weighted``），用于可重复 A/B。
            reranker_service: 可选 cross-encoder 重排服务，需实现 ``async rerank(query, documents)``。
            semantic_rerank: 兼容旧参数；现在表示是否请求真实 reranker，不再按 embedding cosine 重排。
            rerank_top_k: 可选重排头部窗口覆盖。
        """
        self.embeddings = embeddings_service
        self.reranker = reranker_service
        self._distance_alpha: float = float(config.get("context_budget", {}).get("fact_distance_alpha", 0.3))
        retrieval_cfg = config.get("retrieval", {}) or {}
        hybrid_cfg = retrieval_cfg.get("hybrid", {}) or {}
        configured_fusion = str(fusion if fusion is not None else hybrid_cfg.get("fusion", "rrf")).lower()
        if configured_fusion not in {"rrf", "weighted"}:
            raise ValueError(f"unsupported retrieval fusion: {configured_fusion}")
        self._fusion: str = configured_fusion
        self._bm25_weight: float = float(hybrid_cfg.get("bm25_weight", 0.5))
        self._vector_weight: float = float(hybrid_cfg.get("vector_weight", 0.5))
        reranker_cfg = retrieval_cfg.get("reranker", {}) or {}
        self._rerank_requested: bool = bool(
            semantic_rerank if semantic_rerank is not None else reranker_cfg.get("enabled", False)
        )
        configured_rerank_top_k = rerank_top_k if rerank_top_k is not None else retrieval_cfg.get("rerank_top_k", 16)
        self._rerank_top_k: int = max(1, int(configured_rerank_top_k))
        # 内容寻址的语义向量缓存（embed-once）：每个项目一个 VectorStore，按文本 sha1 存向量，
        # 落 canon/embeddings_cache.jsonl 跨查询复用，避免每次检索重嵌入全部候选。
        # Content-addressed embedding cache (embed-once), one VectorStore per project,
        # persisted to disk so each fact/card/chunk is embedded once and reused.
        self.candidate_source = StorageCandidateSource()
        self.temporal_filter = TemporalScopeFilter()
        self.index_builder = ContextualIndexBuilder()
        self.vector_index = VectorIndexAdapter(self.embeddings) if self.embeddings is not None else None
        self.score_fusion = ScoreFusion(
            strategy=self._fusion,
            bm25_weight=self._bm25_weight,
            vector_weight=self._vector_weight,
        )
        self.trace_renderer = RankingTraceRenderer()
        # Phase 7 降级可见：语义检索是否已降级为纯词法（首次降级时 WARNING 一次并置此标志，供运行时查询）。
        self._semantic_degraded: bool = False
        self._reranker_degraded: bool = False
        self._ranking_trace_var: ContextVar[Dict[str, Any]] = ContextVar(
            f"context_select_trace_{id(self)}", default={}
        )
        self._last_ranking_trace: Dict[str, Any] = {}

    def _semantic_enabled(self) -> bool:
        """是否启用语义打分（注入了嵌入后端即启用）。"""
        return self.embeddings is not None

    def reset_ranking_trace(self) -> None:
        """清空最近一次检索 ranking trace。"""
        self._ranking_trace_var.set({})
        self._last_ranking_trace = {}

    def get_last_ranking_trace(self) -> Dict[str, Any]:
        """返回最近一次 retrieval_select 的 ranking trace（JSON-safe）。"""
        return dict(self._ranking_trace_var.get() or self._last_ranking_trace or {})

    def _set_ranking_trace(self, value: Dict[str, Any]) -> None:
        trace = dict(value or {})
        self._ranking_trace_var.set(trace)
        self._last_ranking_trace = trace

    def get_retrieval_policy(self) -> Dict[str, Any]:
        """返回当前检索策略，供 trace、benchmark 与运行时诊断使用。"""
        return {
            "semantic_enabled": self._semantic_enabled(),
            "fusion": self._fusion,
            "semantic_rerank": self._rerank_requested,
            "reranker_available": self.reranker is not None,
            "reranker_backend": type(self.reranker).__name__ if self.reranker is not None else None,
            "rerank_top_k": self._rerank_top_k,
        }

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
            except (AttributeError, TypeError, ValueError):
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

        Formula: min(max(80, total_chapters * 8), 1000)

        | total_chapters | limit | note                          |
        |----------------|-------|-------------------------------|
        | 1-16           | 80    | short work, avoid early fact truncation |
        | 50             | 400   | medium, wider coverage        |
        | 100            | 800   | long, cover major facts       |
        | 200+           | 1000  | capped to avoid perf issues   |

        Args:
            total_chapters: 项目总章节数 / Total number of chapters in the project.

        Returns:
            候选上限 / Candidate limit for each item type.
        """
        if total_chapters <= 0:
            return self.MAX_CANDIDATES_PER_TYPE
        return min(max(80, total_chapters * 8), 1000)

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
            self.reset_ranking_trace()
            return []

        top_k = max(int(top_k or 0), 0)
        if top_k <= 0:
            self.reset_ranking_trace()
            return []

        item_types = [str(t or "").strip().lower() for t in (item_types or []) if str(t or "").strip()]
        if not item_types:
            self.reset_ranking_trace()
            return []

        candidates: List[ContextItem] = []
        filter_trace = {
            "future_fact_filter_active": bool(current_chapter and "fact" in item_types),
            "future_facts_excluded": 0,
            "future_text_chunk_filter_active": bool(current_chapter and "text_chunk" in item_types),
            "future_text_chunks_excluded": 0,
        }
        candidate_limit = self._get_candidate_limit(total_chapters)
        query_lower = query.lower()
        # 语义启用时不按"词法零分"丢弃候选——让语义召回有机会救回字面无重叠但语义相关的条目。
        # When semantic is on, do NOT drop lexical-zero candidates: let embeddings rescue them.
        semantic = self._semantic_enabled()

        # 预加载事实文本，构建 IDF 表提升 BM25 区分度
        # Pre-load fact statements to build IDF table for better BM25 discrimination.
        idf_table = None
        fact_list = []
        if "fact" in item_types:
            try:
                fact_list = await self.candidate_source.facts(storage, project_id)
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
                names = await self.candidate_source.character_names(storage, project_id)
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
                    card = await self.candidate_source.character(storage, project_id, name)
                except Exception:
                    card = None
                if not card:
                    continue
                content = self._format_card(card)
                metadata = {"name": name, "source_type": "character_card"}
                index_text = self._contextual_index_text(content, metadata)
                s = score_text(index_text)
                if s <= 0 and not semantic:
                    continue
                if index_text != content:
                    metadata["_index_text"] = index_text
                metadata["_lex"] = s
                candidates.append(
                    ContextItem(
                        id=f"char_{name}",
                        type=ContextType.CHARACTER_CARD,
                        content=content,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata=metadata,
                    )
                )

        # World cards / 世界观卡
        if "world" in item_types:
            try:
                names = await self.candidate_source.world_names(storage, project_id)
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
                    card = await self.candidate_source.world(storage, project_id, name)
                except Exception:
                    card = None
                if not card:
                    continue
                content = self._format_card(card)
                metadata = {"name": name, "source_type": "world_card"}
                index_text = self._contextual_index_text(content, metadata)
                s = score_text(index_text)
                if s <= 0 and not semantic:
                    continue
                if index_text != content:
                    metadata["_index_text"] = index_text
                metadata["_lex"] = s
                candidates.append(
                    ContextItem(
                        id=f"world_{name}",
                        type=ContextType.WORLD_CARD,
                        content=content,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata=metadata,
                    )
                )

        # Canon facts / 事实（已在上方预加载到 fact_list）
        if "fact" in item_types:
            # 按 introduced_in 倒序排列，截断时保留最新事实而非最旧的
            # Sort by introduced_in descending so truncation keeps newest facts.
            sorted_facts = sorted(
                fact_list,
                key=lambda f: (
                    ChapterIDValidator.calculate_weight(str(getattr(f, "introduced_in", "") or "")),
                    str(getattr(f, "introduced_in", "") or ""),
                ),
                reverse=True,
            )
            eligible_facts = []
            for fact in sorted_facts:
                introduced_in = str(getattr(fact, "introduced_in", "") or "").strip()
                if self.temporal_filter.is_future(introduced_in, current_chapter):
                    filter_trace["future_facts_excluded"] += 1
                    continue
                eligible_facts.append(fact)

            for idx, fact in enumerate(eligible_facts[:candidate_limit]):
                try:
                    statement = str(getattr(fact, "statement", "") or "").strip()
                    fact_id = str(getattr(fact, "id", "") or "").strip() or f"F{idx + 1:04d}"
                    introduced_in = str(getattr(fact, "introduced_in", "") or "").strip()
                    context_prefix = str(getattr(fact, "context_prefix", "") or "").strip()
                    status = str(getattr(fact, "status", "needs_review") or "needs_review")
                except (AttributeError, TypeError, ValueError):
                    continue
                if not statement:
                    continue
                # Contextual Retrieval：用「情境前缀 + 事实」作为检索索引文本（词法+语义都打它），
                # 但展示/返回仍是原始 statement。前缀缺省时退化为纯 statement，行为不变。
                base_meta: Dict[str, Any] = {
                    "source_type": "fact",
                    "fact_id": fact_id,
                    "introduced_in": introduced_in,
                    "chapter": introduced_in,
                    "status": status,
                }
                if context_prefix:
                    base_meta["context_prefix"] = context_prefix
                index_text = self._contextual_index_text(statement, base_meta)
                s = score_text(index_text)
                if s <= 0 and not semantic:
                    continue
                # 距离衰减：近期事实优先，远期事实降权但不归零。
                # 衰减延后到融合之后统一施加（存入 _decay），以免污染语义/词法的排名融合。
                # Distance decay is deferred (stored as _decay) and applied after fusion,
                # so it doesn't distort the lexical/semantic rank fusion.
                decay = self._calculate_distance_decay(current_chapter, introduced_in)
                fact_meta: Dict[str, Any] = {**base_meta, "_decay": decay, "_lex": s}
                if index_text != statement:
                    fact_meta["_index_text"] = index_text  # 供语义嵌入使用；返回前清理
                candidates.append(
                    ContextItem(
                        id=fact_id,
                        type=ContextType.FACT,
                        content=statement,
                        priority=ContextPriority.MEDIUM,
                        relevance_score=s,
                        metadata=fact_meta,
                    )
                )

        # Text chunks / 正文片段
        if "text_chunk" in item_types:
            try:
                chunks = await self.candidate_source.text_chunks(storage, project_id, query, limit=candidate_limit)
            except Exception as exc:
                logger.warning("Failed to search text chunks: %s", exc)
                chunks = []
            for idx, chunk in enumerate(chunks or []):
                if not isinstance(chunk, dict):
                    continue
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue
                chunk_chapter = str(chunk.get("chapter") or "").strip()
                if self.temporal_filter.is_future(chunk_chapter, current_chapter):
                    filter_trace["future_text_chunks_excluded"] += 1
                    continue
                metadata = {
                    "source": chunk.get("source") or {},
                    "chapter": chunk.get("chapter"),
                    "source_type": "text_chunk",
                }
                index_text = self._contextual_index_text(text, metadata)
                s = score_text(index_text)
                if s <= 0 and not semantic:
                    continue
                if index_text != text:
                    metadata["_index_text"] = index_text
                metadata["_lex"] = s
                candidates.append(
                    ContextItem(
                        id=f"text_{idx}",
                        type=ContextType.TEXT_CHUNK,
                        content=text,
                        priority=ContextPriority.LOW,
                        relevance_score=s,
                        metadata=metadata,
                    )
                )

        if not candidates:
            self._set_ranking_trace({
                "query": query,
                "item_types": item_types,
                "candidate_count": 0,
                "returned": 0,
                "semantic_enabled": semantic,
                "semantic_degraded": self._semantic_degraded,
                "filters": filter_trace,
            })
            return []

        return await self._fuse_and_rank(
            candidates,
            query,
            top_k,
            project_id,
            storage,
            filter_trace=filter_trace,
        )

    def _contextual_index_text(self, content: str, metadata: Dict[str, Any]) -> str:
        """Build contextual retrieval text while preserving original display content."""
        return self.index_builder.build(content, metadata)

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

    # ========================================================================
    # 混合检索：语义 + 词法融合与重排 / Hybrid retrieval: fusion + rerank
    # ========================================================================

    async def _fuse_and_rank(
        self,
        candidates: List[ContextItem],
        query: str,
        top_k: int,
        project_id: str = "",
        storage: Any = None,
        *,
        filter_trace: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """对候选施加（可选）语义融合 + 距离衰减 + 重排，返回 top_k。

        - 纯词法模式（无嵌入后端）：final = lexical × decay，与历史版本逐位一致。
        - 语义模式：词法名次与语义名次按 RRF（或加权）融合 → final = fused × decay；
          再对头部 rerank_top_k 个按纯语义分重排（轻量 rerank，复用已算语义分、不额外建模）。

        Apply optional semantic fusion + distance decay + rerank, return top_k.
        Lexical-only mode is byte-for-byte equivalent to the legacy behavior.
        """
        if not candidates:
            return []

        sem_scores: Optional[List[float]] = None
        semantic_used = False
        if self._semantic_enabled():
            try:
                sem_scores = await self._semantic_scores(query, candidates, project_id, storage)
                semantic_used = True
                self._semantic_degraded = False
            except Exception as exc:
                # Phase 7 降级可见：仅首次降级 WARNING（避免每次检索刷屏），并置可查状态标志。
                if not self._semantic_degraded:
                    logger.warning("语义检索降级为纯词法（缺 fastembed/模型或嵌入失败）：%s", exc)
                    self._semantic_degraded = True
                sem_scores = None

        if sem_scores is not None:
            fused = self._fuse_scores(candidates, sem_scores)
            for idx, item in enumerate(candidates):
                item.relevance_score = fused[idx] * float(item.metadata.pop("_decay", 1.0))
                item.metadata["_sem"] = sem_scores[idx]  # 暂存语义分供 rerank
        else:
            # 词法路径（原生纯词法，或语义打分失败后的降级）：施加衰减并丢弃零分项，
            # 与历史"词法零分即丢弃"的行为保持一致（语义启用时被暂留的零分候选在此剔除）。
            kept: List[ContextItem] = []
            for item in candidates:
                item.relevance_score = float(item.relevance_score or 0.0) * float(item.metadata.pop("_decay", 1.0))
                if item.relevance_score > 0:
                    kept.append(item)
            candidates = kept

        if not candidates:
            self._set_ranking_trace({
                "query": query,
                "candidate_count": 0,
                "returned": 0,
                "semantic_enabled": self._semantic_enabled(),
                "semantic_used": semantic_used,
                "semantic_degraded": self._semantic_degraded,
                "fusion": self._fusion,
                "rerank": False,
                "reranker_requested": self._rerank_requested,
                "reranker_degraded": self._reranker_degraded,
                "filters": dict(filter_trace or {}),
            })
            return []

        candidates.sort(key=lambda it: float(it.relevance_score or 0.0), reverse=True)

        rerank_applied = False
        if self._rerank_requested and self._rerank_top_k > 1:
            if self.reranker is None:
                self._reranker_degraded = True
            else:
                head = candidates[: self._rerank_top_k]
                documents = [str(item.metadata.get("_index_text") or item.content or "") for item in head]
                try:
                    rerank_scores = await self.reranker.rerank(query, documents)
                    if len(rerank_scores) != len(head):
                        raise ValueError("reranker backend returned mismatched score count")
                    for item, score in zip(head, rerank_scores):
                        item.metadata["_rerank"] = float(score)
                    head.sort(key=lambda item: float(item.metadata.get("_rerank", 0.0)), reverse=True)
                    candidates = head + candidates[self._rerank_top_k :]
                    rerank_applied = True
                    self._reranker_degraded = False
                except Exception as exc:
                    if not self._reranker_degraded:
                        logger.warning("Cross-encoder reranker degraded; preserving fused ranking: %s", exc)
                    self._reranker_degraded = True

        returned = candidates[:top_k]
        self._set_ranking_trace(self._build_ranking_trace(
            query=query,
            candidates=candidates,
            returned=returned,
            semantic_used=semantic_used,
            rerank_applied=rerank_applied,
            reranker_degraded=self._reranker_degraded,
            filter_trace=filter_trace,
        ))

        for item in candidates:
            item.metadata.pop("_sem", None)  # 清理临时元数据
            item.metadata.pop("_index_text", None)
            item.metadata.pop("_lex", None)
            item.metadata.pop("_rerank", None)

        return returned

    def _build_ranking_trace(
        self,
        *,
        query: str,
        candidates: List[ContextItem],
        returned: List[ContextItem],
        semantic_used: bool,
        rerank_applied: bool,
        reranker_degraded: bool,
        filter_trace: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a compact JSON-safe trace of the last ranking decision."""
        return self.trace_renderer.render(
            query=query,
            candidates=candidates,
            returned=returned,
            fusion=self._fusion,
            semantic_enabled=self._semantic_enabled(),
            semantic_used=semantic_used,
            semantic_degraded=self._semantic_degraded,
            reranker_requested=self._rerank_requested,
            rerank_applied=rerank_applied,
            reranker_degraded=reranker_degraded,
            filters=filter_trace,
        )

    async def _semantic_scores(
        self, query: str, candidates: List[ContextItem], project_id: str = "", storage: Any = None
    ) -> List[float]:
        """返回与 candidates 等长的 query-候选 cosine 相似度列表（embed-once）。

        候选向量按文本内容哈希缓存在 per-project VectorStore（落盘 embeddings_cache.jsonl）：
        只对**未缓存**的文本调用一次嵌入，query 每次都嵌入。跨查询复用，避免重复嵌入全库。
        Candidate vectors are cached by content hash and persisted; only cache-miss
        texts are embedded (plus the query each call). This wires VectorStore into the
        live path so each fact/card/chunk is embedded exactly once.
        """
        if self.vector_index is None:
            return [0.0 for _ in candidates]
        return await self.vector_index.scores(query, candidates, project_id=project_id, storage=storage)

    def _fuse_scores(self, candidates: List[ContextItem], sem_scores: List[float]) -> Dict[int, float]:
        """融合词法名次与语义名次，返回 ``{idx: fused_score}``。"""
        return self.score_fusion.fuse(candidates, sem_scores)

    @staticmethod
    def _ranks(scores: List[float]) -> Dict[int, int]:
        """返回每个下标的名次（1 = 最高分）；并列按下标稳定。"""
        return ScoreFusion.ranks(scores)

    @staticmethod
    def _normalize(scores: List[float]) -> List[float]:
        """min-max 归一到 [0, 1]；全相等则全 0。"""
        return ScoreFusion.normalize(scores)
