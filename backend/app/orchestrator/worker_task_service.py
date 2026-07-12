# -*- coding: utf-8 -*-
"""Worker task service.

P7 抽离点：统一 AgentTask 创建、handler 注册、权限、预算和 trace。
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agents.agent_task import AgentTask, AgentTaskRunner, MergePolicy
from app.context_engine.turn_scope import bind_turn_scope, current_turn_scope, new_turn_scope

Handler = Callable[[AgentTask], Dict[str, Any] | Awaitable[Dict[str, Any]]]


class WorkerTaskService:
    """Facade over AgentTaskRunner so callers do not instantiate workers directly."""

    def __init__(self, handlers: Optional[Dict[str, Handler]] = None, *, agent_name: str = "agent_task_worker"):
        self.runner = AgentTaskRunner(handlers=handlers, agent_name=agent_name)

    def register_handler(self, kind: str, handler: Handler) -> None:
        """Register or replace a worker handler without modifying Orchestrator."""

        self.runner.handlers[str(kind)] = handler

    async def run(self, task: AgentTask) -> AgentTask:
        """Run a prepared AgentTask."""

        parent = current_turn_scope()
        if parent is not None:
            return await self.runner.run(task)
        project_id = str((task.input or {}).get("project_id") or "")
        scope = new_turn_scope(project_id=project_id, turn_id=f"worker_{task.id}")
        with bind_turn_scope(scope):
            return await self.runner.run(task)

    async def run_task(
        self,
        *,
        project_id: str,
        kind: str,
        input: Dict[str, Any] | None = None,
        task_id: Optional[str] = None,
        permissions: list[str] | None = None,
        budget: Dict[str, Any] | None = None,
        output_schema: Dict[str, Any] | None = None,
        merge_policy: str = MergePolicy.NO_MERGE.value,
    ) -> AgentTask:
        """Create and run a worker task from API/service inputs."""

        task = AgentTask(
            id=task_id or f"task_{int(time.time() * 1000)}",
            kind=str(kind),
            input={"project_id": project_id, **(input or {})},
            permissions=permissions or [],
            budget=budget or {},
            output_schema=output_schema or {},
            merge_policy=merge_policy,
        )
        return await self.run(task)
