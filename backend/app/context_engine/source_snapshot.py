"""Actual source descriptors, registry, and revision verification.

中文说明：本模块是 ContextPlan 与 provider payload 的唯一 source registry 实现。
它以实际装配内容为准，不用目录白名单推断模型看到了什么。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.control_plane.store import SQLiteControlStore
from app.context_engine.token_accounting import count_text_tokens
from app.error_contract import safe_error_code


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _content_bytes(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return _stable_json(content).encode("utf-8")


def content_sha256(content: Any) -> str:
    """Return a stable content hash for text, bytes, or structured values."""

    return hashlib.sha256(_content_bytes(content)).hexdigest()


def _estimated_tokens(content: Any) -> int:
    return count_text_tokens(content).tokens if _content_bytes(content) else 0


@dataclass(frozen=True)
class SourceDescriptor:
    """One planned or actual provider input source."""

    source_id: str
    asset_type: str
    revision_kind: str
    content_sha256: str
    trust_label: str
    selection_reason: str
    estimated_tokens: int
    path: str = ""
    artifact_ref: str = ""
    revision: str = ""
    byte_size: int = 0
    mutable: bool = False
    payload_fragment: bool = False
    required: bool = False

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        return content_sha256(payload)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceDescriptor":
        return cls(
            source_id=str(value.get("source_id") or value.get("id") or value.get("type") or "source"),
            asset_type=str(value.get("asset_type") or value.get("type") or "source"),
            revision_kind=str(value.get("revision_kind") or "unresolved"),
            content_sha256=str(value.get("content_sha256") or ""),
            trust_label=str(value.get("trust_label") or "trusted"),
            selection_reason=str(value.get("selection_reason") or value.get("role") or "planned"),
            estimated_tokens=max(0, int(value.get("estimated_tokens") or 0)),
            path=str(value.get("path") or ""),
            artifact_ref=str(value.get("artifact_ref") or value.get("artifact_id") or ""),
            revision=str(value.get("revision") or ""),
            byte_size=max(0, int(value.get("byte_size") or 0)),
            mutable=bool(value.get("mutable")),
            payload_fragment=bool(value.get("payload_fragment")),
            required=bool(value.get("required")),
        )

    @classmethod
    def planned(
        cls,
        *,
        source_id: str,
        asset_type: str,
        selection_reason: str,
        trust_label: str = "trusted",
        required: bool = False,
        content: Any = None,
        artifact_ref: str = "",
    ) -> "SourceDescriptor":
        has_content = content is not None
        raw = _content_bytes(content) if has_content else b""
        digest = hashlib.sha256(raw).hexdigest() if has_content else ""
        return cls(
            source_id=source_id,
            asset_type=asset_type,
            revision_kind="content_hash" if has_content else "planned",
            content_sha256=digest,
            trust_label=trust_label,
            selection_reason=selection_reason,
            estimated_tokens=_estimated_tokens(content) if has_content else 0,
            artifact_ref=artifact_ref,
            revision=digest,
            byte_size=len(raw),
            required=required,
        )

    @classmethod
    def from_content(
        cls,
        *,
        source_id: str,
        asset_type: str,
        content: Any,
        selection_reason: str,
        trust_label: str = "trusted",
        artifact_ref: str = "",
        payload_fragment: bool = False,
        required: bool = False,
    ) -> "SourceDescriptor":
        raw = _content_bytes(content)
        digest = hashlib.sha256(raw).hexdigest()
        return cls(
            source_id=source_id,
            asset_type=asset_type,
            revision_kind="content_hash",
            content_sha256=digest,
            trust_label=trust_label,
            selection_reason=selection_reason,
            estimated_tokens=_estimated_tokens(content),
            artifact_ref=artifact_ref,
            revision=digest,
            byte_size=len(raw),
            payload_fragment=payload_fragment,
            required=required,
        )


def _stable_file_bytes(path: Path, *, attempts: int = 3) -> bytes:
    for _ in range(max(1, attempts)):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return content
    raise RuntimeError(f"source_changed_while_snapshotting:{path.name}")


def _draft_revision(store: Optional[SQLiteControlStore], project_id: str, relative: Path) -> Optional[int]:
    if store is None or len(relative.parts) < 3 or relative.parts[0] != "drafts":
        return None
    filename = relative.name
    if filename != "final.md" and not (filename.startswith("draft_") and filename.endswith(".md")):
        return None
    chapter = relative.parts[1]
    return int(store.get_revision("draft", f"{project_id}/{chapter}/{filename}")["revision"])


def _control_store(project_root: Path) -> Optional[SQLiteControlStore]:
    control_path = project_root.parent / "_system" / "control.sqlite3"
    return SQLiteControlStore(control_path) if control_path.exists() else None


def descriptor_from_file(
    project_root: Path,
    path: Path,
    *,
    source_id: str,
    asset_type: str,
    selection_reason: str,
    trust_label: str = "trusted",
    required: bool = False,
) -> SourceDescriptor:
    """Capture one mutable project file with content and control revisions."""

    root = Path(project_root).resolve()
    resolved = Path(path).resolve()
    relative = resolved.relative_to(root)
    content = _stable_file_bytes(resolved)
    digest = hashlib.sha256(content).hexdigest()
    draft_revision = _draft_revision(_control_store(root), root.name, relative)
    return SourceDescriptor(
        source_id=source_id,
        asset_type=asset_type,
        revision_kind="control_store" if draft_revision is not None else "content_hash",
        content_sha256=digest,
        trust_label=trust_label,
        selection_reason=selection_reason,
        estimated_tokens=_estimated_tokens(content),
        path=relative.as_posix(),
        revision=str(draft_revision if draft_revision is not None else digest),
        byte_size=len(content),
        mutable=True,
        required=required,
    )


def _payload_fragments(
    messages: Sequence[Mapping[str, Any]], tools: Optional[Sequence[Mapping[str, Any]]]
) -> List[Dict[str, Any]]:
    fragments: List[Dict[str, Any]] = []
    for index, message in enumerate(messages or []):
        fragments.append(
            {
                "kind": "message",
                "index": index,
                "role": str(message.get("role") or ""),
                "content": dict(message),
                "content_sha256": content_sha256(dict(message)),
            }
        )
    for index, tool in enumerate(tools or []):
        fragments.append(
            {
                "kind": "tool_schema",
                "index": index,
                "role": "tool",
                "content": dict(tool),
                "content_sha256": content_sha256(dict(tool)),
            }
        )
    return fragments


class SourceRegistry:
    """Turn-local owner of planned sources and actual provider input fragments."""

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        planned: Iterable[Mapping[str, Any] | SourceDescriptor] = (),
    ) -> None:
        self.project_root = Path(project_root).resolve() if project_root else None
        self._planned: Dict[str, SourceDescriptor] = {}
        self._actual: Dict[str, SourceDescriptor] = {}
        for item in planned:
            self.register_planned(item)

    def register_planned(self, value: Mapping[str, Any] | SourceDescriptor) -> SourceDescriptor:
        descriptor = value if isinstance(value, SourceDescriptor) else SourceDescriptor.from_mapping(value)
        self._planned[descriptor.source_id] = descriptor
        return descriptor

    def register_actual(self, value: Mapping[str, Any] | SourceDescriptor) -> SourceDescriptor:
        descriptor = value if isinstance(value, SourceDescriptor) else SourceDescriptor.from_mapping(value)
        self._actual[descriptor.fingerprint] = descriptor
        return descriptor

    def register_content(self, **kwargs: Any) -> SourceDescriptor:
        return self.register_actual(SourceDescriptor.from_content(**kwargs))

    def register_file(self, path: Path, **kwargs: Any) -> SourceDescriptor:
        if self.project_root is None:
            raise ValueError("source_registry_project_root_missing")
        return self.register_actual(descriptor_from_file(self.project_root, path, **kwargs))

    def register_materialized_planned(self) -> None:
        for descriptor in self._planned.values():
            if descriptor.content_sha256 and descriptor.revision_kind == "content_hash":
                self.register_actual(descriptor)

    def register_payload(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        source_prefix: str,
        selection_reason: str,
        artifact_ref: str = "",
    ) -> List[SourceDescriptor]:
        rows: List[SourceDescriptor] = []
        for fragment in _payload_fragments(messages, tools):
            digest = str(fragment["content_sha256"])
            descriptor = SourceDescriptor.from_content(
                source_id=f"{source_prefix}.{fragment['kind']}.{digest[:16]}",
                asset_type="provider_message" if fragment["kind"] == "message" else "tool_schema",
                content=fragment["content"],
                selection_reason=selection_reason,
                artifact_ref=artifact_ref or f"provider_payload:{fragment['kind']}",
                payload_fragment=True,
                required=True,
            )
            rows.append(self.register_actual(descriptor))
        return rows

    def verify_payload(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        registered = {
            row.content_sha256 for row in self._actual.values() if row.payload_fragment and row.content_sha256
        }
        missing = [
            {key: value for key, value in fragment.items() if key != "content"}
            for fragment in _payload_fragments(messages, tools)
            if fragment["content_sha256"] not in registered
        ]
        return {
            "valid": not missing,
            "registered_fragments": len(registered),
            "payload_fragments": len(_payload_fragments(messages, tools)),
            "missing": missing,
        }

    def verify_mutable_sources(self) -> Dict[str, Any]:
        rows = [row.to_dict() for row in self._actual.values() if row.mutable]
        if not rows:
            return {"valid": True, "checked": 0, "failures": []}
        if self.project_root is None:
            return {
                "valid": False,
                "checked": 0,
                "failures": [{"path": "", "reason": "source_registry_project_root_missing"}],
            }
        return verify_source_snapshot(self.project_root, rows)

    def reconcile(self) -> Dict[str, Any]:
        planned = list(self._planned.values())
        actual = list(self._actual.values())
        planned_ids = {row.source_id for row in planned}
        planned_types = {row.asset_type for row in planned}
        actual_ids = {row.source_id for row in actual}
        actual_types = {row.asset_type for row in actual}
        unexpected = [
            row for row in actual if row.source_id not in planned_ids and row.asset_type not in planned_types
        ]
        not_selected = [
            row for row in planned if row.source_id not in actual_ids and row.asset_type not in actual_types
        ]
        return {
            "planned": [row.to_dict() for row in planned],
            "actual": [row.to_dict() for row in actual],
            "unexpected": [row.to_dict() for row in unexpected],
            "not_selected": [row.to_dict() for row in not_selected],
            "summary": {
                "planned_types": sorted(planned_types),
                "actual_types": sorted(actual_types),
                "unexpected_types": sorted({row.asset_type for row in unexpected}),
                "not_selected_types": sorted({row.asset_type for row in not_selected}),
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.reconcile()


def capture_source_snapshot(
    project_root: Path,
    *,
    chapter_id: str = "",
    context_epoch: int = 0,
) -> List[Dict[str, Any]]:
    """Compatibility scan without a fixed source-directory whitelist.

    New runtime code must register actual reads in ``SourceRegistry``. This scan
    remains only for legacy callers and migration diagnostics.
    """

    del chapter_id, context_epoch
    root = Path(project_root).resolve()
    if not root.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in {"history", "traces", "__pycache__"} for part in relative.parts):
            continue
        try:
            descriptor = descriptor_from_file(
                root,
                path,
                source_id=f"legacy.{relative.as_posix()}",
                asset_type=relative.parts[0] if relative.parts else "project_file",
                selection_reason="legacy_snapshot_compatibility",
            )
        except (OSError, RuntimeError, ValueError):
            continue
        rows.append(descriptor.to_dict())
    return rows


def verify_source_snapshot(project_root: Path, snapshot: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify every mutable descriptor is still available unchanged."""

    root = Path(project_root).resolve()
    store = _control_store(root)
    failures: List[Dict[str, str]] = []
    checked = 0
    for row in snapshot:
        relative = str(row.get("path") or "")
        if not relative:
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
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
            actual_revision = _draft_revision(store, root.name, Path(relative))
            if actual_revision is None or str(actual_revision) != str(row.get("revision") or ""):
                failures.append({"path": relative, "reason": "control_revision_mismatch"})
    return {"valid": not failures, "checked": checked, "failures": failures}
