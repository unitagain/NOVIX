# -*- coding: utf-8 -*-
"""P8 tool registry regression tests."""

from app.agents.writing_actions import WritingActionToolset
from app.context_engine.context_plan import build_context_plan_v2
from app.context_engine.tool_registry import list_tool_specs, tool_loadout_for_route, tool_loadout_summary


def test_tool_registry_exposes_explainable_agentic_loadout():
    loadout = tool_loadout_for_route("agentic_writer")
    names = {item["name"] for item in loadout}
    assert {"lookup_card", "query_canon", "create_chapter", "write_content", "edit_lines"}.issubset(names)
    assert all("context_cost" in item and "scope" in item for item in loadout)
    summary = tool_loadout_summary(loadout)
    assert summary["tool_count"] == len(loadout)
    assert summary["permissions"]["allow"] >= 1
    assert "write_content" in summary["write_tools"]


def test_untrusted_context_removes_write_tools_from_loadout():
    loadout = tool_loadout_for_route(
        "agentic_writer",
        trust_context={"consumed_untrusted": True, "trust_label": "untrusted"},
    )
    assert loadout
    assert all(item["read_only"] for item in loadout)
    assert not any(item["name"] == "write_content" for item in loadout)


def test_external_tools_default_to_ask():
    specs = {spec.name: spec for spec in list_tool_specs()}
    assert specs["fanfiction_search"].to_loadout()["permission"] == "ask"
    assert specs["third_party_tool"].to_loadout()["permission"] == "ask"


def test_context_plan_uses_registry(tmp_path):
    plan = build_context_plan_v2(
        turn_id="t1",
        project_id="p1",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path,
    )
    payload = plan.to_dict()
    assert any(item["name"] == "query_canon" for item in payload["tool_loadout"])
    assert payload["policy"]["selection"] == "fresh_context_first_full_canon_or_jit_by_project_size"
    assert payload["fingerprints"]["plan"]


def test_context_plan_allows_writer_terminal_tool(tmp_path):
    plan = build_context_plan_v2(
        turn_id="t1",
        project_id="p1",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path,
    )

    record = plan.validate_request(
        messages=[],
        provider=None,
        temperature=0.7,
        max_tokens=None,
        tools=WritingActionToolset("").schemas(),
        token_accounting={"tokens": 0, "upper_bound_tokens": 0},
    )

    assert "finish_turn" in plan.allowed_tool_names
    assert "finish_turn" in record["tool_names"]
