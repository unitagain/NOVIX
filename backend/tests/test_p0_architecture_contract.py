# -*- coding: utf-8 -*-
"""P0 · 架构收敛契约测试。

这些测试不验证业务输出，而是固定 plan.md 中的主路径术语、控制方和待服务化边界，
防止后续改动把 workflow / Agent / worker / memory 边界重新打散。
"""

from app.orchestrator.architecture import (
    ControlOwner,
    RuntimeStage,
    memory_asset_boundaries,
    route_contract,
    runtime_main_path,
    service_boundaries,
)


def test_runtime_main_path_names_control_owners():
    stages = runtime_main_path()
    by_stage = {item["stage"]: item["owner"] for item in stages}

    assert by_stage[RuntimeStage.INTENT_ROUTING.value] == ControlOwner.WORKFLOW.value
    assert by_stage[RuntimeStage.CONTEXT_PLAN.value] == ControlOwner.WORKFLOW.value
    assert by_stage[RuntimeStage.WRITER_AGENT.value] == ControlOwner.AGENT.value
    assert by_stage[RuntimeStage.PLAN_WORKFLOW.value] == ControlOwner.WORKFLOW.value
    assert by_stage[RuntimeStage.PERMISSION_GATE.value] == ControlOwner.PERMISSION_GATE.value
    assert by_stage[RuntimeStage.COMPRESS.value] == ControlOwner.WORKER.value


def test_route_contract_distinguishes_agent_plan_and_fallback():
    agent = route_contract("agentic_write")
    assert agent["path"] == "agentic_writer"
    assert [s["stage"] for s in agent["stages"]] == [
        RuntimeStage.INTENT_ROUTING.value,
        RuntimeStage.CONTEXT_PLAN.value,
        RuntimeStage.WRITER_AGENT.value,
    ]

    plan = route_contract("plan", auto_execute_plan=True)
    assert plan["path"] == "plan_workflow"
    assert RuntimeStage.PERMISSION_GATE.value in [s["stage"] for s in plan["stages"]]

    fallback = route_contract("edit", fallback=True)
    assert fallback["fallback"] is True
    assert fallback["path"] == RuntimeStage.FALLBACK_WORKFLOW.value


def test_service_and_memory_boundaries_are_explicit():
    services = {item["name"]: item for item in service_boundaries()}
    assert {
        "context_preparation",
        "plan_execution",
        "finalize_analysis",
        "isolated_tasks",
        "control_plane",
    } <= set(services)
    assert "ContextPlanningService" in services["context_preparation"]["target"]
    assert services["plan_execution"]["current"] == "PlanExecutionService"
    assert "FinalizePipeline" in services["finalize_analysis"]["current"]
    assert "WorkerTaskService" in services["isolated_tasks"]["current"]
    assert "SQLite" in services["control_plane"]["current"]

    memory = memory_asset_boundaries()
    assert set(memory) == {"canon", "memory", "memory_pack"}
    assert memory["canon"]["scope"] == "story truth"
    assert "long-term author preference" in memory["memory_pack"]["constraint"]
