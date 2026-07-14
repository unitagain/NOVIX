"""Hash-bound tool result artifacts and their runtime contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from app.error_contract import safe_error_code
from app.utils.logger import get_logger

logger = get_logger(__name__)

TOOL_ARTIFACT_SCHEME = "tool-artifact://"
_ARTIFACT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_DEFAULT_PREVIEW_CHARS = 8000


class ToolExecutionStatus(str, Enum):
    """Terminal status for one tool call."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ToolExecutionResult:
    """Provider-independent result for one executed tool call."""

    tool_call_id: str
    tool_name: str
    status: str
    output_preview: str
    artifact_ref: str
    output_hash: str
    error: Optional[Dict[str, Any]]
    elapsed_ms: int
    recoverable: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def replay_metadata(self) -> Dict[str, Any]:
        return {
            "_tool_status": self.status,
            "_recoverable": self.recoverable,
            "_source_ref": self.artifact_ref,
            "_output_hash": self.output_hash,
        }


@dataclass(frozen=True)
class ToolArtifact:
    """Persisted full-output artifact metadata."""

    artifact_ref: str
    artifact_id: str
    output_hash: str
    output_bytes: int
    created_at: float
    expires_at: float


def output_sha256(output: Any) -> str:
    return hashlib.sha256(str(output or "").encode("utf-8")).hexdigest()


def safe_output_preview(output: Any, *, artifact_ref: str = "", limit: int = _DEFAULT_PREVIEW_CHARS) -> str:
    """Return bounded replay content while preserving the recovery pointer."""

    text = str(output or "")
    bounded = max(256, int(limit or _DEFAULT_PREVIEW_CHARS))
    if len(text) <= bounded:
        return text
    marker = f"\n…(完整工具结果已保存：{artifact_ref or 'artifact_unavailable'})"
    return text[: max(0, bounded - len(marker))].rstrip() + marker


class ToolArtifactStore:
    """Bounded filesystem owner for full tool outputs.

    Artifacts live outside provider payloads under ``DATA_DIR/_system``. Writes are
    atomic, refs are opaque, and reads verify both expiry and the recorded hash.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        retention_seconds: int = _DEFAULT_RETENTION_SECONDS,
        max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else self.default_root()
        self.retention_seconds = max(60, int(retention_seconds))
        self.max_artifact_bytes = max(1024, int(max_artifact_bytes))
        self.max_total_bytes = max(self.max_artifact_bytes, int(max_total_bytes))

    @staticmethod
    def default_root() -> Path:
        from app.config import get_settings

        return (Path(get_settings().data_dir).expanduser().resolve() / "_system" / "tool_artifacts").resolve()

    def persist(
        self,
        output: Any,
        *,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        now: Optional[float] = None,
    ) -> ToolArtifact:
        text = str(output or "")
        output_bytes = len(text.encode("utf-8"))
        if output_bytes > self.max_artifact_bytes:
            raise ValueError("tool_artifact_size_exceeded")

        created_at = float(now if now is not None else time.time())
        expires_at = created_at + self.retention_seconds
        artifact_id = uuid.uuid4().hex
        output_hash = output_sha256(text)
        payload = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "turn_id": str(turn_id or ""),
            "tool_call_id": str(tool_call_id or ""),
            "tool_name": str(tool_name or ""),
            "status": str(status or ""),
            "output": text,
            "output_hash": output_hash,
            "output_bytes": output_bytes,
            "created_at": created_at,
            "expires_at": expires_at,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        serialized_bytes = serialized.encode("utf-8")
        if len(serialized_bytes) > self.max_total_bytes:
            raise ValueError("tool_artifact_capacity_exceeded")

        self.root.mkdir(parents=True, exist_ok=True)
        self.cleanup(now=created_at)
        destination = self.root / f"{artifact_id}.json"
        temporary = self.root / f".{artifact_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as handle:
                handle.write(serialized_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError as exc:
                    logger.warning("Tool artifact temp cleanup failed: code=%s", safe_error_code(exc))
        self.cleanup(now=created_at)
        return ToolArtifact(
            artifact_ref=f"{TOOL_ARTIFACT_SCHEME}{artifact_id}",
            artifact_id=artifact_id,
            output_hash=output_hash,
            output_bytes=output_bytes,
            created_at=created_at,
            expires_at=expires_at,
        )

    def read(self, artifact_ref: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        path = self._path_for_ref(artifact_ref)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("artifact_id") or "") != path.stem:
            raise ValueError("tool_artifact_identity_mismatch")
        if float(payload.get("expires_at") or 0) <= float(now if now is not None else time.time()):
            raise FileNotFoundError("tool_artifact_expired")
        output = str(payload.get("output") or "")
        if output_sha256(output) != str(payload.get("output_hash") or ""):
            raise ValueError("tool_artifact_hash_mismatch")
        return payload

    def exists(self, artifact_ref: str, *, now: Optional[float] = None) -> bool:
        try:
            self.read(artifact_ref, now=now)
            return True
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def cleanup(self, *, now: Optional[float] = None) -> Dict[str, int]:
        """Remove expired artifacts, then oldest artifacts until within capacity."""

        if not self.root.exists():
            return {"removed": 0, "remaining_bytes": 0}
        current = float(now if now is not None else time.time())
        rows = []
        removed = 0
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                created_at = float(payload.get("created_at") or path.stat().st_mtime)
                expires_at = float(payload.get("expires_at") or 0)
                size = path.stat().st_size
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("Invalid tool artifact scheduled for cleanup: code=%s", safe_error_code(exc))
                created_at = path.stat().st_mtime
                expires_at = 0
                size = path.stat().st_size
            if expires_at <= current:
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("Expired tool artifact cleanup failed: code=%s", safe_error_code(exc))
                continue
            rows.append((created_at, path, size))

        remaining = sum(row[2] for row in rows)
        for _, path, size in sorted(rows, key=lambda row: (row[0], row[1].name)):
            if remaining <= self.max_total_bytes:
                break
            try:
                path.unlink()
                remaining -= size
                removed += 1
            except OSError as exc:
                logger.warning("Tool artifact capacity cleanup failed: code=%s", safe_error_code(exc))
        return {"removed": removed, "remaining_bytes": max(0, remaining)}

    def _path_for_ref(self, artifact_ref: str) -> Path:
        ref = str(artifact_ref or "")
        if not ref.startswith(TOOL_ARTIFACT_SCHEME):
            raise ValueError("invalid_tool_artifact_ref")
        artifact_id = ref[len(TOOL_ARTIFACT_SCHEME) :]
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ValueError("invalid_tool_artifact_ref")
        return self.root / f"{artifact_id}.json"


def is_tool_artifact_ref(artifact_ref: str) -> bool:
    ref = str(artifact_ref or "")
    return ref.startswith(TOOL_ARTIFACT_SCHEME) and bool(_ARTIFACT_ID_RE.fullmatch(ref[len(TOOL_ARTIFACT_SCHEME) :]))


def tool_artifact_exists(artifact_ref: str) -> bool:
    """Validate a default-store ref before gateway folding."""

    return is_tool_artifact_ref(artifact_ref) and ToolArtifactStore().exists(artifact_ref)
