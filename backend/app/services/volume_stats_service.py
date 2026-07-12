"""Volume statistics use case, separated from storage adapters."""

from __future__ import annotations

from typing import Any, Optional

from app.error_contract import record_degradation
from app.schemas.volume import VolumeStats


class VolumeStatsService:
    def __init__(self, *, volume_storage: Any, draft_storage: Any) -> None:
        self.volume_storage = volume_storage
        self.draft_storage = draft_storage

    async def get(self, project_id: str, volume_id: str) -> Optional[VolumeStats]:
        volume = await self.volume_storage.get_volume(project_id, volume_id)
        if not volume:
            return None
        chapters = await self.draft_storage.list_chapters(project_id)
        volume_chapters = [chapter for chapter in chapters if chapter.startswith(volume_id)]
        total_words = 0
        for chapter in volume_chapters:
            try:
                draft = await self.draft_storage.get_latest_draft(project_id, chapter)
                if draft:
                    total_words += draft.word_count
            except Exception as exc:
                record_degradation("volume_draft_stats", exc)
        return VolumeStats(
            volume_id=volume_id,
            title=volume.title,
            chapter_count=len(volume_chapters),
            total_words=total_words,
            created_at=volume.created_at,
            updated_at=volume.updated_at,
        )
