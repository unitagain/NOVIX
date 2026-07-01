# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 15 · 评测基线套件（支柱8）。
  固定案例集 + 一键跑出基线数字：检索召回（recall@k / hit_rate）+ 关系命中 + 冲突检出
  + 当前运行指标快照（JSON 成功率 / 缓存命中率）。确定性、可重复、无网络 / 无 key——
  关键 Phase 前后可对比回归，给"更准、更稳、更省"一个可量化的基线。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas.canon import Fact
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.relation_graph import RelationGraph, Relation
from app.eval.retrieval_eval import evaluate_retrieval_recall
from app.utils.json_metrics import json_metrics_snapshot
from app.utils.cache_metrics import cache_metrics_snapshot

# 固定检索案例集（人物关系 / 世界规则 / 事件 / 物件来历）。
_EVAL_FACTS: List[Fact] = [
    Fact(id="F1", statement="张三是李四的授业恩师", source="V1C001", introduced_in="V1C001"),
    Fact(id="F2", statement="迷雾森林在夜晚会让人迷失方向", source="V1C002", introduced_in="V1C002"),
    Fact(id="F3", statement="王五背叛了青云门投靠魔教", source="V1C003", introduced_in="V1C003"),
    Fact(id="F4", statement="主角佩戴的玉佩是母亲的遗物", source="V1C004", introduced_in="V1C004"),
    Fact(id="F5", statement="青云门掌门身患重病时日无多", source="V1C005", introduced_in="V1C005"),
]
_RETRIEVAL_CASES: List[Dict[str, Any]] = [
    {"query": "张三和李四是什么关系", "expect": ["F1"]},
    {"query": "迷雾森林的规则", "expect": ["F2"]},
    {"query": "谁背叛了青云门", "expect": ["F3"]},
    {"query": "玉佩的来历", "expect": ["F4"]},
    {"query": "掌门的身体状况", "expect": ["F5"]},
]

# 固定关系 / 冲突案例集。张三↔李四 师徒 vs 敌对（无 change 标注）应被一致性护栏检出。
_EVAL_RELATIONS: List[Relation] = [
    Relation("张三", "师徒", "李四", "", "V1C001"),
    Relation("王五", "背叛", "青云门", "", "V1C003"),
    Relation("张三", "敌对", "李四", "", "V1C008"),
]


class _EvalFactStorage:
    """只读固定 facts 的评测存储（检索 item_types=['fact'] 仅需 get_all_facts）。"""

    async def get_all_facts(self, project_id: str) -> List[Fact]:
        return list(_EVAL_FACTS)


async def run_retrieval_eval(top_k: int = 5) -> Dict[str, Any]:
    """固定案例的词法检索召回（无 embedding，确定性可回归）。"""
    engine = ContextSelectEngine()  # 纯词法（embeddings_service=None）
    return await evaluate_retrieval_recall(
        engine, _EvalFactStorage(), _RETRIEVAL_CASES, item_types=["fact"], top_k=top_k
    )


def run_relation_eval() -> Dict[str, Any]:
    """固定案例的关系命中 + 冲突检出（确定性，无 LLM）。"""
    graph = RelationGraph(_EVAL_RELATIONS)
    neighbors = graph.neighbors("王五")
    relation_hit = any("青云门" in (r.subject, r.object) for r in neighbors)
    conflicts = graph.inconsistencies()
    return {
        "relation_hit": relation_hit,
        "conflicts_detected": len(conflicts),
        "expected_conflicts": 1,
        "conflict_detection_ok": len(conflicts) >= 1,
    }


async def run_eval_suite(top_k: int = 5) -> Dict[str, Any]:
    """Phase 15 · 一键跑出评测基线：检索召回 + 关系/冲突检出 + 当前运行指标快照。"""
    retrieval = await run_retrieval_eval(top_k=top_k)
    relation = run_relation_eval()
    return {
        "retrieval": {
            "recall": retrieval["recall"],
            "hit_rate": retrieval["hit_rate"],
            "num_cases": retrieval["num_cases"],
            "top_k": retrieval["top_k"],
        },
        "relation": relation,
        "metrics": {"json": json_metrics_snapshot(), "cache": cache_metrics_snapshot()},
    }
