"""Versioned, content-free contracts for backend-owned fallback execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class FallbackTerminalState(str, Enum):
    COMPLETED = "completed"
    REQUIRES_INPUT = "requires_input"
    REQUIRES_APPROVAL = "requires_approval"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class FallbackDecisionV1:
    action: str
    reason: str
    category: str
    operation: str
    resource: Dict[str, str]
    request_fingerprint: str
    resource_fingerprint: str
    payload_fingerprint: str
    revision: Dict[str, Any]
    permission: str
    iterations: int = 0
    source_types: Tuple[str, ...] = ()
    artifact_refs: Tuple[str, ...] = ()
    degradation_types: Tuple[str, ...] = ()
    approval_action_id: str = ""
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["source_types"] = list(self.source_types)
        payload["artifact_refs"] = list(self.artifact_refs)
        payload["degradation_types"] = list(self.degradation_types)
        return payload


@dataclass(frozen=True)
class FallbackExecutionResultV1:
    decision_fingerprint: str
    terminal_state: str
    executed: bool
    result_ref: str = ""
    revision: Dict[str, Any] | None = None
    reason: str = ""
    approval_action_id: str = ""
    idempotency_replayed: bool = False
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
