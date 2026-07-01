# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  轻量关系图（Phase 4，GraphRAG-lite）—— 从 canon/relations.jsonl 读入"有类型关系三元组"
  （主体 -[关系]→ 客体，可带 change/章节），构建邻接，支持"实体邻居""两实体间关系"查询。
  纯本地、确定性、不调 LLM。三元组由档案员章末提取顺带产出（生产侧见 Phase 4b）。

  设计取向：解决纯向量/词法答不出的"连点成线 / 全局关系"类查询（Microsoft GraphRAG 实证），
  但**不跑微软那套重型管线**——复用现有 canon 资产、增量 append、本地遍历。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


class Relation:
    """一条有类型关系：subject -[relation]-> object（可带 change 演变与 chapter 出处）。"""

    __slots__ = ("subject", "relation", "object", "change", "chapter")

    def __init__(self, subject: str, relation: str, object_: str, change: str = "", chapter: str = ""):
        self.subject = subject
        self.relation = relation
        self.object = object_
        self.change = change
        self.chapter = chapter

    def text(self) -> str:
        base = f"{self.subject} —[{self.relation}]→ {self.object}"
        if self.change:
            base += f"（{self.change}）"
        if self.chapter:
            base += f" @{self.chapter}"
        return base


class RelationGraph:
    """实体-关系图；从 relations.jsonl 构建，提供邻居/两实体间关系查询。"""

    def __init__(self, relations: Optional[List[Relation]] = None):
        self.relations: List[Relation] = list(relations or [])
        self._adj: Dict[str, List[int]] = {}
        for idx, rel in enumerate(self.relations):
            self._adj.setdefault(rel.subject, []).append(idx)
            self._adj.setdefault(rel.object, []).append(idx)

    @classmethod
    def load(cls, path) -> "RelationGraph":
        rels: List[Relation] = []
        p = Path(path) if path else None
        if p and p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        subject = str(d.get("subject") or "").strip()
                        object_ = str(d.get("object") or "").strip()
                        relation = str(d.get("relation") or "").strip()
                        if subject and object_ and relation:
                            rels.append(
                                Relation(
                                    subject,
                                    relation,
                                    object_,
                                    str(d.get("change") or ""),
                                    str(d.get("chapter") or d.get("introduced_in") or ""),
                                )
                            )
            except Exception as exc:
                logger.warning("RelationGraph load failed (%s): %s", p, exc)
        return cls(rels)

    def neighbors(self, entity: str, limit: int = 20) -> List[Relation]:
        return [self.relations[idx] for idx in self._adj.get(entity, [])[: max(1, limit)]]

    def between(self, a: str, b: str, limit: int = 20) -> List[Relation]:
        out: List[Relation] = []
        for rel in self.relations:
            if (rel.subject == a and rel.object == b) or (rel.subject == b and rel.object == a):
                out.append(rel)
                if len(out) >= limit:
                    break
        return out

    def describe(self, entity: Optional[str] = None, other: Optional[str] = None, limit: int = 20) -> str:
        """可读化查询结果，供工具/检索直接喂给 LLM。"""
        if entity and other:
            rels = self.between(entity, other, limit)
            if not rels:
                return f"未在关系图中找到『{entity}』与『{other}』的直接关系。"
            return f"【{entity} 与 {other} 的关系】\n" + "\n".join(f"- {r.text()}" for r in rels)
        if entity:
            rels = self.neighbors(entity, limit)
            if not rels:
                return f"未在关系图中找到与『{entity}』相关的关系。"
            return f"【与 {entity} 相关的关系】\n" + "\n".join(f"- {r.text()}" for r in rels)
        return "（关系图查询需提供至少一个实体）"

    def inconsistencies(self) -> List[Dict[str, object]]:
        """确定性关系一致性护栏（Phase 6，无 LLM）。

        检测同一对实体（无向）在不同章节出现**两种及以上不同关系类型**、且**没有任何一条带
        `change` 演变标注**的情况——这通常意味着"人物关系被悄悄改写而未交代转折"，是长篇
        一致性的常见破绽。带 change 标注（如"盟友→敌对"）视为作者有意演变，不报警。

        Deterministic relation-consistency guardrail: flags entity pairs whose relation
        type silently changes across chapters with no `change` note. Returns a list of
        ``{entities, relations, note}`` dicts (empty when consistent).
        """
        pairs: Dict[frozenset, List[Relation]] = {}
        for rel in self.relations:
            if rel.subject == rel.object:
                continue
            pairs.setdefault(frozenset((rel.subject, rel.object)), []).append(rel)

        out: List[Dict[str, object]] = []
        for key, rels in pairs.items():
            types = {r.relation for r in rels}
            if len(types) < 2:
                continue  # 单一关系类型，一致
            if any(str(r.change or "").strip() for r in rels):
                continue  # 已标注演变 → 视为有意为之，不报警
            out.append(
                {
                    "entities": sorted(key),
                    "relations": [r.text() for r in rels],
                    "note": "关系类型在不同章节出现冲突且无演变（change）标注",
                }
            )
        return out
