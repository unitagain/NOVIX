# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  轻量向量库（Phase 4）—— 内存矩阵 + 文件持久化 + 暴力 cosine top-k 检索。
  小说规模（数百~数千条事实）无需 faiss/chroma 等重型向量库；纯本地、零额外服务、Git 友好。
  Lightweight vector store: in-memory + jsonl persistence + brute-force cosine top-k.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.context_engine.embeddings import cosine_similarity
from app.utils.logger import get_logger

logger = get_logger(__name__)


class VectorStore:
    """以 id 为键存 {vector, text, meta}；持久化为 jsonl。"""

    def __init__(self):
        self._items: Dict[str, Dict] = {}

    def upsert(self, item_id: str, vector: List[float], text: str = "", meta: Optional[Dict] = None) -> None:
        self._items[str(item_id)] = {"vector": list(vector or []), "text": text, "meta": meta or {}}

    def __len__(self) -> int:
        return len(self._items)

    def has(self, item_id: str) -> bool:
        return str(item_id) in self._items

    def ids(self) -> List[str]:
        return list(self._items.keys())

    def get(self, item_id: str) -> Optional[Dict]:
        return self._items.get(str(item_id))

    def search(self, query_vector: List[float], top_k: int = 10) -> List[Tuple[str, float]]:
        """返回 [(id, score)]，按 cosine 降序，top_k。"""
        if not query_vector or not self._items:
            return []
        scored = [(item_id, cosine_similarity(query_vector, it["vector"])) for item_id, it in self._items.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: max(1, top_k)]

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for item_id, it in self._items.items():
                f.write(
                    json.dumps(
                        {"id": item_id, "vector": it["vector"], "text": it["text"], "meta": it["meta"]},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    @classmethod
    def load(cls, path) -> "VectorStore":
        store = cls()
        p = Path(path)
        if not p.exists():
            return store
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    store._items[str(rec.get("id"))] = {
                        "vector": rec.get("vector") or [],
                        "text": rec.get("text") or "",
                        "meta": rec.get("meta") or {},
                    }
        except Exception as exc:
            logger.warning("VectorStore load failed (%s): %s", p, exc)
        return store
