# -*- coding: utf-8 -*-
"""Cache metrics regression tests."""

from app.utils.cache_metrics import record_cache, cache_metrics_snapshot, reset_cache_metrics


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
