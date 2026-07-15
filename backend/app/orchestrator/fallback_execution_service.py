"""Backend-owned execution for agent fallback decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Dict, cast

from app.error_contract import safe_error_code
from app.orchestrator.architecture import route_contract
from app.orchestrator.fallback_contracts import (
    FallbackDecisionV1,
    FallbackExecutionResultV1,
    FallbackTerminalState,
)
from app.orchestrator.runtime_contracts import ChatTurnResult, FallbackOwnerPort, PendingActionPort
from app.utils.permissions import decide_permission, stable_fingerprint


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FallbackExecutionService:
    """Own fallback permission, execution, terminal projection, and retry safety."""

    def __init__(self, *, owner: FallbackOwnerPort, pending_actions: PendingActionPort) -> None:
        self.owner = owner
        self.pending_actions = pending_actions

    async def execute(
        self,
        *,
        project_id: str,
        chapter: str,
        message: str,
        action: str,
        fallback_context: Dict[str, Any],
        target_word_count: int,
        approval_action_id: str = "",
        approval_token: str = "",
    ) -> ChatTurnResult:
        decision = await self._decision(
            project_id=project_id,
            chapter=chapter,
            message=message,
            action=action,
            fallback_context=fallback_context,
            target_word_count=target_word_count,
        )
        if not chapter:
            return self._projection(
                decision,
                FallbackExecutionResultV1(
                    decision_fingerprint=decision.request_fingerprint,
                    terminal_state=FallbackTerminalState.REQUIRES_INPUT.value,
                    executed=False,
                    reason="chapter_required",
                ),
            )

        if decision.category == "deadline":
            return self._projection(
                decision,
                FallbackExecutionResultV1(
                    decision_fingerprint=decision.request_fingerprint,
                    terminal_state=FallbackTerminalState.INCOMPLETE.value,
                    executed=False,
                    reason="deadline_exhausted",
                ),
            )

        if action in {"edit", "continue"} and not await self._has_draft(project_id, chapter):
            return self._projection(
                decision,
                FallbackExecutionResultV1(
                    decision_fingerprint=decision.request_fingerprint,
                    terminal_state=FallbackTerminalState.REQUIRES_INPUT.value,
                    executed=False,
                    reason="draft_required",
                ),
            )

        if decision.permission == "deny":
            return self._projection(
                decision,
                FallbackExecutionResultV1(
                    decision_fingerprint=decision.request_fingerprint,
                    terminal_state=FallbackTerminalState.FAILED.value,
                    executed=False,
                    reason="permission_denied",
                ),
            )

        if decision.permission == "ask":
            if not (approval_action_id and approval_token):
                pending = await self._pending_action(project_id, decision)
                decision = replace(decision, approval_action_id=str(pending.get("id") or ""))
                return self._projection(
                    decision,
                    FallbackExecutionResultV1(
                        decision_fingerprint=decision.request_fingerprint,
                        terminal_state=FallbackTerminalState.REQUIRES_APPROVAL.value,
                        executed=False,
                        reason="permission_approval_required",
                        approval_action_id=decision.approval_action_id,
                    ),
                    pending_action=self._pending_projection(pending),
                )
            try:
                approved = await self.pending_actions.consume_action(
                    project_id,
                    action_id=approval_action_id,
                    token=approval_token,
                    operation=decision.operation,
                    target=decision.resource,
                    payload=self._approval_payload(decision),
                )
            except ValueError as exc:
                reason = safe_error_code(exc)
                return self._projection(
                    replace(decision, approval_action_id=approval_action_id),
                    FallbackExecutionResultV1(
                        decision_fingerprint=decision.request_fingerprint,
                        terminal_state=FallbackTerminalState.INCOMPLETE.value,
                        executed=False,
                        reason=reason,
                        approval_action_id=approval_action_id,
                        idempotency_replayed=reason.startswith("action_not_pending"),
                    ),
                )
            decision = replace(decision, approval_action_id=str(approved.get("id") or approval_action_id))

        legacy_result = await self._execute_legacy(
            project_id=project_id,
            chapter=chapter,
            message=message,
            action=action,
            target_word_count=target_word_count,
        )
        terminal, reason = self._terminal_state(legacy_result)
        revision = await self._revision(project_id, chapter)
        execution = FallbackExecutionResultV1(
            decision_fingerprint=decision.request_fingerprint,
            terminal_state=terminal,
            executed=True,
            result_ref=self._result_ref(project_id, chapter, legacy_result),
            revision=revision,
            reason=reason,
            approval_action_id=decision.approval_action_id,
        )
        return self._projection(decision, execution, legacy_result=legacy_result)

    async def _decision(
        self,
        *,
        project_id: str,
        chapter: str,
        message: str,
        action: str,
        fallback_context: Dict[str, Any],
        target_word_count: int,
    ) -> FallbackDecisionV1:
        normalized_action = str(action or "write").strip().lower() or "write"
        operation = "edit_lines" if normalized_action == "edit" else "write_content"
        resource = {"project_id": str(project_id), "chapter": str(chapter), "action": normalized_action}
        revision = await self._revision(project_id, chapter)
        request_fingerprint = _fingerprint(
            {
                "resource": resource,
                "message": str(message or ""),
                "target_word_count": int(target_word_count),
                "revision": revision,
                "fallback_reason": str(fallback_context.get("reason") or "unknown"),
            }
        )
        approval_payload = {
            "request_fingerprint": request_fingerprint,
            "revision": revision,
            "category": str(fallback_context.get("category") or "unknown"),
        }
        permission = decide_permission(operation, resource_scope=resource, payload=approval_payload)
        return FallbackDecisionV1(
            action=normalized_action,
            reason=str(fallback_context.get("reason") or "unknown"),
            category=str(fallback_context.get("category") or "unknown"),
            operation=operation,
            resource=resource,
            request_fingerprint=request_fingerprint,
            resource_fingerprint=stable_fingerprint(resource),
            payload_fingerprint=stable_fingerprint(approval_payload),
            revision=revision,
            permission=permission.level.value,
            iterations=int(fallback_context.get("iterations") or 0),
            source_types=tuple(sorted(str(value) for value in fallback_context.get("source_types") or [])),
            artifact_refs=tuple(sorted(str(value) for value in fallback_context.get("artifact_refs") or [])),
            degradation_types=tuple(
                sorted(str(value) for value in fallback_context.get("degradation_types") or [])
            ),
        )

    async def _pending_action(self, project_id: str, decision: FallbackDecisionV1) -> Dict[str, Any]:
        expected = decide_permission(
            decision.operation,
            resource_scope=decision.resource,
            payload=self._approval_payload(decision),
        ).fingerprint
        for item in await self.pending_actions.list_actions(project_id, status="pending"):
            if str(item.get("operation") or "") == decision.operation and str(
                item.get("decision_fingerprint") or ""
            ) == expected:
                return item
        return await self.pending_actions.create_action(
            project_id,
            operation=decision.operation,
            target=decision.resource,
            payload=self._approval_payload(decision),
            reason=decision.category,
        )

    @staticmethod
    def _approval_payload(decision: FallbackDecisionV1) -> Dict[str, Any]:
        return {
            "request_fingerprint": decision.request_fingerprint,
            "revision": dict(decision.revision),
            "category": decision.category,
        }

    async def _execute_legacy(
        self,
        *,
        project_id: str,
        chapter: str,
        message: str,
        action: str,
        target_word_count: int,
    ) -> Dict[str, Any]:
        if action == "write":
            operation = lambda: self.owner.start_session(
                project_id=project_id,
                chapter=chapter,
                chapter_title=chapter,
                chapter_goal=message,
                target_word_count=target_word_count,
            )
        else:
            operation = lambda: self.owner.process_feedback(
                project_id=project_id,
                chapter=chapter,
                feedback=message,
                action="revise",
            )
        result = await self.owner.application.commands.run(
            project_id=project_id,
            chapter=chapter,
            intent=action,
            route_path="fallback_workflow",
            target_word_count=target_word_count,
            operation=operation,
        )
        return dict(result or {})

    async def _has_draft(self, project_id: str, chapter: str) -> bool:
        return bool(await self.owner.draft_storage.list_draft_versions(project_id, chapter))

    async def _revision(self, project_id: str, chapter: str) -> Dict[str, Any]:
        if not chapter:
            return {"version": "", "revision": 0, "fingerprint": ""}
        versions = await self.owner.draft_storage.list_draft_versions(project_id, chapter)
        version = str(versions[-1]) if versions else "v1"
        row = self.owner.draft_storage.get_draft_revision(project_id, chapter, version)
        return {
            "version": version,
            "revision": int(row.get("revision") or 0),
            "fingerprint": str(row.get("fingerprint") or ""),
        }

    @staticmethod
    def _terminal_state(result: Dict[str, Any]) -> tuple[str, str]:
        if result.get("cancelled"):
            return FallbackTerminalState.CANCELLED.value, str(result.get("reason") or "cancelled")
        status = str(result.get("status") or "").lower()
        if "waiting_user_input" in status or result.get("questions"):
            return FallbackTerminalState.REQUIRES_INPUT.value, "questions_required"
        if result.get("incomplete") or "maximum iterations" in str(result.get("error") or "").lower():
            return FallbackTerminalState.INCOMPLETE.value, str(result.get("reason") or "iteration_limit")
        if result.get("success") is False:
            return FallbackTerminalState.FAILED.value, str(result.get("reason") or "fallback_execution_failed")
        return FallbackTerminalState.COMPLETED.value, ""

    @staticmethod
    def _result_ref(project_id: str, chapter: str, result: Dict[str, Any]) -> str:
        for key in ("draft_v2", "draft_v1", "draft"):
            draft = result.get(key)
            version = str(getattr(draft, "version", "") or (draft.get("version") if isinstance(draft, dict) else ""))
            if version:
                return f"draft://{project_id}/{chapter}/{version}"
        version = str(result.get("version") or "")
        return f"draft://{project_id}/{chapter}/{version}" if version else ""

    @staticmethod
    def _pending_projection(action: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(action.get("id") or ""),
            "operation": str(action.get("operation") or ""),
            "status": str(action.get("status") or "pending"),
            "token": str(action.get("token") or ""),
            "decision_fingerprint": str(action.get("decision_fingerprint") or ""),
            "expires_at": str(action.get("expires_at") or ""),
        }

    @staticmethod
    def _projection(
        decision: FallbackDecisionV1,
        execution: FallbackExecutionResultV1,
        *,
        pending_action: Dict[str, Any] | None = None,
        legacy_result: Dict[str, Any] | None = None,
    ) -> ChatTurnResult:
        payload = dict(legacy_result or {})
        terminal = execution.terminal_state
        payload.update(
            {
                "success": terminal != FallbackTerminalState.FAILED.value,
                "fallback": False,
                "fallback_executed": execution.executed,
                "action": decision.action,
                "terminal_state": terminal,
                "reason": execution.reason or decision.reason,
                "route_contract": route_contract(decision.action, fallback=True),
                "fallback_decision": decision.to_dict(),
                "fallback_execution": execution.to_dict(),
                "compatibility": {"backend_authoritative": True, "legacy_fallback_signal": False},
            }
        )
        if pending_action:
            payload["pending_action"] = pending_action
        return cast(ChatTurnResult, payload)
