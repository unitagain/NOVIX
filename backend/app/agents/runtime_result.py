"""Typed terminal contract shared by agent, provider, tool, and worker paths."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from app.context_engine.tool_artifact import ToolExecutionResult


class AgentRunStatus(str, Enum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentRunResult(Mapping[str, Any]):
    """Provider-independent terminal result with mapping compatibility."""

    status: str
    content: str = ""
    response: Dict[str, Any] = field(default_factory=dict)
    tool_results: Tuple[ToolExecutionResult, ...] = ()
    error: Optional[Dict[str, Any]] = None
    elapsed_ms: int = 0
    iterations: int = 0
    finish_reason: str = ""
    degradations: Tuple[Dict[str, Any], ...] = ()

    @property
    def success(self) -> bool:
        return self.status == AgentRunStatus.COMPLETED.value

    @property
    def incomplete(self) -> bool:
        return self.status == AgentRunStatus.INCOMPLETE.value

    @property
    def cancelled(self) -> bool:
        return self.status == AgentRunStatus.CANCELLED.value

    def to_dict(self, *, include_response: bool = False) -> Dict[str, Any]:
        payload = {
            "status": self.status,
            "success": self.success,
            "incomplete": self.incomplete,
            "cancelled": self.cancelled,
            "content": self.content,
            "tool_results": [result.to_dict() for result in self.tool_results],
            "error": dict(self.error) if self.error else None,
            "elapsed_ms": self.elapsed_ms,
            "iterations": self.iterations,
            "finish_reason": self.finish_reason,
            "degradations": [dict(item) for item in self.degradations],
        }
        if include_response:
            payload["response"] = dict(self.response)
        return payload

    def _projection(self) -> Dict[str, Any]:
        projection = dict(self.response)
        projection.update(self.to_dict(include_response=False))
        return projection

    def __getitem__(self, key: str) -> Any:
        return self._projection()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._projection())

    def __len__(self) -> int:
        return len(self._projection())


def worker_agent_run_result(
    *,
    status: str,
    output: Optional[Dict[str, Any]],
    error: Optional[Dict[str, Any]],
    elapsed_ms: int,
) -> AgentRunResult:
    """Map the isolated worker lifecycle into the common terminal contract."""

    normalized = str(status or "")
    if normalized == "completed":
        run_status = AgentRunStatus.COMPLETED.value
    elif normalized == "cancelled":
        run_status = AgentRunStatus.CANCELLED.value
    else:
        run_status = AgentRunStatus.FAILED.value
    return AgentRunResult(
        status=run_status,
        response={"worker_output": dict(output or {})},
        error=dict(error) if error else None,
        elapsed_ms=max(0, int(elapsed_ms or 0)),
        finish_reason=f"worker_{normalized or 'unknown'}",
    )
