"""Single owner for creative-memory lifecycle mutations."""

from __future__ import annotations

from typing import Any, Dict, List

from app.context_engine.memory_record import (
    MEMORY_RECALLABLE_STATUS,
    MEMORY_TERMINAL_STATUSES,
    MemoryRecordV2,
    build_memory_graph,
    memory_transition_allowed,
    normalize_memory_status,
    parse_string_list,
)


class MemoryLifecycleService:
    def __init__(self, storage: Any):
        self.storage = storage

    async def confirm(self, project_id: str, slug: str) -> bool:
        existing = await self.storage.read_memory(project_id, slug)
        if not existing:
            return False
        record = MemoryRecordV2.from_mapping(existing)
        if record.is_expired():
            return False
        if not memory_transition_allowed(record.status, MEMORY_RECALLABLE_STATUS):
            return False
        return await self.storage._apply_status_transition(
            project_id,
            slug,
            MEMORY_RECALLABLE_STATUS,
            confirmed_by="user",
            trust_label_override="trusted",
        )

    async def reject(self, project_id: str, slug: str) -> bool:
        return await self.transition(project_id, slug, "rejected")

    async def transition(self, project_id: str, slug: str, status: str) -> bool:
        target = normalize_memory_status(status, default="")
        if not target:
            return False
        if target == MEMORY_RECALLABLE_STATUS:
            return await self.confirm(project_id, slug)
        existing = await self.storage.read_memory(project_id, slug)
        if not existing or not memory_transition_allowed(str(existing.get("status") or ""), target):
            return False
        if normalize_memory_status(existing.get("status")) == target:
            return True
        return await self.storage._apply_status_transition(project_id, slug, target)

    async def set_conflicts(self, project_id: str, slug: str, conflicts_with: List[str]) -> bool:
        headers = await self.storage.list_headers(project_id, statuses=["*"])
        ids = {str(item.get("id") or item.get("slug")) for item in headers}
        source_id = str(slug or "")
        targets = parse_string_list(conflicts_with)
        if source_id not in ids or source_id in targets or any(target not in ids for target in targets):
            return False
        return await self.storage._apply_conflict_set(project_id, source_id, targets)

    async def supersede(self, project_id: str, old_slug: str, new_slug: str, **replacement: Any) -> str:
        old_id = str(old_slug or "")
        new_id = str(new_slug or "")
        if not old_id or not new_id or old_id == new_id:
            raise ValueError("memory_supersedes_self_reference")
        headers = await self.storage.list_headers(project_id, statuses=["*"])
        ids = {str(item.get("id") or item.get("slug")) for item in headers}
        if old_id not in ids:
            raise ValueError("memory_not_found")
        if new_id in ids:
            raise ValueError("memory_replacement_exists")
        supersedes = [old_id, *parse_string_list(replacement.get("supersedes"))]
        candidate = {
            "id": new_id,
            "slug": new_id,
            "status": "active",
            "supersedes": supersedes,
            "conflicts_with": parse_string_list(replacement.get("conflicts_with")),
        }
        graph = build_memory_graph([*headers, candidate])
        candidate_errors = [item for item in graph.errors if item.get("id") == new_id]
        if new_id in graph.invalid_ids:
            first = candidate_errors[0] if candidate_errors else graph.errors[0]
            raise ValueError(f"memory_{first.get('relation')}_{first.get('reason')}:{first.get('ref') or new_id}")
        return await self.storage._apply_supersede(project_id, old_id, new_id, replacement)

    async def expire_due(self, project_id: str) -> List[str]:
        headers = await self.storage.list_headers(project_id, statuses=["*"])
        expired: List[str] = []
        for item in headers:
            record = MemoryRecordV2.from_mapping(item)
            if record.status in MEMORY_TERMINAL_STATUSES or not record.is_expired():
                continue
            if await self.storage._apply_status_transition(project_id, item["slug"], "expired"):
                expired.append(item["slug"])
        return expired

    async def migrate_legacy(self, project_id: str) -> int:
        return await self.storage._migrate_legacy_records(project_id)

    async def validate_graph(self, project_id: str) -> Dict[str, Any]:
        headers = await self.storage.list_headers(project_id, statuses=["*"])
        graph = build_memory_graph(headers)
        return {
            "valid": not graph.errors,
            "errors": list(graph.errors),
            "conflict_participants": sorted(graph.conflict_participants),
            "superseded_ids": sorted(graph.superseded_ids),
            "invalid_ids": sorted(graph.invalid_ids),
        }
