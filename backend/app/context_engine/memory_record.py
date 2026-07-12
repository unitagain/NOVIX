"""Versioned creative-memory contract and lifecycle helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


MEMORY_RECORD_VERSION = 2
MEMORY_TERMINAL_STATUSES = {"superseded", "expired", "rejected"}
MEMORY_RECALLABLE_STATUS = "active"
MEMORY_STATUSES_V2 = ("candidate", "needs_review", "active", "superseded", "expired", "rejected")
MEMORY_TRANSITIONS = {
    "candidate": {"active", "needs_review", "rejected"},
    "needs_review": {"active", "rejected"},
    "active": {"needs_review", "superseded", "expired", "rejected"},
    "superseded": set(),
    "expired": set(),
    "rejected": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    text = str(value or "").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = [part.strip() for part in text.split(",")]
    if not isinstance(decoded, list):
        decoded = [decoded]
    return list(dict.fromkeys(str(item).strip() for item in decoded if str(item).strip()))


def encode_string_list(values: Iterable[Any]) -> str:
    normalized = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def parse_version_refs(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict) and item.get("asset_type")]


def memory_transition_allowed(current: str, target: str) -> bool:
    return current == target or target in MEMORY_TRANSITIONS.get(current, set())


@dataclass
class MemoryRecordV2:
    id: str
    name: str
    description: str
    type: str
    scope: str
    content: str
    source: str = "legacy"
    source_refs: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    trust_label: str = "trusted"
    confidence: float = 1.0
    confidence_method: str = "legacy"
    status: str = MEMORY_RECALLABLE_STATUS
    valid_from: str = ""
    expires_at: str = ""
    supersedes: List[str] = field(default_factory=list)
    conflicts_with: List[str] = field(default_factory=list)
    created_by: str = "legacy"
    confirmed_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    recall_reason: str = ""
    recall_score: float = 0.0
    last_recalled_at: str = ""
    source_type: str = "internal"
    activation: str = "legacy"
    version_refs: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = MEMORY_RECORD_VERSION

    @classmethod
    def from_mapping(cls, value: Dict[str, Any]) -> "MemoryRecordV2":
        status = str(value.get("status") or "active")
        if status not in MEMORY_STATUSES_V2:
            status = "active"
        now = str(value.get("updated_at") or value.get("created_at") or "")
        return cls(
            id=str(value.get("id") or value.get("slug") or value.get("name") or "memory"),
            name=str(value.get("name") or value.get("slug") or "memory"),
            description=str(value.get("description") or ""),
            type=str(value.get("type") or "preference"),
            scope=str(value.get("scope") or "project"),
            content=str(value.get("content") or value.get("body") or ""),
            source=str(value.get("source") or "legacy"),
            source_refs=parse_string_list(value.get("source_refs")),
            evidence_refs=parse_string_list(value.get("evidence_refs")),
            trust_label=str(value.get("trust_label") or "trusted"),
            confidence=max(0.0, min(1.0, float(value.get("confidence") or 0.0))),
            confidence_method=str(value.get("confidence_method") or "legacy"),
            status=status,
            valid_from=str(value.get("valid_from") or ""),
            expires_at=str(value.get("expires_at") or ""),
            supersedes=parse_string_list(value.get("supersedes")),
            conflicts_with=parse_string_list(value.get("conflicts_with")),
            created_by=str(value.get("created_by") or "legacy"),
            confirmed_by=str(value.get("confirmed_by") or ""),
            created_at=str(value.get("created_at") or now),
            updated_at=now,
            recall_reason=str(value.get("recall_reason") or ""),
            recall_score=float(value.get("recall_score") or 0.0),
            last_recalled_at=str(value.get("last_recalled_at") or ""),
            source_type=str(value.get("source_type") or "internal"),
            activation=str(value.get("activation") or "legacy"),
            version_refs=parse_version_refs(value.get("version_refs")),
            schema_version=MEMORY_RECORD_VERSION,
        )

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        deadline = parse_datetime(self.expires_at)
        return bool(deadline and deadline <= (now or datetime.now(timezone.utc)))

    def is_valid_at(self, *, now: Optional[datetime] = None) -> bool:
        current = now or datetime.now(timezone.utc)
        valid_from = parse_datetime(self.valid_from)
        return not valid_from or valid_from <= current

    def recall_block_reasons(self, unresolved_conflicts: Iterable[str] = ()) -> List[str]:
        reasons: List[str] = []
        if self.status != MEMORY_RECALLABLE_STATUS:
            reasons.append(f"status:{self.status}")
        if self.is_expired():
            reasons.append("expired")
        if not self.is_valid_at():
            reasons.append("not_yet_valid")
        if self.trust_label == "untrusted":
            reasons.append("untrusted")
        unresolved = set(unresolved_conflicts)
        if unresolved.intersection(self.conflicts_with):
            reasons.append("unresolved_conflict")
        return reasons

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["body"] = value.pop("content")
        value["slug"] = self.name
        return value
