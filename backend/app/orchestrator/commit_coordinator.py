"""Single write-back boundary for orchestrated draft commits."""

from __future__ import annotations

from typing import Any, List, Optional

from app.context_engine.turn_scope import current_turn_scope
from app.orchestrator.turn_runtime import TurnState


class CommitCoordinator:
    """Coordinate authoritative writes and runtime commit transitions."""

    def __init__(self, *, draft_storage: Any):
        self.draft_storage = draft_storage

    async def save_draft(
        self,
        *,
        project_id: str,
        chapter: str,
        version: str,
        content: str,
        word_count: int,
        pending_confirmations: Optional[List[str]] = None,
        expected_revision: Optional[int] = None,
    ) -> Any:
        scope = current_turn_scope()
        runtime = scope.runtime if scope is not None else None
        if runtime is not None and runtime.state not in {TurnState.COMMITTING, TurnState.COMPLETED}:
            runtime.transition(TurnState.COMMITTING)
        kwargs = {
            "project_id": project_id,
            "chapter": chapter,
            "version": version,
            "content": content,
            "word_count": word_count,
            "pending_confirmations": pending_confirmations or [],
        }
        if expected_revision is not None:
            kwargs["expected_revision"] = expected_revision
        return await self.draft_storage.save_draft(**kwargs)

    async def save_final_draft(
        self,
        *,
        project_id: str,
        chapter: str,
        content: str,
        expected_revision: Optional[int] = None,
    ) -> None:
        scope = current_turn_scope()
        runtime = scope.runtime if scope is not None else None
        if runtime is not None and runtime.state not in {TurnState.COMMITTING, TurnState.COMPLETED}:
            runtime.transition(TurnState.COMMITTING)
        kwargs = {"project_id": project_id, "chapter": chapter, "content": content}
        if expected_revision is not None:
            kwargs["expected_revision"] = expected_revision
        await self.draft_storage.save_final_draft(**kwargs)
