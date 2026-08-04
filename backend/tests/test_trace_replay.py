# -*- coding: utf-8 -*-
"""P6 trace replay regression tests."""

import json
import asyncio

from app.context_engine.trace_collector import TraceCollector, TraceEventType, trace_collector
from app.eval.trace_replay import replay_trace_file, replay_trace_payload, summarize_trace
from app.llm_gateway.telemetry import GatewayTelemetryPort


def _payload():
    return {
        "events": [
            {"type": "context_select", "agent_name": "orchestrator", "timestamp": 1, "data": {"tokens": 50}},
            {
                "type": "llm_response",
                "agent_name": "llm_gateway",
                "timestamp": 2,
                "data": {
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                    "latency_ms": 200,
                },
            },
            {"type": "tool_call", "agent_name": "writer", "timestamp": 3, "data": {"tool": "query_canon"}},
            {
                "type": "tool_result",
                "agent_name": "writer",
                "timestamp": 4,
                "data": {"tool": "query_canon", "success": True, "result": "F1"},
            },
            {
                "type": "context_plan",
                "agent_name": "orchestrator",
                "timestamp": 5,
                "data": {"route_path": "agentic_writer", "budget": {"actual_tokens": 180, "latency_ms": 250}},
            },
        ],
        "agent_traces": [],
    }


def test_summarize_trace_aggregates_cost_and_trajectory():
    summary = summarize_trace(_payload())
    assert summary["event_count"] == 5
    assert summary["tool_calls"] == 1
    assert summary["invalid_tool_results"] == 0
    assert summary["tokens"]["llm"]["total"] == 130
    assert summary["tokens"]["context_select"] == 50
    assert summary["tokens"]["total_observed"] == 180
    assert summary["latency_ms"]["observed"] == 250
    assert summary["route_counts"]["agentic_writer"] == 1


def test_replay_trace_payload_thresholds():
    result = replay_trace_payload(_payload(), thresholds={"invalid_tool_rate_max": 0.0, "fallback_rate_max": 0.0})
    assert result["success"] is True
    assert result["gate"]["checks"]["invalid_tool_rate_ok"] is True


def test_replay_trace_file(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    result = replay_trace_file(path)
    assert result["success"] is True
    assert result["summary"]["llm_responses"] == 1


def test_trace_collector_summary_prefers_gateway_usage_without_double_counting():
    start = trace_collector.event_count()
    asyncio.run(
        trace_collector.record(
            TraceEventType.LLM_REQUEST,
            "writer",
            {"tokens": {"total": 120, "prompt": 90, "completion": 30}, "latency_ms": 100},
        )
    )
    asyncio.run(
        trace_collector.record(
            TraceEventType.LLM_RESPONSE,
            "llm_gateway",
            {"usage": {"total_tokens": 120, "prompt_tokens": 90, "completion_tokens": 30}, "latency_ms": 100},
        )
    )
    summary = trace_collector.summarize_events_since(start)
    assert summary["llm_requests"] == 1
    assert summary["llm_responses"] == 1
    assert summary["llm_tokens"] == 120
    assert summary["tokens"] == 120


def test_gateway_telemetry_records_an_immutable_json_snapshot(monkeypatch):
    collector = TraceCollector()
    monkeypatch.setattr("app.llm_gateway.telemetry.trace_collector", collector)
    telemetry = GatewayTelemetryPort()

    record = asyncio.run(
        telemetry.record_request_plan(
            messages=[{"role": "user", "content": "修改正文"}],
            provider="fake",
            temperature=0.2,
            max_tokens=100,
            tools=[],
        )
    )
    record["_deadline"] = object()

    event = collector.get_recent_events(1)[0]
    assert "_deadline" not in event["data"]
    json.dumps(event, ensure_ascii=False)
