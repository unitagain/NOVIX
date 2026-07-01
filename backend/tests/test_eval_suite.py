# -*- coding: utf-8 -*-
"""Phase 15 · 评测基线套件回归测试。

验证固定案例的检索召回有基线、关系命中 + 冲突检出确定性、一键 suite 输出结构完整。
确定性、无网络 / 无 key。
"""

import asyncio

from app.eval.eval_suite import run_eval_suite, run_retrieval_eval, run_relation_eval


def test_retrieval_eval_has_baseline():
    r = asyncio.run(run_retrieval_eval())
    assert r["num_cases"] == 5
    assert r["recall"] > 0  # 词法检索基线应有召回
    assert 0.0 <= r["hit_rate"] <= 1.0


def test_relation_eval_detects_conflict():
    r = run_relation_eval()
    assert r["relation_hit"]  # 王五 → 青云门 命中
    assert r["conflict_detection_ok"]  # 张三↔李四 师徒 vs 敌对（无 change）被检出


def test_eval_suite_one_shot_baseline():
    s = asyncio.run(run_eval_suite())
    assert "retrieval" in s and "relation" in s and "metrics" in s
    assert s["retrieval"]["num_cases"] == 5
    assert "json" in s["metrics"] and "cache" in s["metrics"]


def test_eval_suite_repeatable():
    """确定性：两次跑检索/关系结果一致（可回归）。"""
    a = asyncio.run(run_eval_suite())
    b = asyncio.run(run_eval_suite())
    assert a["retrieval"]["recall"] == b["retrieval"]["recall"]
    assert a["relation"]["conflicts_detected"] == b["relation"]["conflicts_detected"]
