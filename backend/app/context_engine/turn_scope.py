"""Request-scoped runtime state for one user turn.

The scope is propagated with ``contextvars`` so gateway, trace, workers, and
storage-facing orchestration can correlate work without relying on global
event indexes. It contains only runtime metadata; business state remains in
the project storage layer.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class TurnTrace:
    """Mutable execution facts kept separate from frozen context plans."""

    turn_id: str
    trace_id: str
    model_requests: List[Dict[str, Any]] = field(default_factory=list)
    source_verifications: List[Dict[str, Any]] = field(default_factory=list)
    assembly_fingerprints: List[str] = field(default_factory=list)
    ranking_actual: List[Dict[str, Any]] = field(default_factory=list)
    source_usage: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "model_requests": list(self.model_requests),
            "source_verifications": list(self.source_verifications),
            "assembly_fingerprints": list(self.assembly_fingerprints),
            "ranking_actual": list(self.ranking_actual),
            "source_usage": list(self.source_usage),
        }

    def record_model_response(
        self,
        request_id: str,
        usage: Dict[str, Any],
        *,
        provider: str = "",
        model: str = "",
    ) -> None:
        for request in reversed(self.model_requests):
            if request.get("request_id") != request_id:
                continue
            actual = int(usage.get("prompt_tokens") or 0)
            planned = int(request.get("input_tokens") or 0)
            request["actual_input_tokens"] = actual
            request["input_token_delta"] = actual - planned if actual else None
            request["token_reconciliation"] = "provider_reported" if actual else "provider_usage_unavailable"
            request["actual_provider"] = provider
            request["actual_model"] = model
            return

    def reconcile_sources(self, planned_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        tool_source_types = {
            "lookup_card": "canon",
            "query_canon": "canon",
            "query_relations": "relations",
            "read_chapter": "prose",
            "search_prose": "prose",
        }
        actual: set[str] = set()
        for row in self.source_usage:
            source_type = str(row.get("type") or "")
            if source_type == "jit_tool":
                source_type = tool_source_types.get(str(row.get("tool") or ""), "jit_tool")
            if source_type:
                actual.add(source_type)
        planned = {str(row.get("type") or "") for row in planned_sources if row.get("type")}
        return {
            "planned": sorted(planned),
            "actual": sorted(actual),
            "not_selected": sorted(planned - actual),
            "unexpected": sorted(actual - planned),
        }


@dataclass
class TurnScope:
    """Mutable execution envelope whose plans and events are turn-local."""

    turn_id: str
    project_id: str = ""
    chapter_id: str = ""
    trace_id: str = ""
    parent_turn_id: Optional[str] = None
    context_epoch: int = 0
    started_at: float = field(default_factory=time.time)
    cancelled: bool = False
    plans: List[Any] = field(default_factory=list)
    trace_events: List[Any] = field(default_factory=list)
    turn_trace: Optional[TurnTrace] = None
    runtime: Optional[Any] = None

    def __post_init__(self) -> None:
        if not self.trace_id:
            self.trace_id = f"trace_{uuid.uuid4().hex}"
        if self.turn_trace is None:
            self.turn_trace = TurnTrace(turn_id=self.turn_id, trace_id=self.trace_id)
        if self.runtime is None:
            from app.orchestrator.turn_runtime import TurnRuntime

            self.runtime = TurnRuntime(turn_id=self.turn_id)

    @property
    def active_plan(self) -> Any:
        return self.plans[-1] if self.plans else None

    @property
    def model_requests(self) -> List[Dict[str, Any]]:
        return self.turn_trace.model_requests if self.turn_trace is not None else []

    def activate_plan(self, plan: Any) -> None:
        self.plans.append(plan)

    def cancel(self) -> None:
        self.cancelled = True
        if self.runtime is not None:
            self.runtime.cancel()

    def ensure_active(self) -> None:
        if self.cancelled:
            raise RuntimeError("turn_cancelled")

    def prepare_model_request(
        self,
        *,
        messages: List[Dict[str, Any]],
        provider: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        token_accounting: Optional[Dict[str, Any]] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        degradation: Optional[List[Dict[str, Any]]] = None,
        final_payload_fingerprint: str = "",
    ) -> Dict[str, Any]:
        """Validate and register one logical provider request."""

        self.ensure_active()
        plan = self.active_plan
        if plan is not None and hasattr(plan, "validate_request"):
            source_verification = (
                plan.verify_sources() if hasattr(plan, "verify_sources") else {"valid": False, "failures": ["unsupported"]}
            )
            if self.turn_trace is not None:
                self.turn_trace.source_verifications.append(source_verification)
            if source_verification.get("valid") is not True:
                failure = (source_verification.get("failures") or [{}])[0]
                raise RuntimeError(f"context_source_revision_unavailable:{failure.get('path') or failure.get('reason')}")
            record = plan.validate_request(
                messages=messages,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                token_accounting=token_accounting,
                capabilities=capabilities,
                degradation=degradation,
                final_payload_fingerprint=final_payload_fingerprint,
            )
        else:
            record = {
                "planned": False,
                "degradation": "unplanned_model_request",
                "provider": provider,
                "message_count": len(messages or []),
                "max_tokens": max_tokens,
                "tool_names": [],
            }
        record = dict(record)
        record.setdefault("request_id", f"req_{uuid.uuid4().hex}")
        record["turn_id"] = self.turn_id
        record["trace_id"] = self.trace_id
        if self.turn_trace is not None:
            self.turn_trace.model_requests.append(record)
        return record


_current_turn_scope: ContextVar[Optional[TurnScope]] = ContextVar("wenshape_turn_scope", default=None)


def current_turn_scope() -> Optional[TurnScope]:
    """Return the scope inherited by the current async task."""

    return _current_turn_scope.get()


def new_turn_scope(
    *,
    project_id: str = "",
    chapter_id: str = "",
    turn_id: Optional[str] = None,
    parent_turn_id: Optional[str] = None,
    context_epoch: int = 0,
) -> TurnScope:
    return TurnScope(
        turn_id=turn_id or f"turn_{uuid.uuid4().hex}",
        project_id=str(project_id or ""),
        chapter_id=str(chapter_id or ""),
        parent_turn_id=parent_turn_id,
        context_epoch=max(0, int(context_epoch or 0)),
    )


@contextmanager
def bind_turn_scope(scope: TurnScope) -> Iterator[TurnScope]:
    """Bind a scope for all async work spawned in the current context."""

    token: Token = _current_turn_scope.set(scope)
    try:
        yield scope
    finally:
        _current_turn_scope.reset(token)


@contextmanager
def ensure_turn_scope(*, project_id: str = "", chapter_id: str = "") -> Iterator[TurnScope]:
    """Reuse the parent scope or create an isolated root scope."""

    existing = current_turn_scope()
    if existing is not None:
        yield existing
        return
    scope = new_turn_scope(project_id=project_id, chapter_id=chapter_id)
    with bind_turn_scope(scope):
        yield scope
