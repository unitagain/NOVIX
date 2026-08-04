"""Outline storage —— 全文规划大纲（一等创作规划资产，非章节）。

设计要点（见 plan.md §7.2）：
- 独立项目级资产，落 ``outline/outline.md`` + ``outline/outline.meta.yaml``。
- 结构性排除事实提取：大纲不是章节，``list_chapters``/finalize/archivist 根本不遍历它，
  因此永不进入 Canon/Summary/relations。本模块只负责读写与 revision，不接入任何事实管线。
- 用户与 AI 均可编辑：复用 control_store revision + 原子写 + expected_revision 乐观并发
  （与 DraftStorage 同并发模型），支持 RevisionConflict。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.control_plane.store import RevisionConflict, SQLiteControlStore
from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock

__all__ = ["OutlineStorage", "RevisionConflict"]

_OUTLINE_NAMESPACE = "outline"


class OutlineStorage(BaseStorage):
    """File-based outline storage. 唯一 outline 读写与 revision owner。"""

    def __init__(self, data_dir: Optional[str] = None):
        super().__init__(data_dir)
        self.control_store = SQLiteControlStore(Path(self.data_dir) / "_system" / "control.sqlite3")

    def _outline_dir(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "outline"

    def _outline_path(self, project_id: str) -> Path:
        return self._outline_dir(project_id) / "outline.md"

    def _meta_path(self, project_id: str) -> Path:
        return self._outline_dir(project_id) / "outline.meta.yaml"

    def _revision_key(self, project_id: str) -> str:
        return f"{project_id}/outline.md"

    async def get_outline(self, project_id: str) -> Dict[str, Any]:
        """读取大纲正文与元数据；不存在时返回空文档（不报错）。"""
        path = self._outline_path(project_id)
        content = ""
        if path.is_file():
            try:
                content = await self.read_text(path)
            except (OSError, UnicodeError):
                content = ""
        revision = self.control_store.get_revision(_OUTLINE_NAMESPACE, self._revision_key(project_id))
        return {
            "content": content,
            "word_count": len(content),
            "revision": int(revision.get("revision") or 0),
            "fingerprint": str(revision.get("fingerprint") or ""),
            "exists": path.is_file(),
        }

    async def save_outline(
        self,
        project_id: str,
        content: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """覆盖写大纲正文（用户/AI 编辑统一入口），支持 expected_revision 乐观并发。"""
        payload = str(content or "")
        outline_dir = self._outline_dir(project_id)
        self.ensure_dir(outline_dir)
        outline_path = self._outline_path(project_id)
        meta_path = self._meta_path(project_id)
        revision_key = self._revision_key(project_id)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        file_lock = get_file_lock()
        async with self.content_transaction(project_id):
            async with file_lock.lock(outline_dir / ".outline_transaction"):
                revision = self.control_store.get_revision(_OUTLINE_NAMESPACE, revision_key)
                if expected_revision is not None and int(revision["revision"]) != int(expected_revision):
                    raise RevisionConflict(f"revision_conflict:{revision['revision']}!={int(expected_revision)}")
                if revision["fingerprint"] == fingerprint and outline_path.is_file():
                    return {
                        "content": payload,
                        "word_count": len(payload),
                        "revision": int(revision["revision"]),
                        "fingerprint": fingerprint,
                        "exists": True,
                    }
                await self.write_text(outline_path, payload)
                await self.write_yaml(
                    meta_path,
                    {"word_count": len(payload), "updated_at": datetime.now().isoformat()},
                )
                next_revision = self.control_store.bump_revision(
                    _OUTLINE_NAMESPACE,
                    revision_key,
                    expected_revision=int(revision["revision"]),
                    fingerprint=fingerprint,
                )
        return {
            "content": payload,
            "word_count": len(payload),
            "revision": int(next_revision),
            "fingerprint": fingerprint,
            "exists": True,
        }
