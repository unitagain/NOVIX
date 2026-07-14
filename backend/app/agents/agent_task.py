# -*- coding: utf-8 -*-
"""
P4 isolated worker contract.
隔离 worker 契约：低耦合任务通过 AgentTask 运行，正文写作仍由单 Writer 主线串行推进。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast

from app.agents.runtime_result import AgentRunResult, worker_agent_run_result
from app.context_engine.trace_collector import trace_collector
from app.context_engine.turn_scope import current_turn_scope
from app.error_contract import DomainError, error_envelope, safe_error_code
from app.context_engine.tool_registry import tool_loadout_for_route, tool_loadout_summary
from app.utils.permissions import decide_permission
from app.utils.trust import detect_prompt_injection, label_untrusted_context, wrap_untrusted_content


class AgentTaskKind(str, Enum):
    """Supported isolated worker task kinds."""

    RETRIEVE = "retrieve"
    REVIEW = "review"
    MEMORY_EXTRACT = "memory_extract"
    SUMMARIZE = "summarize"
    CONSISTENCY_CHECK = "consistency_check"
    UNTRUSTED_EXTRACT = "untrusted_extract"


class AgentTaskStatus(str, Enum):
    """AgentTask lifecycle status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MergePolicy(str, Enum):
    """How worker output may merge back into the main workflow."""

    AUTO = "auto"
    USER_CONFIRM = "user_confirm"
    NO_MERGE = "no_merge"


Handler = Callable[["AgentTask"], Awaitable[Dict[str, Any]] | Dict[str, Any]]


@dataclass
class AgentTask:
    """Serializable isolated worker task."""

    id: str
    kind: str
    input: Dict[str, Any] = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    parent_restrictions: Dict[str, Any] = field(default_factory=dict)
    trust_context: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    trace_ref: Optional[str] = None
    merge_policy: str = MergePolicy.NO_MERGE.value
    status: str = AgentTaskStatus.PENDING.value
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "input": dict(self.input or {}),
            "permissions": list(self.permissions or []),
            "parent_restrictions": dict(self.parent_restrictions or {}),
            "trust_context": dict(self.trust_context or {}),
            "budget": dict(self.budget or {}),
            "output_schema": dict(self.output_schema or {}),
            "trace_ref": self.trace_ref,
            "merge_policy": self.merge_policy,
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }
        if self.status in {
            AgentTaskStatus.COMPLETED.value,
            AgentTaskStatus.FAILED.value,
            AgentTaskStatus.CANCELLED.value,
        }:
            payload["agent_run_result"] = self.to_agent_run_result().to_dict()
        return payload

    def to_agent_run_result(self) -> AgentRunResult:
        error = None
        if self.error:
            error = error_envelope(DomainError(code=self.error), trace_id=str(self.trace_ref or "")).to_dict()
        metrics = dict((self.output or {}).get("metrics") or {})
        return worker_agent_run_result(
            status=self.status,
            output=self.output,
            error=error,
            elapsed_ms=int(metrics.get("duration_ms") or 0),
        )


