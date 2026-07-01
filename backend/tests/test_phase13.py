# -*- coding: utf-8 -*-
"""Phase 13 · 缓存分层 + 预算升级回归测试。

验证：① full_canon_eligible 的「数量 + token」双门槛（少量超长事实也不全注入）
② cache_metrics 命中率统计。无网络 / 无 key。
"""

from app.orchestrator.orchestrator_helpers import full_canon_eligible
from app.utils.cache_metrics import record_cache, cache_metrics_snapshot, reset_cache_metrics

# ---------- 动作② 预算 token 双门槛 ----------


def test_full_canon_eligible_all_within():
    assert full_canon_eligible(50, 20, 5000, max_facts=80, max_cards=30, max_tokens=12000)


def test_full_canon_blocked_by_token_even_if_count_ok():
    # 数量够但 token 超 → 不全注入（Phase 13 新增 token 维度的价值）
    assert not full_canon_eligible(50, 20, 15000, max_facts=80, max_cards=30, max_tokens=12000)


def test_full_canon_blocked_by_count():
    assert not full_canon_eligible(100, 20, 5000, max_facts=80, max_cards=30, max_tokens=12000)


def test_full_canon_blocked_by_cards():
    assert not full_canon_eligible(50, 40, 5000, max_facts=80, max_cards=30, max_tokens=12000)


# ---------- 动作① 缓存命中率指标 ----------


def test_cache_metrics_hit_rate():
    reset_cache_metrics()
    record_cache(read_tokens=800, creation_tokens=100, input_tokens=100)  # 800 命中 / 200 新
    snap = cache_metrics_snapshot()
    assert snap["cache_read_tokens"] == 800
    assert snap["calls"] == 1
    assert snap["hit_rate"] == 0.8  # 800 / (800+100+100)


def test_cache_metrics_accumulates():
    reset_cache_metrics()
    record_cache(500, 0, 500)
    record_cache(500, 0, 500)
    snap = cache_metrics_snapshot()
    assert snap["calls"] == 2
    assert snap["cache_read_tokens"] == 1000
    assert snap["hit_rate"] == 0.5


def test_cache_metrics_empty_is_zero():
    reset_cache_metrics()
    assert cache_metrics_snapshot()["hit_rate"] == 0.0
