# -*- coding: utf-8 -*-
"""Phase 15 · 评测基线套件回归测试。

验证固定案例的检索召回有基线、关系命中 + 冲突检出确定性、一键 suite 输出结构完整。
确定性、无网络 / 无 key。
"""

import asyncio

from app.eval.eval_suite import (
    run_compact_eval,
    run_consistency_eval,
    run_eval_suite,
    run_memory_governance_eval,
    run_memory_eval,
    run_p8_context_boundary_eval,
    run_retrieval_eval,
    run_retrieval_quality_eval,
    run_relation_eval,
    run_security_eval,
    run_trace_replay_eval,
)


def test_retrieval_eval_has_baseline():
    r = asyncio.run(run_retrieval_eval())
    assert r["num_cases"] == 5
    assert r["recall"] > 0  # 词法检索基线应有召回
    assert 0.0 <= r["hit_rate"] <= 1.0


def test_relation_eval_detects_conflict():
    r = run_relation_eval()
    assert r["relation_hit"]  # 王五 → 青云门 命中
    assert r["conflict_detection_ok"]  # 张三↔李四 师徒 vs 敌对（无 change）被检出


def test_retrieval_quality_eval_has_contextual_prefix_and_trace():
    r = asyncio.run(run_retrieval_quality_eval())
    assert r["contextual_prefix_hit"] is True
    assert r["ranking_trace_available"] is True
    assert r["signals"]["bm25"] is True


def test_memory_eval_has_baseline():
    r = asyncio.run(run_memory_eval())
    assert r["num_cases"] == 2
    assert r["recall"] == 1.0


def test_memory_governance_eval_has_activation_and_backlog_metrics():
    r = asyncio.run(run_memory_governance_eval())
    assert r["success"] is True
    assert r["num_cases"] >= 6


def test_compact_eval_retains_key_context():
    r = asyncio.run(run_compact_eval())
    assert r["compacted"] is True
    assert r["after"] < r["before"]
    assert r["key_retained"] is True
    assert r["recent_retained"] is True


def test_consistency_eval_detects_conflict():
    r = run_consistency_eval()
    assert r["relation_conflict_detected"] is True
    assert r["conflicts_detected"] >= r["expected_conflicts"]


def test_security_eval_blocks_untrusted_long_term_writes():
    r = asyncio.run(run_security_eval())
    assert r["success"] is True
    assert r["num_cases"] >= 4


def test_p8_context_boundary_eval_has_full_boundary_cases():
    r = run_p8_context_boundary_eval()
    assert r["success"] is True
    assert r["num_cases"] >= 8


def test_trace_replay_eval_has_cost_and_gate():
    r = run_trace_replay_eval()
    assert r["success"] is True
    assert r["summary"]["tokens"]["total_observed"] > 0
    assert r["summary"]["latency_ms"]["observed"] > 0


def test_eval_suite_one_shot_baseline():
    s = asyncio.run(run_eval_suite())
    assert "retrieval" in s and "relation" in s and "metrics" in s
    assert "retrieval_quality" in s and "memory" in s and "compact" in s and "consistency" in s
    assert "memory_governance" in s and "security" in s and "p8_context_boundary" in s and "trace_replay" in s
    assert s["retrieval"]["num_cases"] == 5
    assert s["retrieval_quality"]["contextual_prefix_hit"] is True
    assert s["memory"]["num_cases"] == 2
    assert s["memory_governance"]["success"] is True
    assert s["compact"]["key_retained"] is True
    assert s["consistency"]["relation_conflict_detected"] is True
    assert s["security"]["success"] is True
    assert s["p8_context_boundary"]["success"] is True
    assert s["trace_replay"]["success"] is True
    assert "json" in s["metrics"] and "cache" in s["metrics"]


def test_eval_suite_repeatable():
    """确定性：两次跑检索/关系结果一致（可回归）。"""
    a = asyncio.run(run_eval_suite())
    b = asyncio.run(run_eval_suite())
    assert a["retrieval"]["recall"] == b["retrieval"]["recall"]
    assert a["retrieval_quality"]["top_ids"] == b["retrieval_quality"]["top_ids"]
    assert a["relation"]["conflicts_detected"] == b["relation"]["conflicts_detected"]
    assert a["memory"]["recall"] == b["memory"]["recall"]
    assert a["compact"]["key_retained"] == b["compact"]["key_retained"]
    assert a["security"]["success"] == b["security"]["success"]
    assert a["p8_context_boundary"]["success"] == b["p8_context_boundary"]["success"]
