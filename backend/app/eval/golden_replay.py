# -*- coding: utf-8 -*-
"""P6 golden replay gate.

默认套件只使用确定性组件、真实文件存储和可回放 trace，不依赖外部 LLM。
LLM judge 属于扩展轨道，由 app.eval.writing_judge 显式运行。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable, Dict, List

from app.eval.representative_scenarios import EVAL_ASSET_INVENTORY, evaluate_representative_scenarios
from app.eval.trace_replay import replay_trace_payload
from app.orchestrator.architecture import route_contract


CaseRunner = Callable[[], Awaitable[Dict[str, Any]]]
_PRIVATE_METRIC_KEYS = {"body", "content", "message", "output_preview", "prompt", "query", "statement"}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _privacy_safe(value: Any) -> Any:
    if isinstance(value, dict):
        safe: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _PRIVATE_METRIC_KEYS:
                if item not in (None, "", [], {}):
                    safe[f"{key}_fingerprint"] = _fingerprint(item)
                continue
            safe[str(key)] = _privacy_safe(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [_privacy_safe(item) for item in value]
    return value


def _case(
    case_id: str,
    category: str,
    passed: bool,
    metrics: Dict[str, Any] | None = None,
    failure: str = "",
    *,
    gate: bool = True,
):
    return {
        "id": case_id,
        "category": category,
        "passed": bool(passed),
        "gate": bool(gate),
        "metrics": _privacy_safe(metrics or {}),
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
        ("write", False, "agentic_writer"),
        ("edit", False, "agentic_writer"),
        ("continue", False, "agentic_writer"),
        ("plan", False, "plan_workflow"),
        ("plan", True, "plan_workflow"),
    ]
    cases = []
    for idx, (action, auto_execute_plan, expected_path) in enumerate(specs, start=1):
        contract = route_contract(action, auto_execute_plan=auto_execute_plan)
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


async def _representative_cases() -> List[Dict[str, Any]]:
    cases = []
    for result in await evaluate_representative_scenarios():
        manifest = result.get("manifest") or {}
        cases.append(
            _case(
                f"scenario-{result['id']}",
                "representative_scenario",
                bool(result.get("passed")),
                result,
                str(result.get("failure") or "representative scenario failed"),
                gate=manifest.get("layer") == "deterministic",
            )
        )
    return cases


def _evidence_baseline(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app.observability.usage_diagnostics import memory_semantic_recall_decision

    scenario_cases = [case for case in cases if case.get("category") == "representative_scenario"]
    scenario_rows = [case.get("metrics") or {} for case in scenario_cases]
    trace_cost = next((case.get("metrics") or {} for case in cases if case.get("id") == "trace-replay-cost"), {})

    terminal_states: Dict[str, int] = {}
    fallback_categories: Dict[str, int] = {}
    edit_targets: Dict[str, Dict[str, Any]] = {}
    degradation: Dict[str, int] = {}
    required_total = 0
    observed_total = 0
    unexpected_source_types = 0
    evidence_status: Dict[str, int] = {}
    for row in scenario_rows:
        diagnostics = row.get("diagnostics") or {}
        coverage = diagnostics.get("critical_source_coverage") or {}
        required_total += int(coverage.get("required_count") or 0)
        observed_total += int(coverage.get("observed_count") or 0)
        unexpected_source_types += int((diagnostics.get("source_overfetch") or {}).get("count") or 0)
        terminal = str(diagnostics.get("terminal_state") or "unknown")
        terminal_states[terminal] = terminal_states.get(terminal, 0) + 1
        fallback = str(diagnostics.get("fallback_category") or "none")
        fallback_categories[fallback] = fallback_categories.get(fallback, 0) + 1
        edit = diagnostics.get("edit_target_outcome") or {}
        if edit.get("status") == "observed":
            edit_targets[str(edit.get("target") or "unknown")] = {"changed": bool(edit.get("changed"))}
        for item in diagnostics.get("degradation") or []:
            name = str(item or "unknown")
            degradation[name] = degradation.get(name, 0) + 1
        status = str(row.get("evidence_status") or "insufficient_evidence")
        evidence_status[status] = evidence_status.get(status, 0) + 1

    metric_decisions = [
        {
            "metric": "critical_source_coverage",
            "decision": "hard_gate_candidate",
            "basis": "synthetic_required_source_contract",
        },
        {
            "metric": "edit_target_outcome",
            "decision": "hard_gate_candidate",
            "basis": "deterministic_exact_edit_contract",
        },
        {
            "metric": "terminal_state",
            "decision": "hard_gate_candidate",
            "basis": "typed_runtime_terminal_contract",
        },
        {
            "metric": "fallback_category",
            "decision": "hard_gate_candidate",
            "basis": "content_free_reason_taxonomy",
        },
        {
            "metric": "permission_boundary",
            "decision": "hard_gate_candidate",
            "basis": "deterministic_allow_ask_deny_contract",
        },
        {
            "metric": "source_overfetch",
            "decision": "insufficient_evidence",
            "basis": "representative_corpus_not_established",
        },
        {
            "metric": "token_latency",
            "decision": "diagnostic_only",
            "basis": "synthetic_trace_is_not_a_production_budget",
        },
        {
            "metric": "provider_degradation",
            "decision": "engineering_gate",
            "basis": "offline_adapter_inventory_and_explicit_degradation_contract",
        },
        {
            "metric": "style_outcome",
            "decision": "insufficient_evidence",
            "basis": "source_recall_does_not_prove_subjective_prose_quality",
        },
    ]
    resolved_gaps = [
        "provider_adapter_conformance",
    ]
    gaps = [
        "representative_source_overfetch_corpus",
        "production_token_latency_samples",
        "style_constraint_outcome_without_subjective_judge",
    ]
    context_policy_decision = {
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
    memory_policy_decision = {
        **memory_semantic_recall_decision(lexical_queries=0, labeled_semantic_misses=0),
        "schema_version": 1,
        "strategy": "lexical_only",
        "semantic_rrf_enabled": False,
        "eligibility_order": "filter_before_candidate_generation",
        "applied": False,
    }
    decision_material = {
        "assets": EVAL_ASSET_INVENTORY,
        "scenarios": [
            {
                "id": row.get("id"),
                "passed": row.get("passed"),
                "evidence_status": row.get("evidence_status"),
            }
            for row in scenario_rows
        ],
        "metric_decisions": metric_decisions,
        "resolved_gaps": resolved_gaps,
        "gaps": gaps,
        "context_policy_decision": context_policy_decision,
        "memory_policy_decision": memory_policy_decision,
    }
    return {
        "schema_version": 1,
        "classification": "content_free_deterministic_baseline",
        "assets": [dict(item) for item in EVAL_ASSET_INVENTORY],
        "scenario_count": len(scenario_rows),
        "scenario_evidence_status": evidence_status,
        "diagnostics": {
            "critical_source_coverage": {
                "required_count": required_total,
                "observed_count": observed_total,
                "ratio": (observed_total / required_total) if required_total else 1.0,
            },
            "source_overfetch": {"unexpected_type_count": unexpected_source_types},
            "edit_target_outcome": edit_targets,
            "terminal_state": terminal_states,
            "fallback_category": fallback_categories,
            "token": trace_cost.get("tokens") or {"status": "insufficient_evidence"},
            "latency": trace_cost.get("latency_ms") or {"status": "insufficient_evidence"},
            "degradation": degradation,
        },
        "metric_decisions": metric_decisions,
        "resolved_evidence_gaps": resolved_gaps,
        "policy_decisions": {
            "context": context_policy_decision,
            "memory": memory_policy_decision,
        },
        "evidence_gaps": gaps,
        "decision_fingerprint": _fingerprint(decision_material),
    }


async def run_golden_replay_suite(thresholds: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run the default P6 CI gate suite."""

    thresholds = {
        "retrieval_recall_min": 0.6,
        "invalid_tool_rate_max": 0.1,
        "latency_ms_max": 300000,
        "tokens_max": 500000,
        **(thresholds or {}),
    }
    cases: List[Dict[str, Any]] = []
    cases.extend(await _component_cases())
    cases.extend(await _route_cases())
    cases.extend(await _trace_cases(thresholds))
    cases.extend(await _representative_cases())

    retrieval_cases = [case for case in cases if case["category"] == "retrieval"]
    retrieval_pass = [case for case in retrieval_cases if case["passed"]]
    aggregate_checks = {
        "case_count_ok": len(cases) >= 30,
        "retrieval_recall_ok": (len(retrieval_pass) / len(retrieval_cases)) >= thresholds["retrieval_recall_min"]
        if retrieval_cases
        else False,
    }
    failures = [case for case in cases if case.get("gate", True) and not case["passed"]]
    aggregate_failures = [name for name, ok in aggregate_checks.items() if not ok]
    return {
        "success": not failures and not aggregate_failures,
        "num_cases": len(cases),
        "thresholds": thresholds,
        "aggregate": {"checks": aggregate_checks},
        "failures": failures,
        "aggregate_failures": aggregate_failures,
        "evidence_baseline": _evidence_baseline(cases),
        "cases": cases,
    }
