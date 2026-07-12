# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  检索评测（Phase 6，支柱8）—— 确定性、可重复的检索召回评测，给"召回率"一个可回归的基线。
  给定 (query, 期望命中的 fact_id) 用例集，跑 ContextSelectEngine 检索，算 recall@k。
  纯本地、无 LLM、无网络；可对比纯词法 vs 语义混合（注入不同 embeddings 后端）。
  Deterministic, repeatable retrieval-recall evaluation for regression baselines.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List


def _distribution(values: List[float]) -> Dict[str, float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def nearest_rank(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction) + 0.999999) - 1))
        return ordered[index]

    return {
        "mean": sum(ordered) / len(ordered),
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "max": ordered[-1],
    }


async def evaluate_retrieval_recall(
    select_engine,
    storage,
    cases: List[Dict[str, Any]],
    *,
    project_id: str = "eval",
    item_types: List[str] | None = None,
    top_k: int = 5,
    total_chapters: int = 0,
) -> Dict[str, Any]:
    """评测检索召回。

    Args:
        select_engine: ContextSelectEngine 实例（可注入/不注入 embeddings 对比词法 vs 语义）。
        storage: 提供 get_all_facts 等的存储/适配器。
        cases: 用例列表，每项 ``{"query": str, "expect": [fact_id, ...]}``。
        top_k: 每次检索取前 k 条。

    Returns:
        ``{"recall": 0..1, "hit_rate": 0..1, "cases": [...每例明细...], "top_k": k}``
        - recall：期望命中事实的整体召回（micro，按命中事实数 / 期望事实总数）。
        - hit_rate：至少命中一条期望的用例占比。
    """
    item_types = item_types or ["fact"]
    matched_total = 0
    expected_total = 0
    cases_hit = 0
    details: List[Dict[str, Any]] = []
    latencies_ms: List[float] = []
    selected_chars: List[float] = []
    semantic_used_cases = 0
    semantic_degraded_cases = 0
    reranker_used_cases = 0
    reranker_degraded_cases = 0

    for case in cases or []:
        query = str(case.get("query") or "").strip()
        expected = {str(x) for x in (case.get("expect") or []) if str(x).strip()}
        started = time.perf_counter()
        items = (
            await select_engine.retrieval_select(
                project_id=project_id,
                query=query,
                item_types=item_types,
                storage=storage,
                top_k=top_k,
                current_chapter=str(case.get("current_chapter") or case.get("chapter_id") or ""),
                total_chapters=int(case.get("total_chapters") or total_chapters or 0),
            )
            or []
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        trace_getter = getattr(select_engine, "get_last_ranking_trace", None)
        ranking_trace = trace_getter() if callable(trace_getter) else {}
        selected_text_chars = sum(len(str(getattr(item, "content", "") or "")) for item in items)
        latencies_ms.append(elapsed_ms)
        selected_chars.append(float(selected_text_chars))
        if ranking_trace.get("semantic_used"):
            semantic_used_cases += 1
        if ranking_trace.get("semantic_degraded"):
            semantic_degraded_cases += 1
        if ranking_trace.get("reranker_used"):
            reranker_used_cases += 1
        if ranking_trace.get("reranker_degraded"):
            reranker_degraded_cases += 1
        got_ids = {str(getattr(it, "id", "")) for it in items}
        matched = expected & got_ids
        matched_total += len(matched)
        expected_total += len(expected)
        if matched:
            cases_hit += 1
        details.append(
            {
                "query": query,
                "expected": sorted(expected),
                "retrieved": [str(getattr(it, "id", "")) for it in items],
                "matched": sorted(matched),
                "recall": (len(matched) / len(expected)) if expected else 0.0,
                "latency_ms": elapsed_ms,
                "selected_chars": selected_text_chars,
                "ranking_trace": ranking_trace,
            }
        )

    n = len(details)
    return {
        "recall": (matched_total / expected_total) if expected_total else 0.0,
        "hit_rate": (cases_hit / n) if n else 0.0,
        "cases": details,
        "top_k": top_k,
        "num_cases": n,
        "latency_ms": _distribution(latencies_ms),
        "selected_chars": _distribution(selected_chars),
        "semantic_used_cases": semantic_used_cases,
        "semantic_degraded_cases": semantic_degraded_cases,
        "reranker_used_cases": reranker_used_cases,
        "reranker_degraded_cases": reranker_degraded_cases,
    }
