# -*- coding: utf-8 -*-
"""P6 golden replay gate tests."""

import asyncio

from app.eval.golden_replay import run_golden_replay_suite


def test_golden_replay_suite_passes_default_gate():
    result = asyncio.run(run_golden_replay_suite())
    assert result["success"] is True
    assert result["num_cases"] >= 30
    assert not result["failures"]
    assert result["aggregate"]["checks"]["case_count_ok"] is True
    assert result["aggregate"]["checks"]["retrieval_recall_ok"] is True


def test_golden_replay_suite_reports_threshold_failure():
    result = asyncio.run(run_golden_replay_suite({"fallback_rate_max": 0.0}))
    assert result["success"] is False
    assert "fallback_rate_ok" in result["aggregate_failures"]
