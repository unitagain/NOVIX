"""Structured, recoverable conversation compaction artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional


COMPACT_ARTIFACT_VERSION = 2


def stable_message_hash(messages: Iterable[Dict[str, Any]]) -> str:
    payload = json.dumps(list(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass
class CompactArtifactV2:
    id: str
    epoch: int
    parent_epoch: Optional[int]
    decisions: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    entity_state: List[str] = field(default_factory=list)
    open_loops: List[str] = field(default_factory=list)
    recent_summary: str = ""
    source_range: Dict[str, Any] = field(default_factory=dict)
    recovery_refs: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    prompt_fingerprint: str = ""
    parent_artifact_id: str = ""
    version_refs: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = COMPACT_ARTIFACT_VERSION

    @classmethod
    def from_summary(
        cls,
        *,
        artifact_id: str,
        epoch: int,
        parent_epoch: Optional[int],
        summary: Any,
        source_messages: List[Dict[str, Any]],
        recovery_refs: List[str],
        parent_artifact_id: str = "",
        provenance: Optional[Dict[str, Any]] = None,
    ) -> "CompactArtifactV2":
        structured = summary if isinstance(summary, dict) else {}
        recent_summary = str(structured.get("recent_summary") or (summary if isinstance(summary, str) else "")).strip()
        event_ids = [str(item.get("event_id") or "") for item in source_messages if item.get("event_id")]
        source_range = {
            "count": len(source_messages),
            "first_event_id": event_ids[0] if event_ids else "",
            "last_event_id": event_ids[-1] if event_ids else "",
            "sha256": stable_message_hash(source_messages),
        }
        provenance = provenance or {}
        version_refs = [
            {
                "asset_type": "session_events",
                "revision": source_range["sha256"],
                "context_epoch": epoch,
            }
        ]
        if parent_artifact_id:
            version_refs.append(
                {
                    "asset_type": "compact",
                    "artifact_id": parent_artifact_id,
                    "revision": str(parent_epoch or 0),
                    "context_epoch": int(parent_epoch or 0),
                }
            )
        return cls(
            id=artifact_id,
            epoch=epoch,
            parent_epoch=parent_epoch,
            decisions=_strings(structured.get("decisions")),
            constraints=_strings(structured.get("constraints")),
            entity_state=_strings(structured.get("entity_state")),
            open_loops=_strings(structured.get("open_loops")),
            recent_summary=recent_summary,
            source_range=source_range,
            recovery_refs=list(dict.fromkeys(str(item) for item in recovery_refs if item)),
            provider=str(provenance.get("provider") or ""),
            model=str(provenance.get("model") or ""),
            prompt_fingerprint=str(provenance.get("prompt_fingerprint") or ""),
            parent_artifact_id=parent_artifact_id,
            version_refs=version_refs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CompactVerifier:
    """Deterministic structural and recoverability checks before commit."""

    @staticmethod
    def verify(artifact: CompactArtifactV2, source_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        if artifact.schema_version != COMPACT_ARTIFACT_VERSION:
            errors.append("unsupported_schema_version")
        if artifact.epoch < 1:
            errors.append("invalid_epoch")
        if artifact.parent_epoch is not None and artifact.parent_epoch >= artifact.epoch:
            errors.append("invalid_parent_epoch")
        if not artifact.recent_summary:
            errors.append("empty_recent_summary")
        expected_hash = stable_message_hash(source_messages)
        if artifact.source_range.get("sha256") != expected_hash:
            errors.append("source_hash_mismatch")
        if int(artifact.source_range.get("count") or -1) != len(source_messages):
            errors.append("source_count_mismatch")
        event_ids = [str(item.get("event_id") or "") for item in source_messages if item.get("event_id")]
        missing_refs = [event_id for event_id in event_ids if event_id not in artifact.recovery_refs]
        if missing_refs:
            errors.append("missing_recovery_refs")
        if not any((artifact.decisions, artifact.constraints, artifact.entity_state, artifact.open_loops)):
            warnings.append("unstructured_summary")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "source_count": len(source_messages),
            "recoverable": not missing_refs,
        }
