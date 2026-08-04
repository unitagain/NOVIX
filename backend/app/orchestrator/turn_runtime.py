"""Explicit state machine for one writing turn."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from app.error_contract import normalize_error_code, safe_error_code


class TurnState(str, Enum):
    CREATED = "created"
    ROUTING = "routing"
    CONTEXT_PLANNING = "context_planning"
    WRITER_RUNNING = "writer_running"
    PLAN_RUNNING = "plan_running"
    WORKER_RUNNING = "worker_running"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {TurnState.COMPLETED, TurnState.INCOMPLETE, TurnState.FAILED, TurnState.CANCELLED}
_ALLOWED: Dict[TurnState, Set[TurnState]] = {
    TurnState.CREATED: {TurnState.ROUTING, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.ROUTING: {TurnState.CONTEXT_PLANNING, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.CONTEXT_PLANNING: {
        TurnState.WRITER_RUNNING,
        TurnState.PLAN_RUNNING,
        TurnState.WORKER_RUNNING,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.WRITER_RUNNING: {
        TurnState.COMPLETED,
        TurnState.INCOMPLETE,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.PLAN_RUNNING: {
        TurnState.CONTEXT_PLANNING,
        TurnState.COMPLETED,
        TurnState.INCOMPLETE,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.WORKER_RUNNING: {
        TurnState.COMPLETED,
        TurnState.INCOMPLETE,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.COMPLETED: set(),
    TurnState.INCOMPLETE: set(),
    TurnState.FAILED: set(),
    TurnState.CANCELLED: set(),
}


@dataclass
class TurnTransition:
    source: str
    target: str
    timestamp: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass
class TurnRuntime:
    """Deterministic lifecycle with explicit running and terminal states."""

    turn_id: str
    state: TurnState = TurnState.CREATED
    transitions: List[TurnTransition] = field(default_factory=list)
    failure: Optional[str] = None
    timeout_seconds: float = 600.0
    started_monotonic: float = field(default_factory=time.monotonic)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, float(self.timeout_seconds) - (time.monotonic() - self.started_monotonic))

    @property
    def cancelled(self) -> bool:
        return self.state == TurnState.CANCELLED or self._cancel_event.is_set()

    def ensure_active(self) -> None:
        if self.cancelled:
            raise RuntimeError("turn_cancelled")
        if self.state in _TERMINAL:
            raise RuntimeError(f"turn_not_active:{self.state.value}")
        if self.remaining_seconds <= 0:
            raise TimeoutError("turn_deadline_exceeded")

    async def wait_for(self, awaitable: Any, *, timeout_seconds: Optional[float] = None) -> Any:
        """Await work under the shared turn deadline and cancellation signal."""

        self.ensure_active()
        timeout = self.remaining_seconds
        if timeout_seconds is not None and float(timeout_seconds) > 0:
            timeout = min(timeout, float(timeout_seconds))
        work = asyncio.ensure_future(awaitable)
        cancelled = asyncio.create_task(self._cancel_event.wait())
        try:
            done, _ = await asyncio.wait({work, cancelled}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                raise RuntimeError("turn_cancelled")
            if work not in done:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                raise TimeoutError("turn_deadline_exceeded")
            result = await work
            self.ensure_active()
            return result
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    def transition(self, target: TurnState, *, reason: str = "", metadata: Optional[Dict[str, Any]] = None) -> None:
        if target == self.state:
            if target not in {TurnState.CONTEXT_PLANNING, TurnState.WRITER_RUNNING, TurnState.PLAN_RUNNING}:
                raise ValueError(f"invalid_runtime_self_transition:{target.value}")
        elif target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid_runtime_transition:{self.state.value}->{target.value}")
        source = self.state
        self.state = target
        self.transitions.append(
            TurnTransition(
                source=source.value,
                target=target.value,
                timestamp=time.time(),
                reason=str(reason or ""),
                metadata=dict(metadata or {}),
            )
        )

    def fail(self, error: Any) -> None:
        if self.state in _TERMINAL:
            return
        self.failure = safe_error_code(error) if isinstance(error, BaseException) else normalize_error_code(error)
        self.transition(TurnState.FAILED, reason=self.failure)

    def cancel(self, reason: str = "user_cancelled") -> None:
        if self.state in _TERMINAL:
            return
        self._cancel_event.set()
        self.transition(TurnState.CANCELLED, reason=reason)

    def complete(self) -> None:
        if self.state in _TERMINAL:
            return
        self.transition(TurnState.COMPLETED)

    def incomplete(self, reason: str = "incomplete") -> None:
        if self.state in _TERMINAL:
            return
        self.transition(TurnState.INCOMPLETE, reason=normalize_error_code(reason, "incomplete"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "state": self.state.value,
            "terminal": self.state in _TERMINAL,
            "failure": self.failure,
            "timeout_seconds": self.timeout_seconds,
            "remaining_seconds": self.remaining_seconds,
            "transitions": [transition.to_dict() for transition in self.transitions],
        }