class AgentTaskRunner:
    """Run isolated worker tasks with permission metadata, trace, and failure isolation."""

    def __init__(self, handlers: Optional[Dict[str, Handler]] = None, *, agent_name: str = "agent_task_worker"):
        self.agent_name = agent_name
        self.handlers: Dict[str, Handler] = {
            AgentTaskKind.RETRIEVE.value: self._handle_retrieve,
            AgentTaskKind.REVIEW.value: self._handle_review,
            AgentTaskKind.MEMORY_EXTRACT.value: self._handle_memory_extract,
            AgentTaskKind.SUMMARIZE.value: self._handle_summarize,
            AgentTaskKind.CONSISTENCY_CHECK.value: self._handle_consistency_check,
            AgentTaskKind.UNTRUSTED_EXTRACT.value: self._handle_untrusted_extract,
        }
        if handlers:
            self.handlers.update(handlers)

    async def run(self, task: AgentTask) -> AgentTask:
        """Execute one worker task. Exceptions are captured on the task."""
        started = time.time()
        scope = current_turn_scope()
        task.status = AgentTaskStatus.RUNNING.value
        task.error = None
        permission_rows = self._permission_rows(task)
        denied = [row for row in permission_rows if row["level"] == "deny"]
        budget_check = self._check_input_budget(task)
        await self._record(task, phase="start", permissions=permission_rows, metrics=budget_check)
        if task.kind not in self.handlers:
            task.status = AgentTaskStatus.FAILED.value
            task.error = f"unsupported_task_kind:{task.kind}"
            await self._record(task, phase="end", permissions=permission_rows, metrics=self._metrics(task, started))
            return task
        if not budget_check["ok"]:
            task.status = AgentTaskStatus.FAILED.value
            task.error = budget_check["error"]
            task.output = {"permission_requests": permission_rows, "budget": budget_check}
            await self._record(task, phase="end", permissions=permission_rows, metrics=self._metrics(task, started))
            return task
        if denied:
            task.status = AgentTaskStatus.FAILED.value
            task.error = "permission_denied"
            task.output = {"permission_requests": permission_rows}
            await self._record(task, phase="end", permissions=permission_rows, metrics=self._metrics(task, started))
            return task

        handler = self.handlers.get(task.kind)
        if not handler:
            task.status = AgentTaskStatus.FAILED.value
            task.error = f"unsupported_task_kind:{task.kind}"
            await self._record(task, phase="end", permissions=permission_rows, metrics=self._metrics(task, started))
            return task

        try:
            if scope is not None:
                scope.ensure_active()
            result = handler(task)
            if inspect.isawaitable(result):
                awaited = cast(Awaitable[Dict[str, Any]], result)
                timeout_ms = int((task.budget or {}).get("timeout_ms") or 0)
                if scope is not None and scope.runtime is not None:
                    result = await scope.runtime.wait_for(
                        awaited,
                        timeout_seconds=timeout_ms / 1000 if timeout_ms > 0 else None,
                    )
                elif timeout_ms > 0:
                    result = await asyncio.wait_for(awaited, timeout=timeout_ms / 1000)
                else:
                    result = await awaited
            resolved_result = cast(Dict[str, Any], result)
            if scope is not None:
                scope.ensure_active()
            task.output = dict(resolved_result or {})
            task.output.setdefault("permission_requests", permission_rows)
            task.output.setdefault("merge_policy", task.merge_policy)
            output_check = self._check_output_budget(task)
            task.output.setdefault("budget", output_check)
            if not output_check["ok"]:
                task.status = AgentTaskStatus.FAILED.value
                task.error = output_check["error"]
            else:
                task.status = AgentTaskStatus.COMPLETED.value
                task.output.setdefault("metrics", self._metrics(task, started))
        except asyncio.TimeoutError:
            task.status = AgentTaskStatus.FAILED.value
            task.error = (
                "turn_deadline_exceeded"
                if scope is not None and scope.runtime is not None and scope.runtime.remaining_seconds <= 0
                else "budget_timeout"
            )
            task.output = {"permission_requests": permission_rows}
        except RuntimeError as exc:
            if str(exc) == "turn_cancelled":
                task.status = AgentTaskStatus.CANCELLED.value
                task.error = "turn_cancelled"
            else:
                task.status = AgentTaskStatus.FAILED.value
                task.error = safe_error_code(exc)
            task.output = {"permission_requests": permission_rows}
        except Exception as exc:
            task.status = AgentTaskStatus.FAILED.value
            task.error = safe_error_code(exc)
            task.output = {"permission_requests": permission_rows}
        if task.output is None:
            task.output = {"permission_requests": permission_rows}
        task.output.setdefault("metrics", self._metrics(task, started))
        await self._record(task, phase="end", permissions=permission_rows, metrics=self._metrics(task, started))
        return task

    async def _record(
        self,
        task: AgentTask,
        *,
        phase: str,
        permissions: List[Dict[str, str]],
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            await trace_collector.record_agent_task(
                self.agent_name,
                {
                    "phase": phase,
                    "task_id": task.id,
                    "kind": task.kind,
                    "status": task.status,
                    "merge_policy": task.merge_policy,
                    "permissions": permissions,
                    "budget": dict(task.budget or {}),
                    "metrics": dict(metrics or {}),
                    "error": task.error,
                },
            )
            task.trace_ref = f"trace:event:agent_task:{task.id}"
        except Exception:
            return

    @staticmethod
    def _permission_rows(task: AgentTask) -> List[Dict[str, str]]:
        return [
            {
                "operation": str(operation),
                "level": decide_permission(
                    str(operation),
                    resource_scope={"task_id": task.id, "kind": task.kind},
                    payload=task.input,
                    trust_context=task.trust_context,
                    parent_restrictions=task.parent_restrictions,
                    # AgentTask handlers produce isolated artifacts; they do not consume
                    # approval or execute the downstream side effect themselves.
                    actor="worker_proposal",
                ).level.value,
            }
            for operation in (task.permissions or [])
        ]

    @staticmethod
    def _json_len(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, default=str))

    def _check_input_budget(self, task: AgentTask) -> Dict[str, Any]:
        budget = dict(task.budget or {})
        input_chars = self._json_len(task.input or {})
        max_input = int(budget.get("max_input_chars") or 0)
        if max_input > 0 and input_chars > max_input:
            return {"ok": False, "error": "budget_input_chars_exceeded", "input_chars": input_chars, "max_input_chars": max_input}
        return {"ok": True, "input_chars": input_chars, "max_input_chars": max_input}

    def _check_output_budget(self, task: AgentTask) -> Dict[str, Any]:
        budget = dict(task.budget or {})
        output_chars = self._json_len(task.output or {})
        max_output = int(budget.get("max_output_chars") or 0)
        if max_output > 0 and output_chars > max_output:
            return {"ok": False, "error": "budget_output_chars_exceeded", "output_chars": output_chars, "max_output_chars": max_output}
        return {"ok": True, "output_chars": output_chars, "max_output_chars": max_output}

    def _metrics(self, task: AgentTask, started: float) -> Dict[str, Any]:
        return {
            "duration_ms": int((time.time() - started) * 1000),
            "input_chars": self._json_len(task.input or {}),
            "output_chars": self._json_len(task.output or {}),
        }

    @staticmethod
    def _handle_retrieve(task: AgentTask) -> Dict[str, Any]:
        query = str(task.input.get("query") or "").strip().lower()
        candidates = task.input.get("candidates") or []
        hits: List[Dict[str, Any]] = []
        for item in candidates:
            text = str(item.get("text") if isinstance(item, dict) else item)
            if not query or query in text.lower():
                hits.append(item if isinstance(item, dict) else {"text": text})
        limit = int(task.input.get("limit") or len(hits) or 0)
        max_items = int((task.budget or {}).get("max_items") or 0)
        if max_items > 0:
            limit = min(limit, max_items)
        return {"hits": hits[: max(0, limit)], "evidence": hits[: max(0, limit)]}

    @staticmethod
    def _handle_review(task: AgentTask) -> Dict[str, Any]:
        content = str(task.input.get("content") or "")
        issues: List[Dict[str, Any]] = []
        if not content.strip():
            issues.append({"severity": "high", "message": "empty_content"})
        return {"issues": issues, "changed": False}

    @staticmethod
    def _handle_memory_extract(task: AgentTask) -> Dict[str, Any]:
        text = str(task.input.get("feedback") or task.input.get("text") or "").strip()
        if not text:
            return {"candidates": []}
        return {
            "candidates": [
                {
                    "type": task.input.get("type") or "preference",
                    "scope": task.input.get("scope") or "project",
                    "content": text[:500],
                    "source": task.input.get("source") or "agent_task",
                    "confidence": 0.6,
                    "status": "needs_review",
                }
            ]
        }

    @staticmethod
    def _handle_summarize(task: AgentTask) -> Dict[str, Any]:
        text = str(task.input.get("text") or "")
        max_chars = int(task.input.get("max_chars") or 600)
        summary = text.strip()
        if len(summary) > max_chars:
            if max_chars <= 3:
                summary = summary[:max_chars]
            else:
                summary = summary[: max_chars - 3].rstrip() + "..."
        return {"summary": summary}

    @staticmethod
    def _handle_consistency_check(task: AgentTask) -> Dict[str, Any]:
        conflicts = task.input.get("conflicts") or []
        return {"conflicts": list(conflicts), "ok": not bool(conflicts)}

    @staticmethod
    def _handle_untrusted_extract(task: AgentTask) -> Dict[str, Any]:
        content = str(task.input.get("content") or task.input.get("text") or "")
        source = task.input.get("source") or task.input.get("url") or "external"
        source_type = task.input.get("source_type") or "external"
        metadata = label_untrusted_context(content, source=source, source_type=source_type)
        detection = detect_prompt_injection(content)
        loadout = tool_loadout_for_route(
            "worker:untrusted_extract",
            trust_context={"consumed_untrusted": True, "trust_label": "untrusted"},
            read_only_only=True,
        )
        return {
            "trust": metadata,
            "injection": detection,
            "wrapped_content": wrap_untrusted_content(content, source=source, source_type=source_type),
            "read_only_loadout": loadout,
            "tool_loadout_summary": tool_loadout_summary(loadout),
            "write_policy": "no_direct_write; downstream write operations require ask and review",
        }
