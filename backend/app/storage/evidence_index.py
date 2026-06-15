# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  证据索引存储 - 基于文件的证据索引存储层，支持证据项和元数据的持久化。
  Evidence index storage - File-based storage for evidence indices with metadata tracking.
"""

from typing import Any, Dict, List, Optional

from app.schemas.evidence import EvidenceItem, EvidenceIndexMeta
from app.storage.base import BaseStorage


class EvidenceIndexStorage(BaseStorage):
    """
    证据索引存储层 - 基于文件的存储实现。

    File-based storage for evidence indices (facts, summaries, cards, memory).
    Stores items as JSONL and metadata as JSON for efficient append operations.
    """

    def get_index_dir(self, project_id: str):
        """Return the index directory for a project."""
        return self.get_project_path(project_id) / "index"

    def get_index_path(self, project_id: str, index_name: str):
        """Return the JSONL path for an index."""
        return self.get_index_dir(project_id) / f"{index_name}.jsonl"

    def get_meta_path(self, project_id: str, index_name: str):
        """Return the metadata path for an index."""
        return self.get_index_dir(project_id) / f"{index_name}.meta.json"

    async def write_items(self, project_id: str, index_name: str, items: List[EvidenceItem]) -> None:
        """Write evidence items to index storage."""
        path = self.get_index_path(project_id, index_name)
        payload = [item.model_dump(mode="json") for item in items]
        await self.write_jsonl(path, payload)

    async def append_items(self, project_id: str, index_name: str, items: List[EvidenceItem]) -> None:
        """Append evidence items to index storage."""
        path = self.get_index_path(project_id, index_name)
        for item in items:
            await self.append_jsonl(path, item.model_dump(mode="json"))

    async def read_items(self, project_id: str, index_name: str) -> List[EvidenceItem]:
        """Read evidence items from index storage."""
        path = self.get_index_path(project_id, index_name)
        rows = await self.read_jsonl(path)
        return [EvidenceItem(**row) for row in rows]

    async def write_meta(self, project_id: str, index_name: str, meta: EvidenceIndexMeta) -> None:
        """Write index metadata."""
        path = self.get_meta_path(project_id, index_name)
        await self.write_json(path, meta.model_dump(mode="json"))

    async def read_meta(self, project_id: str, index_name: str) -> Optional[EvidenceIndexMeta]:
        """Read index metadata."""
        path = self.get_meta_path(project_id, index_name)
        if not path.exists():
            return None
        try:
            data = await self.read_json(path)
        except Exception:
            return None
        if not data:
            return None
        return EvidenceIndexMeta(**data)

    async def read_json(self, file_path):
        """Read a JSON file."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        import json
        import aiofiles

        async with aiofiles.open(file_path, "r", encoding=self.encoding) as f:
            raw = await f.read()
            return json.loads(raw)

    async def write_json(self, file_path, data: Dict[str, Any]) -> None:
        """Write a JSON file."""
        import json

        payload = json.dumps(data, ensure_ascii=False, indent=2)
        await self._atomic_write(file_path, payload)
