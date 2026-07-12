"""Content-addressed source snapshots for frozen context plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.control_plane.store import SQLiteControlStore
from app.error_contract import safe_error_code


@dataclass(frozen=True)
class SourceRevision:
    path: str
    asset_type: str
    revision: str
    revision_kind: str
    content_sha256: str
    byte_size: int
    context_epoch: int = 0
    artifact_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _stable_file_bytes(path: Path, *, attempts: int = 3) -> bytes:
    for _ in range(max(1, attempts)):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return content
    raise RuntimeError(f"source_changed_while_snapshotting:{path.name}")


def _asset_type(relative: Path) -> str:
    first = relative.parts[0] if relative.parts else "source"
    return {
        "canon": "canon",
        "memory": "memory",
        "memory_packs": "memory_pack",
        "sessions": "session",
        "drafts": "draft",
    }.get(first, "source")


def _draft_revision(store: Optional[SQLiteControlStore], project_id: str, relative: Path) -> Optional[int]:
    if store is None or len(relative.parts) < 3 or relative.parts[0] != "drafts":
        return None
    chapter = relative.parts[1]
    filename = relative.name
    if filename == "final.md" or (filename.startswith("draft_") and filename.endswith(".md")):
        return int(store.get_revision("draft", f"{project_id}/{chapter}/{filename}")["revision"])
    return None


def capture_source_snapshot(
    project_root: Path,
    *,
    chapter_id: str = "",
    context_epoch: int = 0,
) -> List[Dict[str, Any]]:
    """Hash authoritative files without treating mtime or size as identity."""

    project_root = Path(project_root).resolve()
    compact_state = project_root / "sessions" / "compact" / "state.json"
    candidates = [
        project_root / "canon",
        project_root / "memory",
        project_root / "sessions" / "conversation.jsonl",
        compact_state,
    ]
    if chapter_id:
        candidates.append(project_root / "drafts" / chapter_id)
        candidates.append(project_root / "memory_packs" / f"{chapter_id}.json")
    elif (project_root / "memory_packs").exists():
        candidates.extend(sorted((project_root / "memory_packs").glob("*.json")))
    if compact_state.exists():
        try:
            state = json.loads(_stable_file_bytes(compact_state).decode("utf-8-sig")) or {}
            artifact_id = str(state.get("artifact_id") or "")
            if artifact_id:
                candidates.append(project_root / "sessions" / "compact" / f"{artifact_id}.json")
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            pass

    control_path = project_root.parent / "_system" / "control.sqlite3"
    store = SQLiteControlStore(control_path) if control_path.exists() else None
    rows: List[SourceRevision] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        paths = [candidate] if candidate.is_file() else sorted(
            path
            for path in candidate.rglob("*")
            if path.is_file() and "history" not in path.relative_to(candidate).parts
        )
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = resolved.relative_to(project_root)
            content = _stable_file_bytes(resolved)
            digest = hashlib.sha256(content).hexdigest()
            draft_revision = _draft_revision(store, project_root.name, relative)
            artifact_id = ""
            if relative.name == "state.json" and relative.parts[:2] == ("sessions", "compact"):
                try:
                    artifact_id = str((json.loads(content.decode("utf-8-sig")) or {}).get("artifact_id") or "")
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    artifact_id = ""
            elif relative.parts[:2] == ("sessions", "compact") and relative.stem.startswith("compact_epoch_"):
                artifact_id = relative.stem
            rows.append(
                SourceRevision(
                    path=relative.as_posix(),
                    asset_type=_asset_type(relative),
                    revision=str(draft_revision if draft_revision is not None else digest),
                    revision_kind="control_store" if draft_revision is not None else "content_hash",
                    content_sha256=digest,
                    byte_size=len(content),
                    context_epoch=max(0, int(context_epoch or 0)),
                    artifact_id=artifact_id,
                )
            )
    return [row.to_dict() for row in rows]


def verify_source_snapshot(project_root: Path, snapshot: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify that every planned source revision is still available unchanged."""

    project_root = Path(project_root).resolve()
    control_path = project_root.parent / "_system" / "control.sqlite3"
    store = SQLiteControlStore(control_path) if control_path.exists() else None
    failures: List[Dict[str, str]] = []
    checked = 0
    for row in snapshot:
        relative = str(row.get("path") or "")
        path = (project_root / relative).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            failures.append({"path": relative, "reason": "path_outside_project"})
            continue
        if not path.is_file():
            failures.append({"path": relative, "reason": "source_revision_unavailable"})
            continue
        try:
            content = _stable_file_bytes(path)
        except (OSError, RuntimeError) as exc:
            failures.append({"path": relative, "reason": safe_error_code(exc)})
            continue
        checked += 1
        actual = hashlib.sha256(content).hexdigest()
        if actual != str(row.get("content_sha256") or ""):
            failures.append({"path": relative, "reason": "content_sha256_mismatch"})
            continue
        if row.get("revision_kind") == "control_store":
            actual_revision = _draft_revision(store, project_root.name, Path(relative))
            if actual_revision is None or str(actual_revision) != str(row.get("revision") or ""):
                failures.append({"path": relative, "reason": "control_revision_mismatch"})
    return {"valid": not failures, "checked": checked, "failures": failures}
