"""P11 contracts for state, service ownership, and modular pipelines."""

from __future__ import annotations

import pytest

from app.context_engine.models import ContextItem, ContextPriority, ContextType
from app.context_engine.retrieval_pipeline import (
    ContextualIndexBuilder,
    RankingTraceRenderer,
    ScoreFusion,
    TemporalScopeFilter,
)
from app.eval.longform_artifacts import BenchmarkPaths, read_json, write_json
from app.eval.longform_report import render_report
from app.eval.longform_statistics import cluster_bootstrap_mean_ci, numeric_distribution
from app.orchestrator.architecture import service_boundaries
from app.orchestrator.context_assembly_service import ContextAssemblyService
from app.orchestrator.turn_runtime import TurnRuntime, TurnState


def test_turn_runtime_enforces_legal_transitions():
    runtime = TurnRuntime("turn")
    runtime.transition(TurnState.ROUTING)
    runtime.transition(TurnState.CONTEXT_PLANNING)
    runtime.transition(TurnState.WRITER_RUNNING)
    runtime.complete()
    assert runtime.state == TurnState.COMPLETED
    assert [row.target for row in runtime.transitions] == [
        "routing",
        "context_planning",
        "writer_running",
        "completed",
    ]
    with pytest.raises(ValueError, match="invalid_runtime_transition"):
        TurnRuntime("bad").transition(TurnState.WRITER_RUNNING)


def test_context_assembly_is_deterministic_and_uses_plan_budget():
    service = ContextAssemblyService(language="zh")
    plan = type("Plan", (), {"budget": {"output_reserve_tokens": 4200}})()
    first = service.assemble_writer_request(
        message="继续写",
        chapter="V1C001",
        current_text="",
        has_selection=False,
        target_word_count=3000,
        context_plan=plan,
    )
    second = service.assemble_writer_request(
        message="继续写",
        chapter="V1C001",
        current_text="",
        has_selection=False,
        target_word_count=3000,
        context_plan=plan,
    )
    assert first.fingerprint == second.fingerprint
    assert first.max_tokens == 4200
    assert first.messages[0]["role"] == "system"


def test_retrieval_components_are_independently_testable():
    assert TemporalScopeFilter.is_future("V1C004", "V1C003") is True
    assert "章节:V1C003" in ContextualIndexBuilder.build("钥匙仍在桌上", {"chapter": "V1C003"})
    candidates = [
        ContextItem("a", ContextType.FACT, "A", ContextPriority.MEDIUM, relevance_score=1.0),
        ContextItem("b", ContextType.FACT, "B", ContextPriority.MEDIUM, relevance_score=0.1),
    ]
    fused = ScoreFusion(strategy="rrf", bm25_weight=0.5, vector_weight=0.5).fuse(candidates, [0.1, 1.0])
    assert set(fused) == {0, 1}
    trace = RankingTraceRenderer.render(
        query="钥匙",
        candidates=candidates,
        returned=candidates[:1],
        fusion="rrf",
        semantic_enabled=True,
        semantic_used=True,
        semantic_degraded=False,
        reranker_requested=False,
        rerank_applied=False,
        reranker_degraded=False,
    )
    assert trace["signals"]["embedding"] is True
    assert trace["top_results"][0]["id"] == "a"


def test_benchmark_artifact_statistics_and_report_boundaries(tmp_path):
    paths = BenchmarkPaths(tmp_path, "demo")
    write_json(paths.manifest, {"benchmark_id": "demo"})
    assert read_json(paths.manifest)["benchmark_id"] == "demo"
    assert numeric_distribution([1, 2, 3])["p95"] == 3
    ci = cluster_bootstrap_mean_ci(
        [{"scene_id": "s1", "score_b": 1.0}, {"scene_id": "s2", "score_b": 0.0}],
        seed_material="stable",
        samples=100,
    )
    assert ci["clusters"] == 2
    report = render_report(
        {"benchmark_id": "demo", "corpus_name": "demo"},
        {"suite": "smoke", "strategy": "bm25"},
        {},
        [],
    )
    assert "# Longform Benchmark Report" in report


def test_architecture_contract_declares_p11_service_owners():
    boundaries = {row["name"]: row for row in service_boundaries()}
    assert boundaries["turn_runtime"]["current"] == "TurnRuntime explicit state machine"
    assert boundaries["context_preparation"]["current"] == "ContextPlanningService + ContextAssemblyService"
    assert boundaries["writing_execution"]["current"] == "WritingService"
    assert boundaries["post_turn"]["current"] == "PostTurnService"
    assert "candidate/filter/index/vector/fusion/trace" in boundaries["retrieval_pipeline"]["current"]
