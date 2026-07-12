"""Stable benchmark policy models."""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RetrievalStrategySpec:
    name: str
    mode: str = "ranked"
    semantic: bool = False
    rerank: bool = False
    top_k: int = 5


RETRIEVAL_STRATEGIES: Dict[str, RetrievalStrategySpec] = {
    "full_stuffing": RetrievalStrategySpec("full_stuffing", mode="full_stuffing", top_k=0),
    "bm25": RetrievalStrategySpec("bm25"),
    "lexical": RetrievalStrategySpec("lexical"),
    "minimal": RetrievalStrategySpec("minimal", top_k=3),
    "hybrid": RetrievalStrategySpec("hybrid", semantic=True),
    "hybrid_rerank": RetrievalStrategySpec("hybrid_rerank", semantic=True, rerank=True),
    "jit_hybrid": RetrievalStrategySpec("jit_hybrid", semantic=True),
}
