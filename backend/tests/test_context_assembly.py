# -*- coding: utf-8 -*-
"""P8 context assembly policy tests."""

from app.context_engine.context_assembly import build_context_assembly_plan, choose_context_strategy


def test_small_project_uses_full_canon_strategy():
    assert choose_context_strategy(estimated_canon_items=10, context_budget_tokens=16000) == "full_canon"


def test_large_project_uses_jit_retrieval_strategy():
    assert choose_context_strategy(estimated_canon_items=500, estimated_context_tokens=18000) == "jit_retrieval"


def test_context_assembly_separates_ui_history_from_model_context():
    plan = build_context_assembly_plan(
        route_path="agentic_writer",
        estimated_canon_items=30,
        estimated_context_tokens=3000,
        context_budget_tokens=16000,
        has_compact_summary=True,
        source_types=["canon", "memory"],
    ).to_dict()
    assert plan["fresh_context_first"] is True
    assert plan["ui_history_policy"] == "preserve_full_conversation"
    assert plan["model_context_policy"] == "jit_from_project_files"
    assert any(source["type"] == "session_compact" for source in plan["sources"])
