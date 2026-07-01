# -*- coding: utf-8 -*-
"""
Phase 6 验收：检索召回评测（确定性、无 LLM、无网络）。
用纯词法 ContextSelectEngine + 内存 fact 夹具跑 recall@k。
"""

import asyncio

from app.context_engine.select_engine import ContextSelectEngine
from app.eval.retrieval_eval import evaluate_retrieval_recall
from app.schemas.canon import Fact


class _FakeFactStorage:
    def __init__(self, facts):
        self._facts = facts

    async def get_all_facts(self, project_id):
        return list(self._facts)


def _fixture():
    return [
        Fact(id="F1", statement="迷雾森林中潜伏着一份古老的契约", source="V1C001", introduced_in="V1C001"),
        Fact(id="F2", statement="张三的佩剑名为霜锋，削铁如泥", source="V1C002", introduced_in="V1C002"),
        Fact(id="F3", statement="李四惧怕火焰，源于幼年的火灾", source="V1C003", introduced_in="V1C003"),
    ]


def test_recall_perfect_on_lexical_cases():
    engine = ContextSelectEngine()  # 纯词法
    storage = _FakeFactStorage(_fixture())
    cases = [
        {"query": "迷雾森林", "expect": ["F1"]},
        {"query": "霜锋", "expect": ["F2"]},
        {"query": "火焰", "expect": ["F3"]},
    ]
    result = asyncio.run(evaluate_retrieval_recall(engine, storage, cases, top_k=5))
    assert result["num_cases"] == 3
    assert result["recall"] == 1.0  # 字面命中全召回
    assert result["hit_rate"] == 1.0


def test_recall_partial_and_structure():
    engine = ContextSelectEngine()
    storage = _FakeFactStorage(_fixture())
    cases = [
        {"query": "霜锋", "expect": ["F2"]},  # 命中
        {"query": "完全无关的查询词xyz", "expect": ["F1"]},  # 不命中
    ]
    result = asyncio.run(evaluate_retrieval_recall(engine, storage, cases, top_k=5))
    assert result["recall"] == 0.5  # 2 期望中命中 1
    assert result["hit_rate"] == 0.5
    assert result["cases"][0]["matched"] == ["F2"]
    assert result["cases"][1]["matched"] == []


def test_empty_cases():
    engine = ContextSelectEngine()
    storage = _FakeFactStorage(_fixture())
    result = asyncio.run(evaluate_retrieval_recall(engine, storage, [], top_k=5))
    assert result["recall"] == 0.0 and result["num_cases"] == 0
