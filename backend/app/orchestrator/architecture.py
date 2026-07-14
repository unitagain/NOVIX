# -*- coding: utf-8 -*-
"""
P0 architecture contract for the writing runtime.

This module is intentionally declarative: it freezes the terms used by
``plan.md`` so orchestration changes can be reviewed against a small, testable
contract instead of scattered comments.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Tuple


class ControlOwner(str, Enum):
    """Runtime control owner / 运行时控制方。"""

    WORKFLOW = "workflow"
    AGENT = "agent"
    WORKER = "worker"
    PERMISSION_GATE = "permission_gate"


class RuntimeStage(str, Enum):
    """Canonical runtime stages / 主路径阶段。"""

    INTENT_ROUTING = "intent_routing"
    CONTEXT_PLAN = "context_plan"
    WRITER_AGENT = "writer_agent"
    PLAN_WORKFLOW = "plan_workflow"
    PERMISSION_GATE = "permission_gate"
    WRITE_BACK = "write_back"
    COMPRESS = "compress"
    FALLBACK_WORKFLOW = "fallback_workflow"


class RuntimeService(str, Enum):
    """P11 service owners for the main turn path."""

    TURN_RUNTIME = "TurnRuntime"
    CONTEXT_PLANNING = "ContextPlanningService"
    CONTEXT_ASSEMBLY = "ContextAssemblyService"
    WRITING = "WritingService"
    COMMIT = "CommitCoordinator"
    POST_TURN = "PostTurnService"


RUNTIME_MAIN_PATH: Tuple[Dict[str, str], ...] = (
    {
        "stage": RuntimeStage.INTENT_ROUTING.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "classify write/edit/continue/plan/review from one user turn",
    },
    {
        "stage": RuntimeStage.CONTEXT_PLAN.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "select fresh context sources, tool/skill loadout, trust labels, budget, degradation, and trace references",
    },
    {
        "stage": RuntimeStage.WRITER_AGENT.value,
        "owner": ControlOwner.AGENT.value,
        "responsibility": "use retrieval and writing tools for write/edit/continue turns",
    },
    {
        "stage": RuntimeStage.PLAN_WORKFLOW.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "create and serially execute multi-step writing plans",
    },
    {
        "stage": RuntimeStage.PERMISSION_GATE.value,
        "owner": ControlOwner.PERMISSION_GATE.value,
        "responsibility": "apply allow/ask/deny before high-impact writes are committed",
    },
    {
        "stage": RuntimeStage.WRITE_BACK.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "persist draft, canon, memory, memory_pack, plan, and trace updates",
    },
    {
        "stage": RuntimeStage.COMPRESS.value,
        "owner": ControlOwner.WORKER.value,
        "responsibility": "compact sessions, tool results, summaries, and long traces",
    },
)


SERVICE_BOUNDARIES: Tuple[Dict[str, str], ...] = (
    {
        "name": "turn_runtime",
        "current": "TurnRuntime explicit state machine",
        "target": "TurnRuntime explicit state machine",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "context_preparation",
        "current": "ContextPlanningService + ContextAssemblyService",
        "target": "ContextPlanningService + ContextAssemblyService",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "writing_execution",
        "current": "WritingService",
        "target": "WritingService",
        "owner": ControlOwner.AGENT.value,
    },
    {
        "name": "commit_coordination",
        "current": "CommitCoordinator for orchestrated draft write-back",
        "target": "CommitCoordinator",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "post_turn",
        "current": "PostTurnService",
        "target": "PostTurnService",
        "owner": ControlOwner.WORKER.value,
    },
    {
        "name": "compact_lifecycle",
        "current": "CompactArtifactV2 deterministic/semantic verification + recoverable epochs",
        "target": "Verified and recoverable context projection",
        "owner": ControlOwner.WORKER.value,
    },
    {
        "name": "memory_lifecycle",
        "current": "MemoryLifecycleService + MemoryRecordV2",
        "target": "Governed lifecycle and conflict-isolated recall",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "plan_execution",
        "current": "PlanExecutionService",
        "target": "PlanExecutionService",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "finalize_analysis",
        "current": "FinalizePipeline delegates analysis to AnalysisMixin",
        "target": "FinalizePipeline + incremental AnalysisMixin extraction",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "isolated_tasks",
        "current": "WorkerTaskService + AgentTaskRunner; plan research, session agent-task, and untrusted content review use worker boundary",
        "target": "WorkerTaskService for retrieve/review/summarize/memory_extract/untrusted content",
        "owner": ControlOwner.WORKER.value,
    },
    {
        "name": "retrieval_pipeline",
        "current": "ContextSelectEngine facade over candidate/filter/index/vector/fusion/trace components",
        "target": "Composable retrieval pipeline",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "benchmark_pipeline",
        "current": "Harness facade over artifacts/models/statistics/report and P12 context A/B modules",
        "target": "Composable benchmark pipeline",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "eval_campaign",
        "current": "CampaignRunner + CampaignStore + release quality manifest",
        "target": "Resumable budgeted cross-provider evidence campaign",
        "owner": ControlOwner.WORKER.value,
    },
    {
        "name": "provider_reliability",
        "current": "Gateway reliability controller + total deadline + SQLite idempotency + content-free egress ledger",
        "target": "Timeout/retry/rate-limit/circuit/idempotency/capability control",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "durable_jobs",
        "current": "DurableTaskQueue + SQLite WAL + heartbeat/fencing/cancellation/retry/dead-letter worker",
        "target": "Restart-safe background execution",
        "owner": ControlOwner.WORKER.value,
    },
    {
        "name": "control_plane",
        "current": "SQLiteControlStore owns schema migration, jobs, leases, idempotency and revisions",
        "target": "Single-host transactional control plane separated from content files",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "operations",
        "current": "Generation-consistent ProjectMaintenanceService + revision-bound ReleaseGate",
        "target": "Verified backup/restore/migration/corruption/release controls",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "observability",
        "current": "OpenTelemetry SDK pipeline + privacy-safe file/OTLP exporter + windowed SLO evaluator",
        "target": "Standard traces, low-cardinality metrics, error budgets and actionable alerts",
        "owner": ControlOwner.WORKFLOW.value,
    },
)


MEMORY_ASSET_BOUNDARIES: Dict[str, Dict[str, str]] = {
    "canon": {
        "scope": "story truth",
        "writes": "facts, relations, timeline, character state",
        "constraint": "strong consistency; AI-derived entries require review before high trust",
    },
    "memory": {
        "scope": "creative soft knowledge",
        "writes": "preferences, decisions, progress, constraints",
        "constraint": "auditable and scoped; do not store objective story facts here",
    },
    "memory_pack": {
        "scope": "chapter working context",
        "writes": "working memory, evidence pack, unresolved gaps, trace summary",
        "constraint": "local to a chapter/task; do not store long-term author preference here",
    },
}


MAINTENANCE_FREEZE_POLICIES: Tuple[Dict[str, str], ...] = (
    {
        "area": "eval",
        "policy": "diagnostic_only",
        "expansion_gate": "concrete_diagnostic_requirement",
        "refactor_gate": "touched_stage_boundary_with_contract_tests",
    },
    {
        "area": "durable_queue",
        "policy": "existing_consumers_only",
        "expansion_gate": "explicit_cross_restart_business_requirement",
        "refactor_gate": "new_consumer_contract_and_recovery_evidence",
    },
    {
        "area": "legacy_fallback",
        "policy": "compatibility_safety_only",
        "expansion_gate": "no_new_context_features",
        "refactor_gate": "observable_parity_with_agentic_path",
    },
    {
        "area": "permission_policy",
        "policy": "code_owned_static_policy",
        "expansion_gate": "validated_user_configuration_need",
        "refactor_gate": "migration_and_precedence_contract",
    },
)


def runtime_main_path() -> List[Dict[str, str]]:
    """Return a JSON-safe copy of the P0 runtime path."""

    return [dict(item) for item in RUNTIME_MAIN_PATH]


def service_boundaries() -> List[Dict[str, str]]:
    """Return pending service extraction boundaries for P0 handoff."""

    return [dict(item) for item in SERVICE_BOUNDARIES]


def memory_asset_boundaries() -> Dict[str, Dict[str, str]]:
    """Return canon / memory / memory_pack ownership boundaries."""

    return {name: dict(boundary) for name, boundary in MEMORY_ASSET_BOUNDARIES.items()}


def maintenance_freeze_policies() -> List[Dict[str, str]]:
    """Return explicit YAGNI gates for low-usage maintenance surfaces."""

    return [dict(item) for item in MAINTENANCE_FREEZE_POLICIES]


def route_contract(
    action: str,
    *,
    fallback: bool = False,
    auto_execute_plan: bool = False,
) -> Dict[str, Any]:
    """Build the observable route contract for a chat turn.

    The contract is additive metadata for debugging and handoff. It must not be
    used as an authorization decision; write gates still live in permissions.
    """

    normalized = str(action or "write").strip() or "write"
    if fallback:
        path = "fallback_workflow"
        stages = [RuntimeStage.INTENT_ROUTING, RuntimeStage.FALLBACK_WORKFLOW]
    elif normalized == "plan":
        path = "plan_workflow"
        stages = [RuntimeStage.INTENT_ROUTING, RuntimeStage.PLAN_WORKFLOW]
        if auto_execute_plan:
            stages.append(RuntimeStage.PERMISSION_GATE)
    else:
        path = "agentic_writer"
        stages = [RuntimeStage.INTENT_ROUTING, RuntimeStage.CONTEXT_PLAN, RuntimeStage.WRITER_AGENT]

    stage_rows = {item["stage"]: item for item in RUNTIME_MAIN_PATH}
    if RuntimeStage.FALLBACK_WORKFLOW.value not in stage_rows:
        stage_rows[RuntimeStage.FALLBACK_WORKFLOW.value] = {
            "stage": RuntimeStage.FALLBACK_WORKFLOW.value,
            "owner": ControlOwner.WORKFLOW.value,
            "responsibility": "use mature write/edit workflows when agentic writing is unavailable",
        }

    return {
        "intent": normalized,
        "path": path,
        "fallback": bool(fallback),
        "stages": [dict(stage_rows[stage.value]) for stage in stages],
    }
