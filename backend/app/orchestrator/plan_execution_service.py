# -*- coding: utf-8 -*-
"""Plan execution service.

P7 抽离点：Plan 创建、串行执行、断点续传和低风险 research worker。
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agents.planner import generate_plan
from app.error_contract import safe_error_code
from app.orchestrator.worker_task_service import WorkerTaskService
from app.utils.logger import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]
TextCallback = Callable[[str, str], str]
CancelledCallback = Callable[[], bool]
StepRunner = Callable[[str, Dict[str, Any]], Awaitable[str]]


class PlanExecutionService:
    """Create and execute serial writing plans."""

    def __init__(
        self,
        *,
        gateway: Any,
        writer: Any,
        draft_storage: Any,
        plan_store: Any,
        select_engine: Any,
        storage_adapter: Any,
        worker_service: WorkerTaskService,
        emit_progress: ProgressCallback,
        translate: TextCallback,
        is_cancelled: CancelledCallback,
        write_step: StepRunner,
        edit_step: StepRunner,
        analyze_step: StepRunner,
    ):
        self.gateway = gateway
        self.writer = writer
        self.draft_storage = draft_storage
        self.plan_store = plan_store
        self.select_engine = select_engine
        self.storage_adapter = storage_adapter
        self.worker_service = worker_service
        self.emit_progress = emit_progress
        self.translate = translate
        self.is_cancelled = is_cancelled
        self.write_step = write_step
        self.edit_step = edit_step
        self.analyze_step = analyze_step

    async def create_plan(self, project_id: str, goal: str, context_hint: str = "") -> Optional[Dict[str, Any]]:
        """Split a complex instruction into a persisted serial plan."""

        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        try:
            existing_chapters = await self.draft_storage.list_chapters(project_id)
        except Exception:
            existing_chapters = []
        steps = await generate_plan(self.gateway, provider, goal, context_hint, chapters=existing_chapters)
        if not steps:
            return None
        plan = {
            "id": f"plan_{int(time.time() * 1000)}",
            "goal": str(goal or "").strip(),
            "steps": steps,
            "status": "planning",
            "created_at": int(time.time() * 1000),
        }
        await self.plan_store.write_plan(project_id, plan)
        await self.emit_progress(
            self.translate("已生成执行计划", "Plan generated"),
            stage="plan_created",
            status="plan",
            plan_id=plan["id"],
            steps=len(steps),
        )
        return plan

    async def execute_plan(
        self, project_id: str, plan_id: str, step_runner: Optional[StepRunner] = None
    ) -> Dict[str, Any]:
        """Execute a plan serially with per-step persistence."""

        plan = await self.plan_store.read_plan(project_id, plan_id)
        if not plan:
            return {"success": False, "error": "plan_not_found"}
        runner = step_runner or self.run_plan_step
        steps = plan.get("steps") or []
        plan["status"] = "running"
        await self.plan_store.write_plan(project_id, plan)

        completed = True
        for step in steps:
            if self.is_cancelled():
                completed = False
                break
            if step.get("status") == "done":
                continue
            await self.emit_progress(
                self.translate(
                    f"计划步骤 {step.get('id')}/{len(steps)}：{step.get('description', '')}",
                    f"Plan step {step.get('id')}/{len(steps)}: {step.get('description', '')}",
                ),
                stage="plan_step",
                status="plan",
                step_id=step.get("id"),
                action=step.get("action"),
            )
            try:
                result = await runner(project_id, step)
                step["status"] = "done"
                if result:
                    step["result"] = str(result)[:500]
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = safe_error_code(exc)
                logger.warning("Plan step %s failed: %s", step.get("id"), exc)
                completed = False
                await self.plan_store.write_plan(project_id, plan)
                break
            await self.plan_store.write_plan(project_id, plan)

        if self.is_cancelled():
            plan["status"] = "interrupted"
        elif completed:
            plan["status"] = "done"
        else:
            plan["status"] = "failed"
        await self.plan_store.write_plan(project_id, plan)
        return {"success": completed, "plan": plan}

    async def run_plan_step(self, project_id: str, step: Dict[str, Any]) -> str:
        """Dispatch one plan step to the existing serial workflow."""

        action = str(step.get("action") or "").strip()
        chapter = str(step.get("chapter") or "").strip()
        description = str(step.get("description") or "").strip()

        if action == "research":
            return f"research: {await self.research_note(project_id, description)}"

        if action in ("edit", "analyze") and chapter:
            try:
                existing = await self.draft_storage.list_chapters(project_id)
            except Exception:
                existing = []
            if existing and chapter not in existing:
                raise ValueError(f"章节 {chapter} 不存在，无法 {action}")

        if action == "write" and chapter:
            return await self.write_step(project_id, step)
        if action == "edit" and chapter:
            return await self.edit_step(project_id, step)
        if action == "analyze" and chapter:
            return await self.analyze_step(project_id, step)
        return f"{action}: {description[:80]}"

    async def research_note(self, project_id: str, query: str) -> str:
        """Run plan research through retrieval + isolated retrieve worker."""

        query = str(query or "").strip()
        if not query:
            return "（空查询）"
        try:
            items = (
                await self.select_engine.retrieval_select(
                    project_id=project_id,
                    query=query,
                    item_types=["fact", "character", "world"],
                    storage=self.storage_adapter,
                    top_k=8,
                )
                or []
            )
        except Exception as exc:
            logger.warning("plan research retrieval failed: %s", exc)
            return f"retrieval_failed:{safe_error_code(exc)}"

        candidates = [
            {
                "id": str(getattr(item, "id", idx)),
                "text": str(getattr(item, "content", "") or "").strip(),
                "type": str(getattr(item, "type", "") or ""),
            }
            for idx, item in enumerate(items)
        ]
        task = await self.worker_service.run_task(
            project_id=project_id,
            kind="retrieve",
            input={"query": query, "candidates": candidates, "limit": 5},
            budget={"max_items": 5, "max_input_chars": 20000, "max_output_chars": 8000},
        )
        hits = ((task.output or {}).get("hits") or []) if task.status == "completed" else []
        parts = [str(hit.get("text") or "").strip()[:120] for hit in hits if str(hit.get("text") or "").strip()]
        return "；".join(parts) if parts else "未检索到直接相关 canon/card。"
