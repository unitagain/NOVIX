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
import hashlib
import math
from .models import ContextItem, ContextPriority, ContextType
from .text_tokenizer import calculate_overlap_score, calculate_bm25_score, build_idf_table
from .embeddings import cosine_similarity
from .vector_store import VectorStore
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

    def __init__(self, embeddings_service=None):
        """
        初始化上下文选择引擎 / Initialize the context selection engine.

        Args:
            embeddings_service: 可选的嵌入服务 / Optional embeddings service for semantic similarity.
                需实现 ``async embed(texts) -> List[List[float]]``（见 context_engine.embeddings）。
                为 None 时退化为纯词法（BM25 + 词重叠），行为与历史版本一致。
        """
        self.embeddings = embeddings_service
        self._distance_alpha: float = float(config.get("context_budget", {}).get("fact_distance_alpha", 0.3))
        retrieval_cfg = config.get("retrieval", {}) or {}
        hybrid_cfg = retrieval_cfg.get("hybrid", {}) or {}
        self._fusion: str = str(hybrid_cfg.get("fusion", "rrf")).lower()
        self._bm25_weight: float = float(hybrid_cfg.get("bm25_weight", 0.5))
        self._vector_weight: float = float(hybrid_cfg.get("vector_weight", 0.5))
        self._semantic_rerank: bool = bool(retrieval_cfg.get("semantic_rerank", True))
        self._rerank_top_k: int = int(retrieval_cfg.get("rerank_top_k", 16))
        # 内容寻址的语义向量缓存（embed-once）：每个项目一个 VectorStore，按文本 sha1 存向量，
        # 落 canon/embeddings_cache.jsonl 跨查询复用，避免每次检索重嵌入全部候选。
        # Content-addressed embedding cache (embed-once), one VectorStore per project,
        # persisted to disk so each fact/card/chunk is embedded once and reused.
        self._vec_stores: Dict[str, VectorStore] = {}
        self._vec_paths: Dict[str, Any] = {}
        # Phase 7 降级可见：语义检索是否已降级为纯词法（首次降级时 WARNING 一次并置此标志，供运行时查询）。
        self._semantic_degraded: bool = False

    def _semantic_enabled(self) -> bool:
        """是否启用语义打分（注入了嵌入后端即启用）。"""
        return self.embeddings is not None

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
        # 语义启用时不按"词法零分"丢弃候选——让语义召回有机会救回字面无重叠但语义相关的条目。
        # When semantic is on, do NOT drop lexical-zero candidates: let embeddings rescue them.
        semantic = self._semantic_enabled()

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
                if s <= 0 and not semantic:
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
                if s <= 0 and not semantic:
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
                    context_prefix = str(getattr(fact, "context_prefix", "") or "").strip()
                    status = str(getattr(fact, "status", "confirmed") or "confirmed")
                except Exception:
                    continue
                if not statement:
                    continue
                # Contextual Retrieval：用「情境前缀 + 事实」作为检索索引文本（词法+语义都打它），
                # 但展示/返回仍是原始 statement。前缀缺省时退化为纯 statement，行为不变。
                index_text = f"{context_prefix} {statement}".strip() if context_prefix else statement
                s = score_text(index_text)
                if s <= 0 and not semantic:
                    continue
                # 距离衰减：近期事实优先，远期事实降权但不归零。
                # 衰减延后到融合之后统一施加（存入 _decay），以免污染语义/词法的排名融合。
                # Distance decay is deferred (stored as _decay) and applied after fusion,
                # so it doesn't distort the lexical/semantic rank fusion.
                decay = self._calculate_distance_decay(current_chapter, introduced_in)
                # Phase 14 / 自审：needs_review（AI 待确认）事实降权，confirmed 优先——
                # 避免未经作者确认的抽取事实污染检索 / 写作（"不污染主 canon" 的检索层落地）。
                if status == "needs_review":
                    decay *= 0.6
                fact_meta: Dict[str, Any] = {"introduced_in": introduced_in, "_decay": decay}
                if context_prefix:
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
                if s <= 0 and not semantic:
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

        return await self._fuse_and_rank(candidates, query, top_k, project_id, storage)

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
        self, candidates: List[ContextItem], query: str, top_k: int, project_id: str = "", storage: Any = None
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
        if self._semantic_enabled():
            try:
                sem_scores = await self._semantic_scores(query, candidates, project_id, storage)
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
            return []

        candidates.sort(key=lambda it: float(it.relevance_score or 0.0), reverse=True)

        # 轻量 rerank：仅语义模式下，对头部按纯语义分重排。
        if sem_scores is not None and self._semantic_rerank and self._rerank_top_k > 1:
            head = candidates[: self._rerank_top_k]
            head.sort(key=lambda it: float(it.metadata.get("_sem", 0.0)), reverse=True)
            candidates = head + candidates[self._rerank_top_k :]

        for item in candidates:
            item.metadata.pop("_sem", None)  # 清理临时元数据
            item.metadata.pop("_index_text", None)

        return candidates[:top_k]

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
        texts = [str(it.metadata.get("_index_text") or getattr(it, "content", "") or "") for it in candidates]
        store = self._get_vector_store(project_id, storage)
        hashes = [self._text_hash(t) for t in texts]

        # 收集缓存未命中的文本（去重）/ collect cache-miss texts (deduped)
        misses: Dict[str, str] = {}
        for h, t in zip(hashes, texts):
            if t and not store.has(h):
                misses.setdefault(h, t)

        miss_items = list(misses.items())  # [(hash, text)]
        to_embed = [query] + [t for _, t in miss_items]
        vectors = await self.embeddings.embed(to_embed)
        if not vectors or len(vectors) != len(to_embed):
            raise ValueError("embeddings backend returned mismatched vector count")

        query_vec = vectors[0]
        for (h, t), vec in zip(miss_items, vectors[1:]):
            store.upsert(h, vec, text="")  # 不存正文，省空间；命中靠哈希
        if miss_items:
            self._persist_vector_store(project_id)

        scores: List[float] = []
        for h, t in zip(hashes, texts):
            if not t:
                scores.append(0.0)
                continue
            cached = store.get(h)
            scores.append(cosine_similarity(query_vec, cached["vector"]) if cached else 0.0)
        return scores

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _get_vector_store(self, project_id: str, storage: Any) -> VectorStore:
        """惰性获取 per-project 向量缓存；首次从磁盘加载（若 storage 提供路径）。"""
        key = project_id or "_default"
        store = self._vec_stores.get(key)
        if store is not None:
            return store
        path = None
        getter = getattr(storage, "get_embeddings_cache_path", None)
        if getter is not None and project_id:
            try:
                path = getter(project_id)
            except Exception:
                path = None
        store = VectorStore.load(path) if path else VectorStore()
        self._vec_stores[key] = store
        if path:
            self._vec_paths[key] = path
        return store

    def _persist_vector_store(self, project_id: str) -> None:
        """把更新后的向量缓存落盘（无路径则跳过，纯内存复用）。"""
        key = project_id or "_default"
        path = self._vec_paths.get(key)
        store = self._vec_stores.get(key)
        if not path or store is None:
            return
        try:
            store.save(path)
        except Exception as exc:
            logger.warning("Failed to persist embedding cache (%s): %s", path, exc)

    def _fuse_scores(self, candidates: List[ContextItem], sem_scores: List[float]) -> Dict[int, float]:
        """融合词法名次与语义名次，返回 ``{idx: fused_score}``。"""
        n = len(candidates)
        lex = [float(it.relevance_score or 0.0) for it in candidates]
        if self._fusion == "weighted":
            lw = self._normalize(lex)
            sw = self._normalize(sem_scores)
            return {i: self._bm25_weight * lw[i] + self._vector_weight * sw[i] for i in range(n)}
        # 默认 RRF：对名次（而非原始分）求 1/(k+rank) 之和，量纲无关、稳健。
        lex_rank = self._ranks(lex)
        sem_rank = self._ranks(sem_scores)
        return {i: 1.0 / (_RRF_K + lex_rank[i]) + 1.0 / (_RRF_K + sem_rank[i]) for i in range(n)}

    @staticmethod
    def _ranks(scores: List[float]) -> Dict[int, int]:
        """返回每个下标的名次（1 = 最高分）；并列按下标稳定。"""
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return {idx: position + 1 for position, idx in enumerate(order)}

    @staticmethod
    def _normalize(scores: List[float]) -> List[float]:
        """min-max 归一到 [0, 1]；全相等则全 0。"""
        if not scores:
            return []
        lo, hi = min(scores), max(scores)
        if hi <= lo:
            return [0.0 for _ in scores]
        span = hi - lo
        return [(s - lo) / span for s in scores]
