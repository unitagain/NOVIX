# -*- coding: utf-8 -*-
"""Phase 15 · trace 落盘回归测试。

验证 trace_collector.save_trace 把事件 + agent 追踪落盘为可复用 JSON。无网络 / 无 key。
"""

import asyncio
import json

from app.context_engine.trace_collector import trace_collector


def test_save_trace_writes_json(tmp_path):
    path = tmp_path / "sub" / "trace.json"  # 顺带验证自动建目录
    ok = asyncio.run(trace_collector.save_trace(str(path)))
    assert ok
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "events" in data and "agent_traces" in data
    assert isinstance(data["events"], list)


def test_save_trace_includes_context_plan_event(tmp_path):
    path = tmp_path / "trace" / "context_plan.json"
    plan = {
        "task_id": "chat_eval",
        "intent": "write",
        "route_path": "agentic_writer",
        "sources": [{"type": "canon"}],
        "tool_loadout": [{"name": "query_canon", "permission": "allow"}],
        "degradation": [],
    }
    asyncio.run(trace_collector.record_context_plan("orchestrator", plan))
    ok = asyncio.run(trace_collector.save_trace(str(path)))
    assert ok
    data = json.loads(path.read_text(encoding="utf-8"))
    events = [e for e in data["events"] if e.get("type") == "context_plan"]
    assert events
    assert events[-1]["data"]["task_id"] == "chat_eval"
    assert events[-1]["trace_id"]
    assert events[-1]["span_id"]


def test_trace_event_records_parent_span_id():
    parent = asyncio.run(trace_collector.record_context_plan("orchestrator", {"task_id": "parent"}))
    child = asyncio.run(
        trace_collector.record(
            trace_collector.events[-1].type,
            "orchestrator",
            {"task_id": "child"},
            parent_id=parent.id,
        )
    )
    assert child.parent_id == parent.id
    assert child.parent_span_id == parent.span_id
    assert child.trace_id == parent.trace_id
