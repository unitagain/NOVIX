# -*- coding: utf-8 -*-
"""Finalize pipeline service.

P7 抽离点：定稿后写回 final draft、canon/summary/memory 分析与状态更新。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from app.orchestrator.contracts import SessionStatus

AnalyzeCallback = Callable[[str, str, str], Awaitable[Any]]
StatusCallback = Callable[[SessionStatus, str], Awaitable[None]]


class FinalizePipeline:
    """Persist the final draft and run post-finalize analysis."""

    def __init__(
        self,
        *,
        draft_storage: Any,
        commit_coordinator: Any = None,
        analyze_content: AnalyzeCallback,
        update_status: StatusCallback,
    ):
        self.draft_storage = draft_storage
        self.commit_coordinator = commit_coordinator
        self.analyze_content = analyze_content
        self.update_status = update_status

    async def finalize_chapter(self, project_id: str, chapter: str) -> Dict[str, Any]:
        """Finalize one chapter without swallowing exceptions."""

        versions = await self.draft_storage.list_draft_versions(project_id, chapter)
        if not versions:
            return {"success": False, "error": "No draft found to finalize"}

        latest_version = versions[-1]
        draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
        if not draft:
            return {"success": False, "error": "No draft content found to finalize"}

        if self.commit_coordinator is not None:
            await self.commit_coordinator.save_final_draft(
                project_id=project_id, chapter=chapter, content=draft.content
            )
        else:
            await self.draft_storage.save_final_draft(project_id=project_id, chapter=chapter, content=draft.content)
        await self.analyze_content(project_id, chapter, draft.content)
        await self.update_status(SessionStatus.COMPLETED, "Chapter completed.")

        return {
            "success": True,
            "status": SessionStatus.COMPLETED,
            "message": "Chapter finalized successfully",
            "final_draft": draft,
        }
