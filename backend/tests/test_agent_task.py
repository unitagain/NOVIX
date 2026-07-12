# -*- coding: utf-8 -*-
"""P4 · AgentTask 隔离 worker 回归测试。"""

import asyncio

from app.agents.agent_task import AgentTask, AgentTaskRunner, MergePolicy
from app.context_engine.trace_collector import trace_collector


def test_agent_task_memory_extract_returns_review_candidate():
    task = AgentTask(
        id="mem1",
        kind="memory_extract",
        input={"feedback": "对白要短，少用形容词", "source": "session"},
        permissions=["write_memory"],
        merge_policy=MergePolicy.USER_CONFIRM.value,
    )
    result = asyncio.run(AgentTaskRunner().run(task))
    assert result.status == "completed"
    candidate = result.output["candidates"][0]
    assert candidate["status"] == "needs_review"
    assert result.output["permission_requests"][0]["level"] == "ask"
    assert result.output["merge_policy"] == "user_confirm"
    assert result.trace_ref


def test_agent_task_denied_permission_fails_without_handler():
    task = AgentTask(id="deny1", kind="summarize", input={"text": "abc"}, permissions=["delete_chapter"])
    result = asyncio.run(AgentTaskRunner().run(task))
    assert result.status == "failed"
    assert result.error == "permission_denied"
    assert result.output["permission_requests"][0]["level"] == "deny"


def test_agent_task_handler_error_is_isolated():
    def boom(_task):
        raise RuntimeError("worker failed")

    task = AgentTask(id="fail1", kind="summarize", input={"text": "abc"})
    result = asyncio.run(AgentTaskRunner({"summarize": boom}).run(task))
    assert result.status == "failed"
    assert result.error == "internal_error"
    assert "worker failed" not in result.error


def test_agent_task_trace_event_recorded():
    before = len(trace_collector.get_recent_events(1000))
    task = AgentTask(id="sum1", kind="summarize", input={"text": "abcdef", "max_chars": 4})
    result = asyncio.run(AgentTaskRunner().run(task))
    assert result.output["summary"] == "a..."
    events = trace_collector.get_recent_events(1000)[before:]
    assert any(e.get("type") == "agent_task" and e.get("data", {}).get("task_id") == "sum1" for e in events)


def test_agent_task_input_budget_is_enforced():
    task = AgentTask(id="budget1", kind="summarize", input={"text": "abcdef"}, budget={"max_input_chars": 5})
    result = asyncio.run(AgentTaskRunner().run(task))
    assert result.status == "failed"
    assert result.error == "budget_input_chars_exceeded"


def test_agent_task_retrieve_respects_max_items():
    task = AgentTask(
        id="retr1",
        kind="retrieve",
        input={"query": "a", "candidates": [{"text": "a1"}, {"text": "a2"}, {"text": "a3"}], "limit": 3},
        budget={"max_items": 2},
    )
    result = asyncio.run(AgentTaskRunner().run(task))
    assert result.status == "completed"
    assert len(result.output["hits"]) == 2


def test_agent_task_timeout_is_isolated():
    async def slow(_task):
        await asyncio.sleep(0.05)
        return {"ok": True}

    task = AgentTask(id="timeout1", kind="summarize", input={"text": "abc"}, budget={"timeout_ms": 1})
    result = asyncio.run(AgentTaskRunner({"summarize": slow}).run(task))
    assert result.status == "failed"
    assert result.error == "budget_timeout"
