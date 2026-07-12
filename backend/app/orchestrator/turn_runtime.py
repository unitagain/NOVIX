"""Explicit state machine for one writing turn."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class TurnState(str, Enum):
    CREATED = "created"
    ROUTING = "routing"
    CONTEXT_PLANNING = "context_planning"
    WRITER_RUNNING = "writer_running"
    PLAN_RUNNING = "plan_running"
    FALLBACK_RUNNING = "fallback_running"
    WORKER_RUNNING = "worker_running"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {TurnState.COMPLETED, TurnState.FAILED, TurnState.CANCELLED}
_ALLOWED: Dict[TurnState, Set[TurnState]] = {
    TurnState.CREATED: {TurnState.ROUTING, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.ROUTING: {TurnState.CONTEXT_PLANNING, TurnState.FALLBACK_RUNNING, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.CONTEXT_PLANNING: {
        TurnState.WRITER_RUNNING,
        TurnState.PLAN_RUNNING,
        TurnState.FALLBACK_RUNNING,
        TurnState.WORKER_RUNNING,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.WRITER_RUNNING: {
        TurnState.COMMITTING,
        TurnState.FALLBACK_RUNNING,
        TurnState.COMPLETED,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.PLAN_RUNNING: {
        TurnState.CONTEXT_PLANNING,
        TurnState.COMMITTING,
        TurnState.FALLBACK_RUNNING,
        TurnState.COMPLETED,
        TurnState.CANCELLED,
        TurnState.FAILED,
    },
    TurnState.FALLBACK_RUNNING: {TurnState.COMMITTING, TurnState.COMPLETED, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.WORKER_RUNNING: {TurnState.COMMITTING, TurnState.COMPLETED, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.COMMITTING: {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.FAILED},
    TurnState.COMPLETED: set(),
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
    """Deterministic lifecycle with explicit retry, fallback, and terminal states."""

    turn_id: str
    state: TurnState = TurnState.CREATED
    transitions: List[TurnTransition] = field(default_factory=list)
    failure: Optional[str] = None

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
        self.failure = str(error)
        self.transition(TurnState.FAILED, reason=self.failure)

    def cancel(self, reason: str = "user_cancelled") -> None:
        if self.state in _TERMINAL:
            return
        self.transition(TurnState.CANCELLED, reason=reason)

    def complete(self) -> None:
        if self.state in _TERMINAL:
            return
        self.transition(TurnState.COMPLETED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "state": self.state.value,
            "terminal": self.state in _TERMINAL,
            "failure": self.failure,
            "transitions": [transition.to_dict() for transition in self.transitions],
        }
