"""Structured, recoverable conversation compaction artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.context_engine.token_accounting import count_provider_payload, count_text_tokens


COMPACT_ARTIFACT_VERSION = 3


def stable_message_hash(messages: Iterable[Dict[str, Any]]) -> str:
    payload = json.dumps(list(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def compact_summary_payload(summary: Any) -> Dict[str, Any]:
    """Normalize string or structured summaries for stable accounting."""

    structured = summary if isinstance(summary, dict) else {}
    return {
        "decisions": _strings(structured.get("decisions")),
        "constraints": _strings(structured.get("constraints")),
        "entity_state": _strings(structured.get("entity_state")),
        "open_loops": _strings(structured.get("open_loops")),
        "recent_summary": str(
            structured.get("recent_summary") or (summary if isinstance(summary, str) else "")
        ).strip(),
    }


def _summary_payload(artifact: "CompactArtifactV2") -> Dict[str, Any]:
    return {
        "decisions": artifact.decisions,
        "constraints": artifact.constraints,
        "entity_state": artifact.entity_state,
        "open_loops": artifact.open_loops,
        "recent_summary": artifact.recent_summary,
    }


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
    selected_turn_ids: List[str] = field(default_factory=list)
    preserved_tail_id: str = ""
    source_token_estimate: int = 0
    summary_token_estimate: int = 0
    accounting_estimator: str = ""
    source_accounting: Dict[str, Any] = field(default_factory=dict)
    summary_accounting: Dict[str, Any] = field(default_factory=dict)
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
        selected_turn_ids: Optional[List[str]] = None,
        preserved_tail_id: str = "",
        source_accounting: Optional[Dict[str, Any]] = None,
        summary_accounting: Optional[Dict[str, Any]] = None,
    ) -> "CompactArtifactV2":
        structured = summary if isinstance(summary, dict) else {}
        normalized_summary = compact_summary_payload(summary)
        recent_summary = normalized_summary["recent_summary"]
        event_ids = [str(item.get("event_id") or "") for item in source_messages if item.get("event_id")]
        source_range = {
            "count": len(source_messages),
            "first_event_id": event_ids[0] if event_ids else "",
            "last_event_id": event_ids[-1] if event_ids else "",
            "sha256": stable_message_hash(source_messages),
            "selected_turn_count": len(selected_turn_ids or []),
            "preserved_tail_id": str(preserved_tail_id or ""),
        }
        provenance = provenance or {}
        provider = str(provenance.get("provider") or "")
        model = str(provenance.get("model") or "")
        source_accounting = dict(
            source_accounting
            or count_provider_payload(source_messages, provider=provider, model=model).to_dict()
        )
        summary_value = normalized_summary
        summary_accounting = dict(
            summary_accounting or count_text_tokens(summary_value, provider=provider, model=model).to_dict()
        )
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
            provider=provider,
            model=model,
            prompt_fingerprint=str(provenance.get("prompt_fingerprint") or ""),
            parent_artifact_id=parent_artifact_id,
            version_refs=version_refs,
            selected_turn_ids=list(dict.fromkeys(selected_turn_ids or [])),
            preserved_tail_id=str(preserved_tail_id or ""),
            source_token_estimate=int(source_accounting.get("tokens") or 0),
            summary_token_estimate=int(summary_accounting.get("tokens") or 0),
            accounting_estimator=str(source_accounting.get("tokenizer") or ""),
            source_accounting=source_accounting,
            summary_accounting=summary_accounting,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CompactVerifier:
    """Deterministic structural and recoverability checks before commit."""

    @staticmethod
    def verify(
        artifact: CompactArtifactV2,
        source_messages: List[Dict[str, Any]],
        *,
        parent_artifact: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        if artifact.schema_version != COMPACT_ARTIFACT_VERSION:
            errors.append("unsupported_schema_version")
        if artifact.epoch < 1:
            errors.append("invalid_epoch")
        if artifact.parent_epoch is not None and artifact.parent_epoch >= artifact.epoch:
            errors.append("invalid_parent_epoch")
        if artifact.parent_artifact_id == artifact.id:
            errors.append("lineage_cycle")
        if artifact.parent_artifact_id:
            if parent_artifact is None:
                errors.append("parent_artifact_missing")
            else:
                if str(parent_artifact.get("id") or "") != artifact.parent_artifact_id:
                    errors.append("parent_artifact_mismatch")
                if int(parent_artifact.get("epoch") or 0) != int(artifact.parent_epoch or 0):
                    errors.append("parent_epoch_mismatch")
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
        if len(artifact.selected_turn_ids) != len(set(artifact.selected_turn_ids)):
            errors.append("duplicate_selected_turn_ids")
        provider = str((artifact.source_accounting or {}).get("provider") or artifact.provider or "")
        model = str((artifact.source_accounting or {}).get("model") or artifact.model or "")
        expected_source_accounting = count_provider_payload(
            source_messages,
            provider=provider,
            model=model,
        )
        if artifact.source_token_estimate != expected_source_accounting.tokens:
            errors.append("source_token_estimate_mismatch")
        expected_summary_accounting = count_text_tokens(
            _summary_payload(artifact),
            provider=str((artifact.summary_accounting or {}).get("provider") or artifact.provider or ""),
            model=str((artifact.summary_accounting or {}).get("model") or artifact.model or ""),
        )
        if artifact.summary_token_estimate != expected_summary_accounting.tokens:
            errors.append("summary_token_estimate_mismatch")
        if artifact.accounting_estimator != expected_source_accounting.tokenizer:
            errors.append("accounting_estimator_mismatch")
        if not any((artifact.decisions, artifact.constraints, artifact.entity_state, artifact.open_loops)):
            warnings.append("unstructured_summary")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "source_count": len(source_messages),
            "recoverable": not missing_refs,
            "selected_turn_count": len(artifact.selected_turn_ids),
            "source_accounting": expected_source_accounting.to_dict(),
            "summary_accounting": expected_summary_accounting.to_dict(),
        }
