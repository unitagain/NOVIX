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

  本模块是关系遍历的唯一 owner。除 Canon 抽取关系外，作者在关系图谱中手绘的设定边
  （U4，cards/relations.yaml）也经 ``Relation.from_card_edge`` 汇入同一张图，
  由调用方（WriterToolset._query_relations）合并——不新建第二条检索通道。

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
    """一条有类型关系：subject -[relation]-> object（可带 change 演变与 chapter 出处）。

    两个来源共用本结构：Canon 抽取关系（带 chapter 出处）与作者在关系图谱中手绘的
    设定关系（U4，无出处、可带 appellation 称呼）。来源差异由字段自然体现，
    渲染后模型可自行区分「已发生事实」与「作者设定」。
    """

    __slots__ = ("subject", "relation", "object", "change", "chapter", "appellation", "reverse_appellation")

    def __init__(
        self,
        subject: str,
        relation: str,
        object_: str,
        change: str = "",
        chapter: str = "",
        appellation: str = "",
        reverse_appellation: str = "",
    ):
        self.subject = subject
        self.relation = relation
        self.object = object_
        self.change = change
        self.chapter = chapter
        self.appellation = appellation
        self.reverse_appellation = reverse_appellation

    @classmethod
    def from_card_edge(cls, data: Dict[str, object]) -> "Relation":
        """由卡片层设定边构造关系（U4）。

        方向语义：``from`` 是 ``to`` 的 ``relation``，因此 subject=from、object=to；
        ``appellation`` 是 object 对 subject 的称呼，``reverse_appellation`` 是 subject
        对 object 的称呼。设定边没有章节出处（chapter 留空），因此不参与「未来章节」时间过滤。
        """
        return cls(
            str(data.get("from") or "").strip(),
            str(data.get("relation") or "").strip(),
            str(data.get("to") or "").strip(),
            "",
            "",
            str(data.get("appellation") or "").strip(),
            str(data.get("reverse_appellation") or "").strip(),
        )

    def text(self) -> str:
        base = f"{self.subject} —[{self.relation}]→ {self.object}"
        if self.change:
            base += f"（{self.change}）"
        if self.chapter:
            base += f" @{self.chapter}"
        # 双向称呼各自独立输出，模型据此直接决定对白里怎么称呼对方。
        forms = []
        if self.appellation:
            forms.append(f"{self.object}称{self.subject}「{self.appellation}」")
        if self.reverse_appellation:
            forms.append(f"{self.subject}称{self.object}「{self.reverse_appellation}」")
        if forms:
            base += f"（{'；'.join(forms)}）"
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
