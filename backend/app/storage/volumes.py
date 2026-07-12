"""
中文说明：分卷存储服务，基于文件系统管理分卷元数据与摘要。

Volume Storage
File-based CRUD for volume metadata and summaries.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.schemas.volume import Volume, VolumeCreate, VolumeSummary
from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock


class VolumeStorage(BaseStorage):
    """File-based storage for volumes."""

    async def create_volume(self, project_id: str, volume_create: VolumeCreate) -> Volume:
        """
        Create a new volume with auto-incremented ID.
        Uses file lock to prevent race conditions on concurrent creation.
        使用文件锁防止并发创建导致的 ID 冲突。
        """
        await self._ensure_default_volume(project_id)

        # Use a lock file to serialize volume creation for the same project
        lock_path = self.get_project_path(project_id) / "volumes" / ".create_lock"
        self.ensure_dir(lock_path.parent)
        file_lock = get_file_lock()

        async with file_lock.lock(lock_path, timeout=10.0):
            volumes = await self.list_volumes(project_id)
            existing_ids = {v.id for v in volumes}

            # Find next available volume ID
            next_volume_num = len(volumes) + 1
            volume_id = f"V{next_volume_num}"
            while volume_id in existing_ids:
                next_volume_num += 1
                volume_id = f"V{next_volume_num}"

            volume = Volume(
                id=volume_id,
                project_id=project_id,
                title=volume_create.title,
                summary=volume_create.summary,
                order=volume_create.order or next_volume_num,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            await self._save_volume(project_id, volume)
            return volume

    async def get_volume(self, project_id: str, volume_id: str) -> Optional[Volume]:
        """Get volume metadata."""
        if volume_id == "V1":
            await self._ensure_default_volume(project_id)

        file_path = self._get_volume_file_path(project_id, volume_id)
        if not file_path.exists():
            return None

        data = await self.read_yaml(file_path)
        return Volume(**data)

    async def list_volumes(self, project_id: str) -> List[Volume]:
        """List all volumes for a project."""
        await self._ensure_default_volume(project_id)

        volumes_dir = self.get_project_path(project_id) / "volumes"
        if not volumes_dir.exists():
            return []

        volumes: List[Volume] = []
        for file_path in sorted(self._list_volume_files(volumes_dir)):
            data = await self.read_yaml(file_path)
            volumes.append(Volume(**data))

        volumes.sort(key=lambda v: v.order)
        return volumes

    async def update_volume(self, project_id: str, volume: Volume) -> Volume:
        """Update volume metadata."""
        volume.updated_at = datetime.now()
        await self._save_volume(project_id, volume)
        return volume

    async def delete_volume(self, project_id: str, volume_id: str) -> bool:
        """Delete a volume file."""
        file_path = self._get_volume_file_path(project_id, volume_id)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    async def save_volume_summary(self, project_id: str, summary: VolumeSummary) -> None:
        """Save a volume summary."""
        file_path = self._get_volume_summary_file_path(project_id, summary.volume_id)
        self.ensure_dir(file_path.parent)
        await self.write_yaml(file_path, summary.model_dump())

    async def get_volume_summary(self, project_id: str, volume_id: str) -> Optional[VolumeSummary]:
        """Get a volume summary."""
        file_path = self._get_volume_summary_file_path(project_id, volume_id)
        if not file_path.exists():
            return None

        data = await self.read_yaml(file_path)
        return VolumeSummary(**data)

    def _get_volume_file_path(self, project_id: str, volume_id: str) -> Path:
        """Get volume metadata file path."""
        return self.get_project_path(project_id) / "volumes" / f"{volume_id}.yaml"

    def _get_volume_summary_file_path(self, project_id: str, volume_id: str) -> Path:
        """Get volume summary file path."""
        return self.get_project_path(project_id) / "volumes" / f"{volume_id}_summary.yaml"

    async def _save_volume(self, project_id: str, volume: Volume) -> None:
        """Persist volume metadata."""
        file_path = self._get_volume_file_path(project_id, volume.id)
        self.ensure_dir(file_path.parent)
        await self.write_yaml(file_path, volume.model_dump())

    async def _ensure_default_volume(self, project_id: str) -> None:
        """Ensure default volume V1 exists."""
        volumes_dir = self.get_project_path(project_id) / "volumes"
        if volumes_dir.exists() and any(self._list_volume_files(volumes_dir)):
            return

        default_volume = Volume(
            id="V1",
            project_id=project_id,
            title="Volume 1",
            summary=None,
            order=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await self._save_volume(project_id, default_volume)

    def _list_volume_files(self, volumes_dir: Path) -> List[Path]:
        """List volume files excluding summaries."""
        return [path for path in volumes_dir.glob("*.yaml") if not path.name.endswith("_summary.yaml")]
