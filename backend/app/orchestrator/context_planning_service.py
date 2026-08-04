# -*- coding: utf-8 -*-
"""Context planning service for chat turns.

P7 抽离点：集中负责 ContextPlan 构建、真实成本回填和 trace 落盘。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from app.context_engine.context_plan import ContextPlanV2, build_context_plan_v2
from app.context_engine.trace_collector import TraceCollector, trace_collector
from app.context_engine.turn_scope import TurnScope, current_turn_scope
from app.orchestrator.architecture import route_contract
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContextPlanningService:
    """Attach observable ContextPlan metadata to one chat result."""

    def __init__(
        self,
        *,
        select_engine: Any,
        draft_storage: Any,
        gateway: Any = None,
        tracer: TraceCollector = trace_collector,
    ):
        self.select_engine = select_engine
        self.draft_storage = draft_storage
        self.gateway = gateway
        self.tracer = tracer

    def prepare_context_plan(
        self,
        *,
        scope: TurnScope,
        project_id: str,
        chapter: str,
        intent: str,
        route_path: str,
        target_word_count: int = 3000,
        auto_execute_plan: bool = False,
        agent_name: str = "writer",
    ) -> ContextPlanV2:
        """Build and activate the executable plan before any provider request."""

        profile = self._provider_profile(agent_name)
        plan = build_context_plan_v2(
            turn_id=scope.turn_id,
            project_id=project_id,
            chapter_id=chapter,
            intent=intent,
            route_path=route_path,
            project_root=self.draft_storage.get_project_path(project_id),
            provider_profile=profile,
            target_word_count=target_word_count,
            auto_execute_plan=auto_execute_plan,
            context_epoch=scope.context_epoch,
        )
        scope.activate_plan(plan)
        return plan

    def _provider_profile(self, agent_name: str = "writer") -> Dict[str, Any]:
        profile: Dict[str, Any] = {}
        if self.gateway is not None:
            try:
                profile = dict(self.gateway.get_profile_for_agent(agent_name) or {})
                profile_id = self.gateway.get_provider_for_agent(agent_name)
                profile.setdefault("id", profile_id)
            except Exception:
                profile = {}
        return profile

    async def attach_chat_context_plan(
        self,
        result: Dict[str, Any],
        *,
        project_id: str,
        chapter: str,
        intent: str,
        target_word_count: int,
        auto_execute_plan: bool = False,
        trace_start_index: int = 0,
        trace_started_at: float = 0.0,
    ) -> Dict[str, Any]:
        """Build, enrich, persist, and attach a ContextPlan."""

        route = result.get("route_contract") or route_contract(intent)
        scope = current_turn_scope()
        active_v2 = scope.active_plan if scope is not None else None
        task_id = scope.turn_id if scope is not None else f"chat_{int(time.time() * 1000)}"
        context_plan = active_v2 if isinstance(active_v2, ContextPlanV2) else build_context_plan_v2(
            turn_id=task_id,
            project_id=project_id,
            chapter_id=chapter,
            intent=intent,
            route_path=str(route.get("path") or "agentic_writer"),
            project_root=self.draft_storage.get_project_path(project_id),
            provider_profile=self._provider_profile(),
            target_word_count=target_word_count,
            auto_execute_plan=auto_execute_plan,
            context_epoch=scope.context_epoch if scope is not None else 0,
        )

        ranking_trace: Dict[str, Any] = {}
        try:
            ranking_trace = self.select_engine.get_last_ranking_trace()
        except Exception:
            ranking_trace = {}
        if ranking_trace:
            if scope is not None and scope.turn_trace is not None:
                scope.turn_trace.ranking_actual.append(dict(ranking_trace))

        actual_metrics = (
            self.tracer.summarize_turn(scope)
            if scope is not None
            else self.tracer.summarize_events_since(trace_start_index, started_at=trace_started_at)
        )
        trace_ref = f"traces/{task_id}.json"
        trace_path = self.draft_storage.get_project_path(project_id) / trace_ref
        payload = context_plan.to_dict()
        payload["budget"].update(
            {
                "actual_tokens": int(actual_metrics.get("tokens") or 0),
                "latency_ms": int(actual_metrics.get("elapsed_ms") or 0),
                "tool_calls": int(actual_metrics.get("tool_calls") or 0),
                "llm_requests": int(actual_metrics.get("llm_requests") or 0),
                "llm_responses": int(actual_metrics.get("llm_responses") or 0),
                "trace_events": int(actual_metrics.get("event_count") or 0),
            }
        )
        if ranking_trace:
            payload["ranking"]["actual"] = ranking_trace
        payload["actual"] = actual_metrics
        payload["turn_trace"] = scope.turn_trace.to_dict() if scope is not None and scope.turn_trace else {}
        clarification = result.get("clarification")
        if isinstance(clarification, dict):
            clarification_questions = clarification.get("questions")
            if not isinstance(clarification_questions, list):
                clarification_questions = result.get("questions")
            if not isinstance(clarification_questions, list):
                clarification_questions = []
            payload["clarification"] = {
                "decision": "ask" if clarification_questions else "proceed",
                "reason": str(clarification.get("reason") or "")[:240],
                "question_count": min(3, len(clarification_questions)),
                "tool": "ask_clarification",
            }
        payload["reconciliation"] = {
            "sources": (
                scope.source_registry.reconcile()
                if scope is not None and scope.source_registry is not None
                else {}
            ),
            "requests": (
                scope.turn_trace.to_dict().get("model_requests", []) if scope is not None and scope.turn_trace else []
            ),
        }
        payload["trace_ref"] = trace_ref
        try:
            await self.tracer.record_context_plan("orchestrator", payload)
            saved = await self.tracer.save_trace(
                str(trace_path), events=list(scope.trace_events) if scope is not None else None
            )
            if saved:
                await self.tracer.save_otel(
                    str(trace_path.with_suffix(".otel.json")),
                    events=list(scope.trace_events) if scope is not None else None,
            )
            if not saved:
                payload["trace_ref"] = None
                trace_ref = ""
        except Exception as exc:
            logger.warning("ContextPlan trace persist failed: %s", exc)
            payload["trace_ref"] = None
            trace_ref = ""
        result["context_plan"] = payload
        if trace_ref:
            result["trace_ref"] = trace_ref
        return result
