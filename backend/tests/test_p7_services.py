# -*- coding: utf-8 -*-
"""P7 service extraction regression tests."""

import asyncio

from app.agents.agent_task import AgentTask
from app.context_engine.trace_collector import trace_collector
from app.orchestrator.context_planning_service import ContextPlanningService
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.worker_task_service import WorkerTaskService


class _SelectEngine:
    def __init__(self):
        self._last_ranking_trace = {"top_results": [{"id": "F1", "score": 1.0}], "fusion": "lexical"}

    def get_last_ranking_trace(self):
        return dict(self._last_ranking_trace)


class _DraftPath:
    def __init__(self, base):
        self.base = base

    def get_project_path(self, project_id):
        return self.base / project_id


def test_orchestrator_exposes_p7_services(tmp_path):
    orch = Orchestrator(str(tmp_path))
    assert orch.context_planning_service is not None
    assert orch.plan_execution_service is not None
    assert orch.worker_task_service is not None


def test_worker_task_service_registers_new_handler_without_orchestrator_change():
    service = WorkerTaskService()

    def count_chars(task: AgentTask):
        return {"chars": len(str(task.input.get("text") or ""))}

    service.register_handler("count_chars", count_chars)
    result = asyncio.run(service.run_task(project_id="p", kind="count_chars", input={"text": "abcd"}))
    assert result.status == "completed"
    assert result.output["chars"] == 4


def test_context_planning_service_attaches_trace_and_actual_ranking(tmp_path):
    service = ContextPlanningService(select_engine=_SelectEngine(), draft_storage=_DraftPath(tmp_path))
    result = asyncio.run(
        service.attach_chat_context_plan(
            {"success": True, "route_contract": {"path": "agentic_writer", "fallback": False}},
            project_id="p",
            chapter="V1C001",
            intent="write",
            target_word_count=1000,
        )
    )
    assert result["context_plan"]["route_path"] == "agentic_writer"
    assert result["context_plan"]["version"] == 2
    assert result["context_plan"]["ranking"]["actual"]["top_results"][0]["id"] == "F1"
    assert (tmp_path / "p" / result["trace_ref"]).exists()


def test_plan_research_uses_worker_trace(tmp_path):
    orch = Orchestrator(str(tmp_path))

    class _Item:
        id = "F1"
        type = "fact"
        content = "玉佩是母亲遗物"

    async def retrieval_select(**_kwargs):
        return [_Item()]

    orch.select_engine.retrieval_select = retrieval_select
    before = len(trace_collector.get_recent_events(1000))
    note = asyncio.run(orch.plan_execution_service.research_note("p", "玉佩"))
    events = trace_collector.get_recent_events(1000)[before:]
    assert "玉佩" in note
    assert any(event.get("type") == "agent_task" for event in events)
