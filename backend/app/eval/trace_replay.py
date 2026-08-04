# -*- coding: utf-8 -*-
"""P6 trace replay utilities.

该模块只读取已保存的 trace JSON，聚合轨迹级指标，不重新执行业务逻辑。
它用于把真实失败轨迹转成可重复评测证据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def load_trace(path: str | Path) -> Dict[str, Any]:
    """Load a persisted trace JSON file."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trace payload must be a JSON object")
    return payload


def _events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_events = payload.get("events") or []
    if not isinstance(raw_events, list):
        return []
    return [item for item in raw_events if isinstance(item, dict)]


def _event_type(event: Dict[str, Any]) -> str:
    return str(event.get("type") or "").strip()


def _event_data(event: Dict[str, Any]) -> Dict[str, Any]:
    data = event.get("data") or {}
    return data if isinstance(data, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_tokens(data: Dict[str, Any]) -> Dict[str, int]:
    usage = data.get("usage") or data.get("tokens") or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt = _int(usage.get("prompt_tokens", usage.get("prompt", 0)))
    completion = _int(usage.get("completion_tokens", usage.get("completion", 0)))
    total = _int(usage.get("total_tokens", usage.get("total", 0)))
    if total <= 0:
        total = prompt + completion
    return {"total": total, "prompt": prompt, "completion": completion}


def extract_context_plans(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ContextPlan events from a trace payload."""

    plans: List[Dict[str, Any]] = []
    for event in _events(payload):
        if _event_type(event) == "context_plan":
            data = _event_data(event)
            if data:
                plans.append(data)
    return plans


def summarize_trace(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate trajectory, token, latency, route, and degradation metrics."""

    events = _events(payload)
    by_type: Dict[str, int] = {}
    llm_request_tokens = {"total": 0, "prompt": 0, "completion": 0}
    llm_response_tokens = {"total": 0, "prompt": 0, "completion": 0}
    context_select_tokens = 0
    latency_from_events = 0
    invalid_tool_results = 0
    route_counts: Dict[str, int] = {}
    fallback_count = 0

    for event in events:
        event_type = _event_type(event)
        by_type[event_type] = by_type.get(event_type, 0) + 1
        data = _event_data(event)

        if event_type == "llm_request":
            usage = _usage_tokens(data)
            for key, value in usage.items():
                llm_request_tokens[key] += value
            latency_from_events += _int(data.get("latency_ms"))
        elif event_type == "llm_response":
            usage = _usage_tokens(data)
            for key, value in usage.items():
                llm_response_tokens[key] += value
            latency_from_events += _int(data.get("latency_ms"))
        elif event_type == "context_select":
            context_select_tokens += _int(data.get("tokens"))
        elif event_type == "tool_result":
            if data.get("success") is False:
                invalid_tool_results += 1
        elif event_type == "context_plan":
            route_path = str(data.get("route_path") or "").strip()
            if route_path:
                route_counts[route_path] = route_counts.get(route_path, 0) + 1
            for item in data.get("degradation") or []:
                if isinstance(item, dict) and item.get("status") == "fallback":
                    fallback_count += 1

    context_plans = extract_context_plans(payload)
    context_plan_tokens = sum(_int((plan.get("budget") or {}).get("actual_tokens")) for plan in context_plans)
    context_plan_latency = sum(_int((plan.get("budget") or {}).get("latency_ms")) for plan in context_plans)
    llm_tokens = llm_response_tokens if llm_response_tokens["total"] > 0 else llm_request_tokens
    tool_results = by_type.get("tool_result", 0)
    tool_calls = by_type.get("tool_call", 0)
    invalid_tool_rate = (invalid_tool_results / tool_results) if tool_results else 0.0
    context_plan_count = len(context_plans)
    fallback_rate = (fallback_count / context_plan_count) if context_plan_count else 0.0

    return {
        "event_count": len(events),
        "by_type": by_type,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "invalid_tool_results": invalid_tool_results,
        "invalid_tool_rate": invalid_tool_rate,
        "llm_requests": by_type.get("llm_request", 0),
        "llm_responses": by_type.get("llm_response", 0),
        "context_selects": by_type.get("context_select", 0),
        "context_plans": context_plan_count,
        "context_compresses": by_type.get("context_compress", 0),
        "agent_tasks": by_type.get("agent_task", 0),
        "fallback_count": fallback_count,
        "fallback_rate": fallback_rate,
        "route_counts": route_counts,
        "tokens": {
            "llm": llm_tokens,
            "context_select": context_select_tokens,
            "context_plan_actual": context_plan_tokens,
            "total_observed": llm_tokens["total"] + context_select_tokens,
        },
        "latency_ms": {
            "events": latency_from_events,
            "context_plan_actual": context_plan_latency,
            "observed": max(latency_from_events, context_plan_latency),
        },
    }


def evaluate_trace_thresholds(summary: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate a trace summary against CI-friendly thresholds."""

    thresholds = thresholds or {}
    checks = {
        "has_events": summary.get("event_count", 0) > 0,
        "invalid_tool_rate_ok": summary.get("invalid_tool_rate", 0.0)
        <= float(thresholds.get("invalid_tool_rate_max", 0.2)),
        "fallback_rate_ok": summary.get("fallback_rate", 0.0) <= float(thresholds.get("fallback_rate_max", 0.5)),
        "latency_ok": summary.get("latency_ms", {}).get("observed", 0)
        <= int(thresholds.get("latency_ms_max", 300000)),
        "tokens_ok": summary.get("tokens", {}).get("total_observed", 0)
        <= int(thresholds.get("tokens_max", 500000)),
    }
    failures = [name for name, ok in checks.items() if not ok]
    return {"passed": not failures, "checks": checks, "failures": failures, "thresholds": thresholds}


def replay_trace_payload(payload: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Replay a trace payload into metrics and threshold checks."""

    summary = summarize_trace(payload)
    gate = evaluate_trace_thresholds(summary, thresholds)
    return {"success": gate["passed"], "summary": summary, "gate": gate}


def replay_trace_file(path: str | Path, thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Replay a saved trace file into metrics and threshold checks."""

    return replay_trace_payload(load_trace(path), thresholds)


def replay_trace_files(paths: Iterable[str | Path], thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Replay multiple saved trace files and return an aggregate gate result."""

    cases = []
    for path in paths:
        result = replay_trace_file(path, thresholds)
        result["path"] = str(path)
        cases.append(result)
    failures = [case for case in cases if not case.get("success")]
    return {"success": not failures, "num_cases": len(cases), "failures": failures, "cases": cases}
