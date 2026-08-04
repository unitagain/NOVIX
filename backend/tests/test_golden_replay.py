# -*- coding: utf-8 -*-
"""P6 golden replay gate tests."""

import asyncio

import app.eval.golden_replay as golden_replay
from app.eval.golden_replay import run_golden_replay_suite
from app.eval.representative_scenarios import SCENARIO_MANIFESTS


def test_golden_replay_suite_passes_default_gate():
    result = asyncio.run(run_golden_replay_suite())
    assert result["success"] is True
    assert result["num_cases"] >= 30
    assert not result["failures"]
    assert result["aggregate"]["checks"]["case_count_ok"] is True
    assert result["aggregate"]["checks"]["retrieval_recall_ok"] is True


def test_golden_replay_suite_reports_threshold_failure():
    result = asyncio.run(run_golden_replay_suite({"retrieval_recall_min": 1.1}))
    assert result["success"] is False
    assert "retrieval_recall_ok" in result["aggregate_failures"]


def test_t0_representative_manifest_is_bounded_and_complete():
    assert 8 <= len(SCENARIO_MANIFESTS) <= 12
    assert {manifest.layer for manifest in SCENARIO_MANIFESTS} == {"deterministic", "provider_optional"}
    assert {manifest.edit_target for manifest in SCENARIO_MANIFESTS} >= {"head", "middle", "tail"}
    for manifest in SCENARIO_MANIFESTS:
        assert manifest.required_sources
        assert manifest.allowed_terminal_states
        assert manifest.permission_boundary


def test_t0_baseline_classifies_gate_candidates_and_evidence_gaps():
    result = asyncio.run(run_golden_replay_suite())
    baseline = result["evidence_baseline"]
    decisions = {item["metric"]: item["decision"] for item in baseline["metric_decisions"]}

    assert baseline["classification"] == "content_free_deterministic_baseline"
    assert baseline["scenario_count"] == len(SCENARIO_MANIFESTS)
    assert decisions["critical_source_coverage"] == "hard_gate_candidate"
    assert decisions["source_overfetch"] == "insufficient_evidence"
    assert decisions["token_latency"] == "diagnostic_only"
    assert decisions["provider_degradation"] == "engineering_gate"
    assert baseline["resolved_evidence_gaps"] == [
        "provider_adapter_conformance",
    ]
    assert "provider_adapter_conformance" not in baseline["evidence_gaps"]
    assert baseline["diagnostics"]["edit_target_outcome"] == {
        "head": {"changed": True},
        "middle": {"changed": True},
        "tail": {"changed": True},
    }
    assert baseline["policy_decisions"]["context"] == {
        "schema_version": 1,
        "status": "closed_no_change",
        "default_strategy": "jit_retrieval",
        "story_source_catalog_enabled": False,
        "stable_push_layer_enabled": False,
        "applied": False,
        "reasons": [
            "critical_source_coverage_has_no_observed_gap",
            "representative_source_overfetch_corpus_missing",
            "production_token_latency_samples_missing",
        ],
    }
    assert baseline["policy_decisions"]["memory"]["status"] == "insufficient_evidence"
    assert baseline["policy_decisions"]["memory"]["semantic_rrf_enabled"] is False
    assert baseline["policy_decisions"]["memory"]["default_enabled"] is False


def test_t0_baseline_decision_is_stable_and_content_free():
    first = asyncio.run(run_golden_replay_suite())
    second = asyncio.run(run_golden_replay_suite())

    assert first["evidence_baseline"]["decision_fingerprint"] == second["evidence_baseline"]["decision_fingerprint"]
    serialized = str(first)
    assert "张三和李四是什么关系" not in serialized
    assert "旧港钟楼何时开启" not in serialized
    assert "对白保持简短并用动作承接" not in serialized
    assert "synthetic_tool_failure" not in serialized


def test_t0_scenarios_cover_required_terminal_and_edit_outcomes():
    result = asyncio.run(run_golden_replay_suite())
    scenario_cases = {
        case["id"]: case
        for case in result["cases"]
        if case["category"] == "representative_scenario"
    }

    assert all(case["passed"] for case in scenario_cases.values())
    assert scenario_cases["scenario-iteration-limit"]["metrics"]["diagnostics"]["terminal_state"] == "incomplete"
    assert scenario_cases["scenario-tool-failure-recovery"]["metrics"]["diagnostics"]["terminal_state"] == "completed"
    for target in ("head", "middle", "tail"):
        outcome = scenario_cases[f"scenario-edit-{target}"]["metrics"]["diagnostics"]["edit_target_outcome"]
        assert outcome == {"target": target, "changed": True, "status": "observed"}


def test_t0_provider_optional_failure_is_diagnostic_only(monkeypatch):
    original = golden_replay.evaluate_representative_scenarios

    async def with_provider_failure():
        rows = await original()
        for row in rows:
            if row["manifest"]["layer"] == "provider_optional":
                row["passed"] = False
                row["failure"] = "synthetic optional diagnostic failure"
        return rows

    monkeypatch.setattr(golden_replay, "evaluate_representative_scenarios", with_provider_failure)
    result = asyncio.run(run_golden_replay_suite())
    provider_case = next(case for case in result["cases"] if case["id"] == "scenario-provider-capability-degradation")

    assert result["success"] is True
    assert provider_case["passed"] is False
    assert provider_case["gate"] is False
    assert provider_case not in result["failures"]
