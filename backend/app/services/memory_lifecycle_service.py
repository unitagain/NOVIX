"""Single owner for creative-memory lifecycle mutations."""

from __future__ import annotations

from typing import Any, List


class MemoryLifecycleService:
    def __init__(self, storage: Any):
        self.storage = storage

    async def confirm(self, project_id: str, slug: str) -> bool:
        return await self.storage.confirm_memory(project_id, slug)

    async def reject(self, project_id: str, slug: str) -> bool:
        return await self.storage.reject_memory(project_id, slug)

    async def transition(self, project_id: str, slug: str, status: str) -> bool:
        return await self.storage.set_memory_status(project_id, slug, status)

    async def set_conflicts(self, project_id: str, slug: str, conflicts_with: List[str]) -> bool:
        return await self.storage.set_memory_conflicts(project_id, slug, conflicts_with)

    async def supersede(self, project_id: str, old_slug: str, new_slug: str, **replacement: Any) -> str:
        return await self.storage.supersede_memory(project_id, old_slug, new_slug, **replacement)

    async def expire_due(self, project_id: str) -> List[str]:
        return await self.storage.expire_due_memories(project_id)
