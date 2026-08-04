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
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.context_engine.source_snapshot import SourceDescriptor, SourceRegistry


def _public_request_view(request: Dict[str, Any]) -> Dict[str, Any]:
    """剔除 model_request 记录里的内部 `_` 前缀键（如 gateway 注入的 `_deadline`
    RequestDeadline 活对象），确保 turn_trace 序列化进响应/trace 时是 JSON-safe。"""

    return {key: value for key, value in request.items() if not str(key).startswith("_")}


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
            "model_requests": [_public_request_view(request) for request in self.model_requests],
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
            if actual:
                from app.context_engine.token_accounting import record_token_estimator_observation

                request["estimator_calibration"] = record_token_estimator_observation(
                    provider=provider or str(request.get("provider") or ""),
                    model=model,
                    estimated_tokens=planned,
                    actual_tokens=actual,
                )
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
    timeout_seconds: Optional[float] = None
    cancelled: bool = False
    plans: List[Any] = field(default_factory=list)
    trace_events: List[Any] = field(default_factory=list)
    turn_trace: Optional[TurnTrace] = None
    runtime: Optional[Any] = None
    source_registry: Optional[SourceRegistry] = None

    def __post_init__(self) -> None:
        if not self.trace_id:
            self.trace_id = f"trace_{uuid.uuid4().hex}"
        if self.turn_trace is None:
            self.turn_trace = TurnTrace(turn_id=self.turn_id, trace_id=self.trace_id)
        if self.runtime is None:
            from app.orchestrator.turn_runtime import TurnRuntime

            timeout = self.timeout_seconds
            if timeout is None:
                try:
                    from app.config import config

                    timeout = float(config.get("session", {}).get("timeout_seconds") or 600)
                except (AttributeError, TypeError, ValueError):
                    timeout = 600.0
            self.runtime = TurnRuntime(turn_id=self.turn_id, timeout_seconds=max(0.001, float(timeout)))
        if self.source_registry is None:
            self.source_registry = SourceRegistry()

    @property
    def active_plan(self) -> Any:
        return self.plans[-1] if self.plans else None

    @property
    def model_requests(self) -> List[Dict[str, Any]]:
        return self.turn_trace.model_requests if self.turn_trace is not None else []

    def activate_plan(self, plan: Any) -> None:
        self.plans.append(plan)
        project_root = str(getattr(plan, "project_root", "") or "")
        planned = getattr(plan, "sources", ()) or ()
        self.source_registry = SourceRegistry(
            project_root=Path(project_root) if project_root else None,
            planned=planned,
        )
        self.source_registry.register_materialized_planned()

    def register_source(self, descriptor: SourceDescriptor | Dict[str, Any]) -> SourceDescriptor:
        if self.source_registry is None:
            self.source_registry = SourceRegistry()
        return self.source_registry.register_actual(descriptor)

    def register_source_content(self, **kwargs: Any) -> SourceDescriptor:
        if self.source_registry is None:
            self.source_registry = SourceRegistry()
        return self.source_registry.register_content(**kwargs)

    def register_source_file(self, path: Path, **kwargs: Any) -> SourceDescriptor:
        if self.source_registry is None:
            self.source_registry = SourceRegistry()
        return self.source_registry.register_file(path, **kwargs)

    def register_provider_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        source_prefix: str,
        selection_reason: str,
        artifact_ref: str = "",
    ) -> List[SourceDescriptor]:
        if self.source_registry is None:
            self.source_registry = SourceRegistry()
        return self.source_registry.register_payload(
            messages,
            tools,
            source_prefix=source_prefix,
            selection_reason=selection_reason,
            artifact_ref=artifact_ref,
        )

    @property
    def source_closure_required(self) -> bool:
        plan = self.active_plan
        if plan is None:
            return False
        policy = getattr(plan, "policy", {}) or {}
        return bool(policy.get("source_closure_required"))

    def cancel(self) -> None:
        self.cancelled = True
        if self.runtime is not None:
            self.runtime.cancel()

    def ensure_active(self) -> None:
        if self.cancelled:
            raise RuntimeError("turn_cancelled")
        if self.runtime is not None:
            self.runtime.ensure_active()

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
            planned_verification = (
                plan.verify_sources() if hasattr(plan, "verify_sources") else {"valid": False, "failures": ["unsupported"]}
            )
            actual_verification = (
                self.source_registry.verify_mutable_sources()
                if self.source_registry is not None
                else {"valid": False, "failures": [{"reason": "source_registry_missing"}]}
            )
            payload_verification = (
                self.source_registry.verify_payload(messages, tools)
                if self.source_registry is not None
                else {"valid": False, "missing": [{"kind": "registry"}]}
            )
            source_verification = {
                "valid": bool(planned_verification.get("valid"))
                and bool(actual_verification.get("valid"))
                and (not self.source_closure_required or bool(payload_verification.get("valid"))),
                "planned": planned_verification,
                "actual_mutable": actual_verification,
                "payload": payload_verification,
            }
            if self.turn_trace is not None:
                self.turn_trace.source_verifications.append(source_verification)
            if planned_verification.get("valid") is not True:
                failure = (planned_verification.get("failures") or [{}])[0]
                raise RuntimeError(f"context_source_revision_unavailable:{failure.get('path') or failure.get('reason')}")
            if actual_verification.get("valid") is not True:
                failure = (actual_verification.get("failures") or [{}])[0]
                raise RuntimeError(
                    f"context_actual_source_revision_unavailable:{failure.get('path') or failure.get('reason')}"
                )
            if self.source_closure_required and payload_verification.get("valid") is not True:
                missing = (payload_verification.get("missing") or [{}])[0]
                raise RuntimeError(
                    f"context_unregistered_payload:{missing.get('kind') or 'source'}:{missing.get('index', '')}"
                )
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
            record["source_closure"] = source_verification
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


def register_current_provider_payload(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    owner: str,
    selection_reason: str = "direct_provider_call_assembly",
) -> None:
    """Register a direct provider call assembled outside BaseAgent services."""

    scope = current_turn_scope()
    if scope is None or not scope.source_closure_required:
        return
    for index, message in enumerate(messages):
        role = str(message.get("role") or "")
        asset_type = {
            "system": "prompt",
            "user": "user_message",
            "assistant": "provider_response",
            "tool": "tool_result",
        }.get(role, "provider_message")
        scope.register_source_content(
            source_id=f"{owner}.semantic.{role or 'message'}.{index}",
            asset_type=asset_type,
            content=dict(message),
            selection_reason=selection_reason,
            artifact_ref=owner,
        )
    scope.register_provider_payload(
        messages,
        tools,
        source_prefix=owner,
        selection_reason=selection_reason,
        artifact_ref=owner,
    )


def new_turn_scope(
    *,
    project_id: str = "",
    chapter_id: str = "",
    turn_id: Optional[str] = None,
    parent_turn_id: Optional[str] = None,
    context_epoch: int = 0,
    timeout_seconds: Optional[float] = None,
) -> TurnScope:
    return TurnScope(
        turn_id=turn_id or f"turn_{uuid.uuid4().hex}",
        project_id=str(project_id or ""),
        chapter_id=str(chapter_id or ""),
        parent_turn_id=parent_turn_id,
        context_epoch=max(0, int(context_epoch or 0)),
        timeout_seconds=timeout_seconds,
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
