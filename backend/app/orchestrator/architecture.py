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


RUNTIME_MAIN_PATH: Tuple[Dict[str, str], ...] = (
    {
        "stage": RuntimeStage.INTENT_ROUTING.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "classify write/edit/continue/plan/review from one user turn",
    },
    {
        "stage": RuntimeStage.CONTEXT_PLAN.value,
        "owner": ControlOwner.WORKFLOW.value,
        "responsibility": "select context sources, budget, degradation, and trace references",
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
        "name": "context_preparation",
        "current": "ContextMixin._prepare_writer_context + Writer.gather_context_via_tools",
        "target": "ContextPlan service",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "plan_execution",
        "current": "Orchestrator.create_plan / execute_plan / _run_plan_step",
        "target": "Plan application service",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "finalize_analysis",
        "current": "AnalysisMixin._analyze_content and canon persistence helpers",
        "target": "Finalize pipeline",
        "owner": ControlOwner.WORKFLOW.value,
    },
    {
        "name": "isolated_tasks",
        "current": "tool loop, plan research, compact, creative memory extraction",
        "target": "AgentTask worker boundary",
        "owner": ControlOwner.WORKER.value,
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


def runtime_main_path() -> List[Dict[str, str]]:
    """Return a JSON-safe copy of the P0 runtime path."""

    return [dict(item) for item in RUNTIME_MAIN_PATH]


def service_boundaries() -> List[Dict[str, str]]:
    """Return pending service extraction boundaries for P0 handoff."""

    return [dict(item) for item in SERVICE_BOUNDARIES]


def memory_asset_boundaries() -> Dict[str, Dict[str, str]]:
    """Return canon / memory / memory_pack ownership boundaries."""

    return {name: dict(boundary) for name, boundary in MEMORY_ASSET_BOUNDARIES.items()}


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
