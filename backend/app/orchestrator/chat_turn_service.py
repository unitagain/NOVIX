"""Main write/edit/continue/plan turn coordinator."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from app.context_engine.turn_scope import bind_turn_scope, current_turn_scope, new_turn_scope
from app.orchestrator.architecture import route_contract
from app.orchestrator.turn_runtime import TurnState
from app.orchestrator.runtime_contracts import ChatTurnOwnerPort, ChatTurnResult, WritingResult
from app.error_contract import record_degradation


class ChatTurnService:
    """Own the stateful routing path while delegating domain work to services."""

    def __init__(self, owner: ChatTurnOwnerPort):
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
        reasoning_level: str = "off",
        selection_text: str = "",
    ) -> ChatTurnResult:
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
                    reasoning_level=reasoning_level,
                    selection_text=selection_text,
                )
                terminal_state = str(result.get("terminal_state") or "")
                if result.get("cancelled") or scope.cancelled:
                    scope.runtime.cancel()
                elif terminal_state in {"requires_input", "incomplete"} or result.get(
                    "incomplete"
                ):
                    scope.runtime.incomplete(str(result.get("reason") or "incomplete"))
                elif terminal_state == "failed" or result.get("success") is False:
                    scope.runtime.fail(str(result.get("reason") or "turn_failed"))
                else:
                    scope.runtime.complete()
                result["runtime"] = scope.runtime.to_dict()
                from app.observability.runtime_metrics import runtime_metrics

                if terminal_state == "cancelled" or result.get("cancelled") or scope.cancelled:
                    metric = "writer.turn.cancelled"
                elif terminal_state in {"requires_input", "incomplete"} or result.get(
                    "incomplete"
                ):
                    metric = "writer.turn.incomplete"
                elif terminal_state == "failed" or result.get("success") is False:
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
        reasoning_level: str,
        selection_text: str = "",
    ) -> ChatTurnResult:
        # Kept in the API for compatibility; storage and the Writer tool loop
        # are authoritative.
        del has_draft, selection_text
        try:
            self.owner.select_engine.reset_ranking_trace()
        except Exception as exc:
            record_degradation("chat_turn_ranking_trace_reset", exc)
        backend_has_draft = False
        current_text = ""
        if chapter:
            current_text, working_path = await self.owner.draft_storage.get_working_text(project_id, chapter)
            backend_has_draft = working_path is not None and bool(current_text.strip())
        decision = await self.owner.decide_writing_action(
            project_id,
            chapter,
            message,
            has_selection=has_selection,
            has_draft=backend_has_draft,
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

        writer_options: Dict[str, Any] = {
            "has_selection": has_selection,
            "thinking": thinking,
            "target_word_count": target_word_count,
        }
        if reasoning_level not in {"auto", "off"}:
            writer_options["reasoning_level"] = reasoning_level
        agent_result: WritingResult = await self.owner.writing_service.run(
            project_id, chapter, message, **writer_options
        )
        chapter_target = agent_result.get("chapter_target")
        result_chapter = (
            str(chapter_target.get("chapter") or "")
            if isinstance(chapter_target, dict)
            else str(chapter or "")
        )
        auto_commit = agent_result.get("auto_commit")
        if isinstance(auto_commit, dict) and auto_commit.get("committed") and result_chapter:
            turn_effect = agent_result.get("turn_effect")
            if isinstance(turn_effect, dict):
                try:
                    canon_sync = await self.owner.application.analysis.apply_turn_effect(
                        project_id,
                        result_chapter,
                        turn_effect,
                    )
                except Exception as exc:
                    record_degradation("created_chapter_turn_effect_sync", exc)
                    canon_sync = {"success": False, "reason": "turn_effect_sync_failed"}
                auto_commit["canon_sync"] = canon_sync
        await self._attach_writing_memory(
            agent_result,
            project_id=project_id,
            chapter=result_chapter,
        )
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

    async def _attach_writing_memory(
        self,
        result: WritingResult,
        *,
        project_id: str,
        chapter: str,
    ) -> None:
        """Attach persisted memory status and the context supply used by this completed turn."""
        storage = getattr(self.owner, "memory_pack_storage", None)
        read_pack = getattr(storage, "read_pack", None)
        build_status = getattr(storage, "build_status", None)
        if not callable(read_pack) or not callable(build_status):
            return
        try:
            pack = await read_pack(project_id, chapter)
            status = dict(build_status(chapter, pack) or {})
            supply = dict(result.get("context_supply") or {})
            status["turn_context"] = {
                "available": list(supply.get("available") or []),
                "retrieved": list(supply.get("retrieved") or []),
                "used": list(supply.get("used") or []),
                "omitted": list(supply.get("omitted") or []),
            }
            result["writing_memory"] = status
        except Exception as exc:
            record_degradation("agentic_writing_memory_status", exc)
