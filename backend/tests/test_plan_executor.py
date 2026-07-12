# -*- coding: utf-8 -*-
"""Phase 11 · Plan 串行执行器编排测试。

用真实 Orchestrator + 注入的 mock step_runner，验证编排逻辑：串行顺序、每步进度持久化、
断点续传（跳过已完成）、用户打断、步骤失败记录。不触真实写作/LLM（runner 被 mock）。
"""

import asyncio

from app.orchestrator.orchestrator import Orchestrator


def _orch(tmp_path):
    return Orchestrator(str(tmp_path))


def _seed(orch, steps, status="planning"):
    plan = {"id": "p1", "goal": "g", "steps": steps, "status": status}
    asyncio.run(orch.plan_store.write_plan("proj", plan))


def test_execute_plan_runs_steps_in_order(tmp_path):
    orch = _orch(tmp_path)
    _seed(
        orch,
        [
            {"id": 1, "action": "research", "description": "查伏笔", "status": "pending"},
            {"id": 2, "action": "write", "description": "写第6章", "chapter": "V1C006", "status": "pending"},
        ],
    )
    calls = []

    async def runner(pid, step):
        calls.append(step["id"])
        return f"ran {step['id']}"

    result = asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    assert result["success"]
    assert calls == [1, 2]  # 串行顺序（红线 1）
    assert result["plan"]["status"] == "done"
    assert all(s["status"] == "done" for s in result["plan"]["steps"])


def test_execute_plan_persists_progress(tmp_path):
    orch = _orch(tmp_path)
    _seed(orch, [{"id": 1, "action": "research", "description": "d", "status": "pending"}])

    async def runner(pid, step):
        return "ok"

    asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    reloaded = asyncio.run(orch.plan_store.read_plan("proj", "p1"))  # 从文件重读
    assert reloaded["status"] == "done"
    assert reloaded["steps"][0]["status"] == "done"
    assert reloaded["steps"][0]["result"] == "ok"


def test_execute_plan_resumes_skipping_done(tmp_path):
    orch = _orch(tmp_path)
    _seed(
        orch,
        [
            {"id": 1, "action": "research", "description": "d1", "status": "done"},
            {"id": 2, "action": "research", "description": "d2", "status": "pending"},
        ],
        status="running",
    )
    calls = []

    async def runner(pid, step):
        calls.append(step["id"])
        return "ok"

    asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    assert calls == [2]  # 断点续传：跳过已完成的 step 1


def test_execute_plan_interrupt(tmp_path):
    orch = _orch(tmp_path)
    _seed(
        orch,
        [
            {"id": 1, "action": "research", "description": "d1", "status": "pending"},
            {"id": 2, "action": "research", "description": "d2", "status": "pending"},
        ],
    )

    async def runner(pid, step):
        orch.cancel_session()
        return "ok"

    result = asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    assert result["plan"]["status"] == "interrupted"
    assert result["plan"]["steps"][0]["status"] == "done"  # 已完成的保留


def test_execute_plan_step_failure_recorded(tmp_path):
    orch = _orch(tmp_path)
    _seed(orch, [{"id": 1, "action": "research", "description": "d", "status": "pending"}])

    async def runner(pid, step):
        raise ValueError("boom")

    result = asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    assert result["plan"]["steps"][0]["status"] == "failed"
    assert "boom" in result["plan"]["steps"][0]["error"]


def test_execute_plan_stops_on_failure(tmp_path):
    """失败即停：某步失败 → 标 failed + 后续步骤不跑 + status=failed + success=False。"""
    orch = _orch(tmp_path)
    _seed(
        orch,
        [
            {"id": 1, "action": "research", "description": "d1", "status": "pending"},
            {"id": 2, "action": "research", "description": "d2", "status": "pending"},
        ],
    )
    calls = []

    async def runner(pid, step):
        calls.append(step["id"])
        if step["id"] == 1:
            raise ValueError("boom")
        return "ok"

    result = asyncio.run(orch.application.plans.execute_plan("proj", "p1", step_runner=runner))
    assert calls == [1]  # 第 2 步未执行（不在坏状态上盲跑）
    assert result["success"] is False
    assert result["plan"]["status"] == "failed"
    assert result["plan"]["steps"][0]["status"] == "failed"
    assert result["plan"]["steps"][1]["status"] == "pending"  # 后续保持未执行


def test_execute_plan_not_found(tmp_path):
    result = asyncio.run(_orch(tmp_path).application.plans.execute_plan("proj", "nope"))
    assert not result["success"]
