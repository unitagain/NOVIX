# -*- coding: utf-8 -*-
"""Cross-encoder reranking backends for the context selection pipeline."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from app.context_engine.embeddings import resolve_model_cache_dir
from app.utils.logger import get_logger

logger = get_logger(__name__)


class RerankerBackend(ABC):
    """Focused interface for query-document cross-encoder scoring."""

    @abstractmethod
    async def rerank(self, query: str, documents: List[str]) -> List[float]:
        """Return one relevance score per document, in input order."""
        raise NotImplementedError


class OnnxCrossEncoderReranker(RerankerBackend):
    """Lazy fastembed cross-encoder reranker running locally through ONNX."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", cache_dir: Optional[str] = None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._unavailable = False

    def _ensure(self):
        if self._unavailable:
            raise RuntimeError("reranker backend unavailable (previous load failed)")
        if self._model is None:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(model_name=self.model_name, cache_dir=self.cache_dir)
            except Exception:
                self._unavailable = True
                raise
        return self._model

    async def rerank(self, query: str, documents: List[str]) -> List[float]:
        if not documents:
            return []
        model = self._ensure()

        def _run() -> List[float]:
            return [float(score) for score in model.rerank(str(query or ""), list(documents))]

        return await asyncio.to_thread(_run)


def create_reranker_backend(config) -> Optional[RerankerBackend]:
    """Create the configured reranker; disabled or unsupported backends return None."""
    cfg = (config.get("retrieval", {}) or {}).get("reranker", {}) or {}
    if not cfg.get("enabled", False):
        return None
    backend = str(cfg.get("backend", "onnx_cross_encoder")).lower()
    if backend == "onnx_cross_encoder":
        return OnnxCrossEncoderReranker(
            model_name=str(cfg.get("model", "BAAI/bge-reranker-base")),
            cache_dir=resolve_model_cache_dir(cfg.get("cache_dir") or "models/reranker"),
        )
    logger.warning("Unsupported reranker backend '%s'; reranking disabled.", backend)
    return None
