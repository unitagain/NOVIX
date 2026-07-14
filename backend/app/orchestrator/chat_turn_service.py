"""Main write/edit/continue/plan turn coordinator."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from app.context_engine.turn_scope import bind_turn_scope, current_turn_scope, new_turn_scope
from app.orchestrator.architecture import route_contract
from app.orchestrator.turn_runtime import TurnState
from app.error_contract import record_degradation


class ChatTurnService:
    """Own the stateful routing path while delegating domain work to services."""

    def __init__(self, owner: Any):
        self.owner = owner

    async def run(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool = False,
        has_draft: bool = False,
        target_word_count: int = 3000,
        auto_execute_plan: bool = False,
        thinking: bool = False,
    ) -> Dict[str, Any]:
        started_at = time.monotonic()
        scope = new_turn_scope(project_id=project_id, chapter_id=chapter)
        scope.context_epoch = await self.owner.session_history.current_context_epoch(project_id)
        self.owner._active_turn_scopes[scope.turn_id] = scope
        try:
            with bind_turn_scope(scope):
                scope.runtime.transition(TurnState.ROUTING)
                self.owner.context_planning_service.prepare_context_plan(
                    scope=scope,
                    project_id=project_id,
                    chapter=chapter,
                    intent="auto",
                    route_path="intent_routing",
                    target_word_count=target_word_count,
                )
                result = await self._run_scoped(
                    project_id,
                    chapter,
                    message,
                    has_selection=has_selection,
                    has_draft=has_draft,
                    target_word_count=target_word_count,
                    auto_execute_plan=auto_execute_plan,
                    thinking=thinking,
                )
                if result.get("cancelled") or scope.cancelled:
                    scope.runtime.cancel()
                elif result.get("incomplete"):
                    scope.runtime.incomplete(str(result.get("reason") or "incomplete"))
                elif result.get("success") is False:
                    scope.runtime.fail(str(result.get("reason") or "turn_failed"))
                else:
                    scope.runtime.complete()
                result["runtime"] = scope.runtime.to_dict()
                from app.observability.runtime_metrics import runtime_metrics

                if result.get("cancelled") or scope.cancelled:
                    metric = "writer.turn.cancelled"
                elif result.get("incomplete"):
                    metric = "writer.turn.incomplete"
                elif result.get("success") is False:
                    metric = "writer.turn.failure"
                else:
                    metric = "writer.turn.success"
                runtime_metrics.increment(metric)
                return result
        except asyncio.CancelledError:
            scope.runtime.cancel("task_cancelled")
            raise
        except Exception as exc:
            scope.runtime.fail(exc)
            from app.observability.runtime_metrics import runtime_metrics

            runtime_metrics.increment("writer.turn.failure")
            raise
        finally:
            from app.observability.runtime_metrics import runtime_metrics

            runtime_metrics.observe("writer.turn.latency_ms", (time.monotonic() - started_at) * 1000.0)
            self.owner._active_turn_scopes.pop(scope.turn_id, None)

    async def _run_scoped(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool,
        has_draft: bool,
        target_word_count: int,
        auto_execute_plan: bool,
        thinking: bool,
    ) -> Dict[str, Any]:
        try:
            self.owner.select_engine.reset_ranking_trace()
        except Exception as exc:
            record_degradation("chat_turn_ranking_trace_reset", exc)
        decision = await self.owner.decide_writing_action(
            project_id,
            chapter,
            message,
            has_selection=has_selection,
            has_draft=has_draft,
        )
        action = str(decision.get("action") or "write")
        scope = current_turn_scope()
        if scope is not None:
            scope.runtime.transition(TurnState.CONTEXT_PLANNING, metadata={"action": action})

        if action == "plan":
            if scope is not None:
                self.owner.context_planning_service.prepare_context_plan(
                    scope=scope,
                    project_id=project_id,
                    chapter=chapter,
                    intent="plan",
                    route_path="plan_workflow",
                    target_word_count=target_word_count,
                    auto_execute_plan=auto_execute_plan,
                )
                scope.runtime.transition(TurnState.PLAN_RUNNING)
            plan = await self.owner.application.plans.create_plan(project_id, goal=message)
            if plan:
                result: Dict[str, Any] = {"success": True, "status": "plan_ready", "plan": plan}
                if auto_execute_plan:
                    result["execution"] = await self.owner.application.plans.execute_plan(project_id, plan["id"])
                return await self.owner.context_planning_service.attach_chat_context_plan(
                    {
                        **result,
                        "action": "plan",
                        "decision": decision,
                        "route_contract": route_contract("plan", auto_execute_plan=auto_execute_plan),
                    },
                    project_id=project_id,
                    chapter=chapter,
                    intent="plan",
                    target_word_count=target_word_count,
                    auto_execute_plan=auto_execute_plan,
                )
            if scope is not None:
                scope.runtime.transition(TurnState.CONTEXT_PLANNING, reason="plan_generation_unavailable")
            action = "write"

        if chapter:
            if scope is not None:
                self.owner.context_planning_service.prepare_context_plan(
                    scope=scope,
                    project_id=project_id,
                    chapter=chapter,
                    intent=action,
                    route_path="agentic_writer",
                    target_word_count=target_word_count,
                )
                scope.runtime.transition(TurnState.WRITER_RUNNING)
            agent_result = await self.owner.writing_service.run(
                project_id,
                chapter,
                message,
                has_selection=has_selection,
                thinking=thinking,
                target_word_count=target_word_count,
            )
            if not agent_result.get("fallback"):
                result_action = str(agent_result.get("action") or action)
                return await self.owner.context_planning_service.attach_chat_context_plan(
                    {
                        **agent_result,
                        "decision": decision,
                        "route_contract": route_contract(result_action),
                    },
                    project_id=project_id,
                    chapter=chapter,
                    intent=result_action,
                    target_word_count=target_word_count,
                )
            if scope is not None:
                scope.ensure_active()
            fallback_reason = str(agent_result.get("reason") or "")
            fallback_context = dict(agent_result.get("fallback_context") or {})
        else:
            fallback_reason = "no_chapter"
            fallback_context = {"schema_version": 1, "reason": fallback_reason, "category": "routing"}

        try:
            from app.observability.usage_diagnostics import record_fallback

            record_fallback(str(fallback_context.get("category") or "unknown"))
        except Exception as exc:
            record_degradation("chat_turn_fallback_diagnostics", exc)

        if scope is not None:
            if scope.runtime.state != TurnState.FALLBACK_RUNNING:
                scope.runtime.transition(
                    TurnState.FALLBACK_RUNNING,
                    reason=fallback_reason,
                    metadata={
                        "category": str(fallback_context.get("category") or "unknown"),
                        "iterations": int(fallback_context.get("iterations") or 0),
                    },
                )
            self.owner.context_planning_service.prepare_context_plan(
                scope=scope,
                project_id=project_id,
                chapter=chapter,
                intent=action,
                route_path="fallback_workflow",
                target_word_count=target_word_count,
                fallback_reason=fallback_reason,
            )
        return await self.owner.context_planning_service.attach_chat_context_plan(
            {
                "success": True,
                "fallback": True,
                "action": action,
                "decision": decision,
                "route_contract": route_contract(action, fallback=True),
                "fallback_context": fallback_context,
            },
            project_id=project_id,
            chapter=chapter,
            intent=action,
            target_word_count=target_word_count,
            fallback_reason=fallback_reason,
        )
