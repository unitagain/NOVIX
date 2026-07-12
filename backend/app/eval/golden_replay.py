# -*- coding: utf-8 -*-
"""P6 golden replay gate.

默认套件只使用确定性组件、真实文件存储和可回放 trace，不依赖外部 LLM。
LLM judge 属于扩展轨道，由 app.eval.writing_judge 显式运行。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

from app.eval.trace_replay import replay_trace_payload
from app.orchestrator.architecture import route_contract


CaseRunner = Callable[[], Awaitable[Dict[str, Any]]]


def _case(case_id: str, category: str, passed: bool, metrics: Dict[str, Any] | None = None, failure: str = ""):
    return {
        "id": case_id,
        "category": category,
        "passed": bool(passed),
        "metrics": metrics or {},
        "failure": "" if passed else failure,
    }


def _sample_trace_payload() -> Dict[str, Any]:
    return {
        "events": [
            {
                "id": "evt_000001",
                "type": "context_select",
                "agent_name": "orchestrator",
                "timestamp": 1.0,
                "data": {"selected": 3, "candidates": 8, "tokens": 120},
            },
            {
                "id": "evt_000002",
                "type": "tool_call",
                "agent_name": "writer",
                "timestamp": 2.0,
                "data": {"tool": "query_canon", "args": {"query": "玉佩"}},
            },
            {
                "id": "evt_000003",
                "type": "tool_result",
                "agent_name": "writer",
                "timestamp": 2.5,
                "data": {"tool": "query_canon", "success": True, "result": "F4"},
                "parent_id": "evt_000002",
            },
            {
                "id": "evt_000004",
                "type": "llm_response",
                "agent_name": "llm_gateway",
                "timestamp": 3.0,
                "data": {
                    "provider": "openai",
                    "model": "gpt-eval",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                    "latency_ms": 350,
                },
            },
            {
                "id": "evt_000005",
                "type": "context_plan",
                "agent_name": "orchestrator",
                "timestamp": 4.0,
                "data": {
                    "task_id": "golden-trace",
                    "route_path": "agentic_writer",
                    "budget": {"actual_tokens": 260, "latency_ms": 420, "tool_calls": 1, "llm_requests": 1},
                    "degradation": [],
                    "trace_ref": "traces/golden-trace.json",
                },
            },
        ],
        "agent_traces": [],
    }


async def _component_cases() -> List[Dict[str, Any]]:
    from app.eval.eval_suite import (
        run_compact_eval,
        run_consistency_eval,
        run_memory_eval,
        run_memory_governance_eval,
        run_p8_context_boundary_eval,
        run_retrieval_eval,
        run_retrieval_quality_eval,
        run_security_eval,
    )

    cases: List[Dict[str, Any]] = []

    retrieval = await run_retrieval_eval(top_k=5)
    for idx, item in enumerate(retrieval.get("cases") or [], start=1):
        cases.append(
            _case(
                f"retrieval-{idx:02d}",
                "retrieval",
                bool(item.get("matched")),
                {"query": item.get("query"), "matched": item.get("matched"), "retrieved": item.get("retrieved")},
                "expected fact was not retrieved",
            )
        )

    quality = await run_retrieval_quality_eval()
    cases.extend(
        [
            _case(
                "retrieval-quality-prefix",
                "retrieval",
                quality.get("contextual_prefix_hit") is True,
                quality,
                "contextual prefix did not affect ranking",
            ),
            _case(
                "retrieval-quality-trace",
                "retrieval",
                quality.get("ranking_trace_available") is True,
                quality,
                "ranking trace missing",
            ),
            _case(
                "retrieval-quality-bm25",
                "retrieval",
                bool((quality.get("signals") or {}).get("bm25")),
                quality,
                "bm25 signal missing",
            ),
        ]
    )

    memory = await run_memory_eval()
    cases.append(_case("memory-recall", "memory", memory.get("recall") == 1.0, memory, "memory recall regressed"))

    governance = await run_memory_governance_eval()
    for item in governance.get("cases") or []:
        cases.append(
            _case(
                f"memory-governance-{item.get('id')}",
                "memory",
                item.get("passed") is True,
                item,
                str(item.get("failure") or "memory governance case failed"),
            )
        )

    compact = await run_compact_eval()
    cases.extend(
        [
            _case("compact-triggered", "compact", compact.get("compacted") is True, compact, "compact did not trigger"),
            _case("compact-key-retained", "compact", compact.get("key_retained") is True, compact, "key context lost"),
            _case(
                "compact-recent-retained",
                "compact",
                compact.get("recent_retained") is True,
                compact,
                "recent context lost",
            ),
        ]
    )

    consistency = run_consistency_eval()
    cases.extend(
        [
            _case(
                "consistency-conflict",
                "consistency",
                consistency.get("relation_conflict_detected") is True,
                consistency,
                "relation conflict not detected",
            ),
            _case(
                "consistency-count",
                "consistency",
                consistency.get("conflicts_detected", 0) >= consistency.get("expected_conflicts", 1),
                consistency,
                "conflict count below expected",
            ),
        ]
    )

    security = await run_security_eval()
    for item in security.get("cases") or []:
        cases.append(
            _case(
                f"security-{item.get('id')}",
                "security",
                item.get("passed") is True,
                item,
                str(item.get("failure") or "security case failed"),
            )
        )

    p8_boundary = run_p8_context_boundary_eval()
    for item in p8_boundary.get("cases") or []:
        cases.append(
            _case(
                f"p8-boundary-{item.get('id')}",
                "security",
                item.get("passed") is True,
                item,
                str(item.get("failure") or "P8 context boundary case failed"),
            )
        )

    return cases


async def _route_cases() -> List[Dict[str, Any]]:
    specs = [
        ("write", False, False, "agentic_writer"),
        ("edit", False, False, "agentic_writer"),
        ("continue", False, False, "agentic_writer"),
        ("plan", False, False, "plan_workflow"),
        ("plan", False, True, "plan_workflow"),
        ("review", True, False, "fallback_workflow"),
    ]
    cases = []
    for idx, (action, fallback, auto_execute_plan, expected_path) in enumerate(specs, start=1):
        contract = route_contract(action, fallback=fallback, auto_execute_plan=auto_execute_plan)
        cases.append(
            _case(
                f"route-{idx:02d}",
                "route",
                contract.get("path") == expected_path,
                {"action": action, "contract": contract},
                f"expected route {expected_path}",
            )
        )
    return cases


async def _trace_cases(thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = replay_trace_payload(_sample_trace_payload(), thresholds=thresholds)
    summary = result["summary"]
    return [
        _case("trace-replay-gate", "trace", result.get("success") is True, result, "trace thresholds failed"),
        _case(
            "trace-replay-cost",
            "trace",
            summary.get("tokens", {}).get("total_observed", 0) > 0
            and summary.get("latency_ms", {}).get("observed", 0) > 0,
            summary,
            "trace cost metrics missing",
        ),
        _case(
            "trace-replay-tool-use",
            "trace",
            summary.get("tool_calls") == 1 and summary.get("invalid_tool_results") == 0,
            summary,
            "trace tool metrics regressed",
        ),
    ]


async def run_golden_replay_suite(thresholds: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run the default P6 CI gate suite."""

    thresholds = {
        "retrieval_recall_min": 0.6,
        "fallback_rate_max": 0.35,
        "invalid_tool_rate_max": 0.1,
        "latency_ms_max": 300000,
        "tokens_max": 500000,
        **(thresholds or {}),
    }
    cases: List[Dict[str, Any]] = []
    cases.extend(await _component_cases())
    cases.extend(await _route_cases())
    cases.extend(await _trace_cases(thresholds))

    retrieval_cases = [case for case in cases if case["category"] == "retrieval"]
    retrieval_pass = [case for case in retrieval_cases if case["passed"]]
    fallback_route_cases = [
        case
        for case in cases
        if case["category"] == "route" and (case.get("metrics", {}).get("contract") or {}).get("fallback")
    ]
    fallback_rate = len(fallback_route_cases) / len([case for case in cases if case["category"] == "route"])
    aggregate_checks = {
        "case_count_ok": len(cases) >= 30,
        "retrieval_recall_ok": (len(retrieval_pass) / len(retrieval_cases)) >= thresholds["retrieval_recall_min"]
        if retrieval_cases
        else False,
        "fallback_rate_ok": fallback_rate <= thresholds["fallback_rate_max"],
    }
    failures = [case for case in cases if not case["passed"]]
    aggregate_failures = [name for name, ok in aggregate_checks.items() if not ok]
    return {
        "success": not failures and not aggregate_failures,
        "num_cases": len(cases),
        "thresholds": thresholds,
        "aggregate": {"checks": aggregate_checks, "fallback_rate": fallback_rate},
        "failures": failures,
        "aggregate_failures": aggregate_failures,
        "cases": cases,
    }
