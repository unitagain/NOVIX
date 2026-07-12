"""Stable application ports exposed by the Orchestrator facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from app.error_contract import record_degradation


class StreamTaskRegistry:
    """Own per-chapter stream task replacement and cancellation semantics."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}

    def replace(self, chapter: str, task: asyncio.Task) -> None:
        previous = self._tasks.pop(str(chapter), None)
        if previous and not previous.done():
            previous.cancel()
        self._tasks[str(chapter)] = task

    def discard(self, chapter: str) -> None:
        self._tasks.pop(str(chapter), None)

    def cancel_all(self) -> int:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        return len(tasks)


class ConversationPort:
    def __init__(self, session_history: Any, post_turn_service: Any) -> None:
        self.session_history = session_history
        self.post_turn_service = post_turn_service

    async def append(self, project_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        return await self.session_history.append(project_id, message)

    async def load(self, project_id: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        return await self.session_history.load(project_id, limit=limit)

    async def compact(self, project_id: str, *, keep_recent: int = 40, trigger_at: int = 120) -> Dict[str, Any]:
        return await self.post_turn_service.compact_conversation(
            project_id,
            keep_recent=keep_recent,
            trigger_at=trigger_at,
        )


class CommandPort:
    def __init__(self, run_command: Callable[..., Awaitable[Any]]) -> None:
        self._run_command = run_command

    async def run(self, **kwargs: Any) -> Any:
        return await self._run_command(**kwargs)


class AnalysisPort:
    """Public analysis use-case boundary; routers do not depend on Orchestrator internals."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    async def analyze_chapter(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.owner.analyze_chapter(**kwargs)

    async def save_analysis(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.owner.save_analysis(**kwargs)

    async def analyze_sync(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        return await self.owner.analyze_sync(project_id, chapters)

    async def analyze_batch(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        return await self.owner.analyze_batch(project_id, chapters)

    async def save_analysis_batch(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.owner.save_analysis_batch(**kwargs)


class ContextPort:
    def __init__(self, ensure_memory_pack: Callable[..., Awaitable[Dict[str, Any]]]) -> None:
        self._ensure_memory_pack = ensure_memory_pack

    async def ensure_memory_pack(self, **kwargs: Any) -> Dict[str, Any]:
        return await self._ensure_memory_pack(**kwargs)


class VolumeSummaryService:
    def __init__(self, *, draft_storage: Any, archivist: Any) -> None:
        self.draft_storage = draft_storage
        self.archivist = archivist

    async def refresh(self, project_id: str, volume_ids: List[str]) -> Dict[str, Any]:
        refreshed: List[str] = []
        failed: List[Dict[str, str]] = []
        seen = set()
        for volume_id in [str(value or "").strip() for value in volume_ids if str(value or "").strip()]:
            if volume_id in seen:
                continue
            seen.add(volume_id)
            try:
                summaries = await self.draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)
                summary = await self.archivist.generate_volume_summary(
                    project_id=project_id,
                    volume_id=volume_id,
                    chapter_summaries=summaries,
                )
                await self.draft_storage.volume_storage.save_volume_summary(project_id, summary)
                refreshed.append(volume_id)
            except Exception as exc:
                degradation_code = record_degradation("volume_summary_refresh", exc)
                failed.append({"volume_id": volume_id, "error": degradation_code})
        return {"success": not failed, "refreshed": refreshed, "failed": failed}


@dataclass(frozen=True)
class OrchestratorApplicationPorts:
    conversation: ConversationPort
    commands: CommandPort
    analysis: AnalysisPort
    context: ContextPort
    volumes: VolumeSummaryService
    plans: Any
