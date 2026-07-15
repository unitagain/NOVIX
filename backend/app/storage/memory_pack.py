"""
Memory Pack Storage / 章节记忆包存储
负责持久化每章最新一份的检索记忆包，供主笔/编辑复用
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles

from app.config import config as app_cfg
from app.context_engine.turn_scope import current_turn_scope
from app.storage.base import BaseStorage
from app.storage.file_lock import get_file_lock
from app.utils.chapter_id import ChapterIDValidator, normalize_chapter_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Max number of history backups to keep per chapter memory pack.
_storage_cfg = app_cfg.get("storage", {})
MAX_MEMORY_PACK_HISTORY = int(_storage_cfg.get("max_memory_pack_history", 3))
_MEMORY_PACK_SOURCE_ROOTS = {"canon", "cards", "drafts", "memory", "outline", "summaries", "volumes"}
_SOURCE_INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceScanResult:
    revisions: list[Dict[str, Any]]
    files_scanned: int
    bytes_hashed: int
    scan_latency_ms: float
    hash_latency_ms: float
    total_latency_ms: float
    fallback_reason: str = ""

    def diagnostics(self, *, changed_files: int = 0) -> Dict[str, Any]:
        payload = asdict(self)
        payload.pop("revisions", None)
        payload["changed_files"] = max(0, int(changed_files))
        return payload


class MemoryPackStorage(BaseStorage):
    """File-based storage for chapter memory packs / 章节记忆包文件存储。"""

    def _canonicalize_chapter_id(self, chapter_id: str) -> str:
        normalized = normalize_chapter_id(chapter_id)
        if normalized and ChapterIDValidator.validate(normalized):
            return normalized
        return str(chapter_id).strip() if chapter_id else ""

    def get_pack_path(self, project_id: str, chapter: str) -> Path:
        """Return the JSON path for a chapter memory pack."""
        canonical = self._canonicalize_chapter_id(chapter)
        return self.get_project_path(project_id) / "memory_packs" / f"{canonical}.json"

    async def read_pack(self, project_id: str, chapter: str) -> Optional[Dict[str, Any]]:
        """Read memory pack for a chapter; return None if not found."""
        path = self.get_pack_path(project_id, chapter)
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding=self.encoding) as f:
            raw = await f.read()
            payload = json.loads(raw)
        canonical = self._canonicalize_chapter_id(chapter)
        if canonical:
            payload["chapter"] = canonical
        verification = await self.verify_source_binding(project_id, canonical or chapter, payload)
        payload["stale"] = not verification["valid"]
        payload["stale_reasons"] = verification["reasons"]
        payload["source_verification"] = verification
        return payload

    async def write_pack(self, project_id: str, chapter: str, pack: Dict[str, Any]) -> None:
        """Write (overwrite) memory pack for a chapter.

        Before overwriting, the current file is rotated into a timestamped
        history backup.  At most ``MAX_MEMORY_PACK_HISTORY`` backups are kept.
        """
        path = self.get_pack_path(project_id, chapter)
        self.ensure_dir(path.parent)
        canonical = self._canonicalize_chapter_id(chapter)
        pack = dict(pack or {})
        for runtime_key in ("stale", "stale_reasons", "source_verification"):
            pack.pop(runtime_key, None)
        if canonical:
            pack["chapter"] = canonical
        if not pack.get("built_at"):
            pack["built_at"] = datetime.now(timezone.utc).isoformat()
        scope = current_turn_scope()
        context_epoch = (
            int(scope.context_epoch)
            if scope is not None
            else int((pack.get("source_binding") or {}).get("context_epoch") or pack.get("context_epoch") or 0)
        )
        pack["source_binding"] = await self.build_source_binding(
            project_id,
            canonical or chapter,
            context_epoch=context_epoch,
        )
        payload = json.dumps(pack, ensure_ascii=False, indent=2, default=str)
        async with self.content_transaction(project_id):
            # Rotate existing pack into history before overwriting.
            if path.exists():
                self._rotate_history(path)
            await self._atomic_write(path, payload)

    async def build_source_binding(self, project_id: str, chapter: str, *, context_epoch: int = 0) -> Dict[str, Any]:
        scan = await self._capture_source_revisions_async(project_id, chapter)
        return {
            "schema_version": 1,
            "context_epoch": max(0, int(context_epoch or 0)),
            "source_fingerprint": self._source_fingerprint(scan.revisions),
            "source_revisions": scan.revisions,
            "scan_diagnostics": scan.diagnostics(),
        }

    async def verify_source_binding(self, project_id: str, chapter: str, pack: Dict[str, Any]) -> Dict[str, Any]:
        binding = dict((pack or {}).get("source_binding") or {})
        if not binding:
            return {"valid": False, "reasons": ["source_binding_missing"], "checked": 0}
        stored = list(binding.get("source_revisions") or [])
        reasons = []
        scan = await self._capture_source_revisions_async(project_id, chapter)
        current = scan.revisions
        stored_by_path = {str(item.get("path") or ""): str(item.get("content_sha256") or "") for item in stored}
        current_by_path = {str(item.get("path") or ""): str(item.get("content_sha256") or "") for item in current}
        changes = {"missing": 0, "added": 0, "changed": 0}
        for path in sorted(set(stored_by_path) | set(current_by_path)):
            if path not in current_by_path:
                changes["missing"] += 1
            elif path not in stored_by_path:
                changes["added"] += 1
            elif current_by_path[path] != stored_by_path[path]:
                changes["changed"] += 1
        reasons.extend(name for name, count in changes.items() if count)
        if self._source_fingerprint(current) != str(binding.get("source_fingerprint") or ""):
            reasons.append("source_fingerprint_changed")
        scope = current_turn_scope()
        if scope is not None and int(binding.get("context_epoch") or 0) != int(scope.context_epoch or 0):
            reasons.append("context_epoch_changed")
        return {
            "valid": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "checked": len(set(stored_by_path).intersection(current_by_path)),
            "context_epoch": int(binding.get("context_epoch") or 0),
            "change_counts": changes,
            "scan_diagnostics": scan.diagnostics(changed_files=sum(changes.values())),
        }

    async def profile_source_scan(self, project_id: str, chapter: str = "") -> Dict[str, Any]:
        """Run one content-free source scan for synthetic/local performance evidence."""

        return (await self._capture_source_revisions_async(project_id, chapter)).diagnostics()

    async def _capture_source_revisions_async(self, project_id: str, chapter: str) -> SourceScanResult:
        index_path = self._source_index_path(project_id)
        async with get_file_lock().lock(index_path):
            result = await asyncio.to_thread(self._capture_source_revisions, project_id, chapter)
        try:
            from app.observability.runtime_metrics import runtime_metrics

            runtime_metrics.observe("memory_pack.scan.files", result.files_scanned)
            runtime_metrics.observe("memory_pack.scan.bytes_hashed", result.bytes_hashed)
            runtime_metrics.observe("memory_pack.scan.latency_ms", result.scan_latency_ms)
            runtime_metrics.observe("memory_pack.hash.latency_ms", result.hash_latency_ms)
        except Exception as exc:
            from app.error_contract import record_degradation

            record_degradation("memory_pack_scan_metrics", exc)
        return result

    def _capture_source_revisions(self, project_id: str, chapter: str) -> SourceScanResult:
        started = time.perf_counter()
        project_root = self.get_project_path(project_id)
        del chapter
        if not project_root.exists():
            return SourceScanResult([], 0, 0, 0.0, 0.0, 0.0, "project_root_missing")
        cached_files, fallback_reason = self._load_source_index(project_id)
        scan_started = time.perf_counter()
        paths = self._enumerate_source_files(project_root)
        scan_latency_ms = (time.perf_counter() - scan_started) * 1000.0
        rows = []
        files_scanned = 0
        bytes_hashed = 0
        next_index: Dict[str, Dict[str, Any]] = {}
        hash_started = time.perf_counter()
        for relative, path, stat in paths:
            files_scanned += 1
            path_fingerprint = hashlib.sha256(relative.encode("utf-8")).hexdigest()
            cached = cached_files.get(path_fingerprint) or {}
            if (
                int(cached.get("size", -1)) == int(stat.st_size)
                and int(cached.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
                and cached.get("content_sha256")
            ):
                digest = str(cached["content_sha256"])
                byte_size = int(stat.st_size)
            else:
                content = b""
                for _ in range(3):
                    before = path.stat()
                    content = path.read_bytes()
                    after = path.stat()
                    if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
                        stat = after
                        break
                else:
                    raise RuntimeError(f"memory_pack_source_changed:{path_fingerprint}")
                digest = hashlib.sha256(content).hexdigest()
                byte_size = len(content)
                bytes_hashed += byte_size
            next_index[path_fingerprint] = {
                "size": byte_size,
                "mtime_ns": int(stat.st_mtime_ns),
                "content_sha256": digest,
            }
            rows.append(
                {
                    "path": relative,
                    "path_fingerprint": path_fingerprint,
                    "revision_kind": "content_hash",
                    "revision": digest,
                    "content_sha256": digest,
                    "byte_size": byte_size,
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
        hash_latency_ms = (time.perf_counter() - hash_started) * 1000.0
        self._write_source_index(project_id, next_index)
        return SourceScanResult(
            revisions=rows,
            files_scanned=files_scanned,
            bytes_hashed=bytes_hashed,
            scan_latency_ms=round(scan_latency_ms, 3),
            hash_latency_ms=round(hash_latency_ms, 3),
            total_latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _enumerate_source_files(project_root: Path) -> list[tuple[str, Path, os.stat_result]]:
        rows: list[tuple[str, Path, os.stat_result]] = []

        def walk(root: Path) -> None:
            if not root.exists():
                return
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        walk(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        rows.append((path.relative_to(project_root).as_posix(), path, entry.stat(follow_symlinks=False)))

        project_file = project_root / "project.yaml"
        if project_file.is_file():
            rows.append(("project.yaml", project_file, project_file.stat()))
        for root_name in sorted(_MEMORY_PACK_SOURCE_ROOTS):
            walk(project_root / root_name)
        rows.sort(key=lambda item: item[0])
        return rows

    def _source_index_path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "memory_packs" / "source_revision_index.json"

    def _load_source_index(self, project_id: str) -> tuple[Dict[str, Dict[str, Any]], str]:
        path = self._source_index_path(project_id)
        if not path.exists():
            return {}, "index_missing"
        try:
            payload = json.loads(path.read_text(encoding=self.encoding))
            if int(payload.get("schema_version") or 0) != _SOURCE_INDEX_SCHEMA_VERSION:
                return {}, "index_schema_mismatch"
            files = payload.get("files")
            if not isinstance(files, dict):
                return {}, "index_invalid"
            return {str(key): dict(value) for key, value in files.items() if isinstance(value, dict)}, ""
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return {}, "index_unreadable"

    def _write_source_index(self, project_id: str, files: Dict[str, Dict[str, Any]]) -> None:
        path = self._source_index_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SOURCE_INDEX_SCHEMA_VERSION,
            "classification": "derived_content_hash_manifest",
            "files": files,
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding=self.encoding,
            )
            os.replace(str(temporary), str(path))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _source_fingerprint(revisions: list[Dict[str, Any]]) -> str:
        stable_revisions = [
            {
                "path": str(item.get("path") or ""),
                "content_sha256": str(item.get("content_sha256") or item.get("revision") or ""),
                "byte_size": int(item.get("byte_size") or 0),
            }
            for item in revisions
        ]
        payload = json.dumps(
            stable_revisions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # History rotation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rotate_history(pack_path: Path) -> None:
        """Rename current pack to a timestamped backup and prune old ones."""
        history_dir = pack_path.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        stem = pack_path.stem  # e.g. "V1C01"
        backup_name = f"{stem}_{ts}.json"
        try:
            os.replace(str(pack_path), str(history_dir / backup_name))
        except OSError:
            # Best-effort: skip history if rename fails (e.g. Windows file lock).
            return

        # Prune old backups beyond MAX_MEMORY_PACK_HISTORY.
        prefix = f"{stem}_"
        backups = sorted(
            [p for p in history_dir.iterdir() if p.name.startswith(prefix) and p.suffix == ".json"],
            key=lambda p: p.stat().st_mtime,
        )
        while len(backups) > MAX_MEMORY_PACK_HISTORY:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass

    async def delete_pack(self, project_id: str, chapter: str) -> bool:
        """Delete memory pack for a chapter.

        Args:
            project_id: Target project id.
            chapter: Chapter id.

        Returns:
            True if pack existed and was deleted.
        """
        path = self.get_pack_path(project_id, chapter)
        if path.exists():
            path.unlink()
            return True
        return False

    def build_status(self, chapter: str, pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a lightweight status payload for frontend display."""
        canonical = self._canonicalize_chapter_id(chapter)
        if not pack:
            return {
                "exists": False,
                "chapter": canonical,
            }
        payload = pack.get("payload") or pack.get("working_memory_payload") or {}
        evidence_stats = (payload.get("evidence_pack") or {}).get("stats") or {}
        snapshot = pack.get("card_snapshot") or {}
        return {
            "exists": True,
            "chapter": canonical,
            "built_at": pack.get("built_at"),
            "source": pack.get("source"),
            "stale": bool(pack.get("stale")),
            "stale_reasons": list(pack.get("stale_reasons") or []),
            "context_epoch": int((pack.get("source_binding") or {}).get("context_epoch") or 0),
            "evidence_stats": evidence_stats,
            "card_snapshot": (
                {
                    "characters": len(snapshot.get("characters") or []),
                    "world": len(snapshot.get("world") or []),
                }
                if isinstance(snapshot, dict)
                else {"characters": 0, "world": 0}
            ),
        }
