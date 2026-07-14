"""Versioned creative-memory contract and lifecycle helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


MEMORY_RECORD_VERSION = 2
MEMORY_TERMINAL_STATUSES = {"superseded", "expired", "rejected"}
MEMORY_RECALLABLE_STATUS = "active"
MEMORY_REVIEW_STATUS = "needs_review"
MEMORY_STATUSES_V2 = ("candidate", "needs_review", "active", "superseded", "expired", "rejected")
MEMORY_TRANSITIONS = {
    "candidate": {"active", "needs_review", "expired", "rejected"},
    "needs_review": {"active", "expired", "rejected"},
    "active": {"needs_review", "superseded", "expired", "rejected"},
    "superseded": set(),
    "expired": set(),
    "rejected": set(),
}


def normalize_memory_status(value: Any, *, default: str = MEMORY_REVIEW_STATUS) -> str:
    status = str(value or "").strip().lower()
    return status if status in MEMORY_STATUSES_V2 else default


def normalize_trust_label(value: Any) -> str:
    label = str(value or "").strip().lower()
    return label if label in {"trusted", "untrusted"} else "unknown"


def normalize_confidence(value: Any, *, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(1.0, score))


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


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def memory_transition_allowed(current: str, target: str) -> bool:
    normalized_current = normalize_memory_status(current)
    normalized_target = normalize_memory_status(target, default="")
    return bool(normalized_target) and (
        normalized_current == normalized_target
        or normalized_target in MEMORY_TRANSITIONS.get(normalized_current, set())
    )


def memory_has_provenance(value: Mapping[str, Any]) -> bool:
    if parse_string_list(value.get("source_refs")) or parse_string_list(value.get("evidence_refs")):
        return True
    source = str(value.get("source") or "").strip().lower()
    confirmed_by = str(value.get("confirmed_by") or "").strip()
    return bool(confirmed_by or source not in {"", "legacy", "unknown"})


@dataclass(frozen=True)
class MemoryGraphState:
    """Validated relationship projection used by lifecycle and recall."""

    ids: Set[str]
    conflict_participants: Set[str]
    superseded_ids: Set[str]
    invalid_ids: Set[str]
    errors: List[Dict[str, str]]


def _record_id(value: Mapping[str, Any]) -> str:
    return str(value.get("id") or value.get("slug") or value.get("name") or "").strip()


def build_memory_graph(records: Sequence[Mapping[str, Any]]) -> MemoryGraphState:
    rows = [dict(item) for item in records if isinstance(item, Mapping) and _record_id(item)]
    by_id = {_record_id(item): item for item in rows}
    ids = set(by_id)
    active_ids = {
        item_id
        for item_id, item in by_id.items()
        if normalize_memory_status(item.get("status")) == MEMORY_RECALLABLE_STATUS
    }
    conflicts: Set[str] = set()
    superseded: Set[str] = set()
    invalid: Set[str] = set()
    errors: List[Dict[str, str]] = []
    supersede_edges: Dict[str, List[str]] = {}

    for item_id, item in by_id.items():
        conflict_refs = parse_string_list(item.get("conflicts_with"))
        supersede_refs = parse_string_list(item.get("supersedes"))
        supersede_edges[item_id] = supersede_refs
        for relation, refs in (("conflicts_with", conflict_refs), ("supersedes", supersede_refs)):
            for ref in refs:
                if ref == item_id:
                    invalid.add(item_id)
                    errors.append({"id": item_id, "relation": relation, "reason": "self_reference"})
                elif ref not in ids:
                    invalid.add(item_id)
                    errors.append({"id": item_id, "relation": relation, "reason": "missing_reference", "ref": ref})
        for ref in conflict_refs:
            if item_id in active_ids and ref in active_ids and ref != item_id:
                conflicts.update({item_id, ref})
        if normalize_memory_status(item.get("status")) == MEMORY_RECALLABLE_STATUS:
            superseded.update(ref for ref in supersede_refs if ref in ids and ref != item_id)

    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node: str, path: List[str]) -> None:
        if node in visiting:
            cycle = path[path.index(node) :] if node in path else [node]
            invalid.update(cycle)
            errors.append({"id": node, "relation": "supersedes", "reason": "cycle"})
            return
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        for target in supersede_edges.get(node, []):
            if target in ids:
                visit(target, path)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for item_id in sorted(ids):
        visit(item_id, [])
    return MemoryGraphState(
        ids=ids,
        conflict_participants=conflicts,
        superseded_ids=superseded,
        invalid_ids=invalid,
        errors=errors,
    )


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
    trust_label: str = "unknown"
    confidence: float = 0.0
    confidence_method: str = "legacy"
    status: str = MEMORY_REVIEW_STATUS
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
    source_type: str = "unknown"
    activation: str = "legacy"
    deterministic_source: bool = False
    reversible: bool = False
    impact: str = "unknown"
    version_refs: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = MEMORY_RECORD_VERSION

    @classmethod
    def from_mapping(cls, value: Dict[str, Any]) -> "MemoryRecordV2":
        status = normalize_memory_status(value.get("status"))
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
            trust_label=normalize_trust_label(value.get("trust_label")),
            confidence=normalize_confidence(value.get("confidence")),
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
            source_type=str(value.get("source_type") or "unknown"),
            activation=str(value.get("activation") or "legacy"),
            deterministic_source=parse_bool(value.get("deterministic_source")),
            reversible=parse_bool(value.get("reversible")),
            impact=str(value.get("impact") or "unknown"),
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

    def recall_block_reasons(
        self,
        unresolved_conflicts: Iterable[str] = (),
        *,
        superseded_ids: Iterable[str] = (),
        invalid_ids: Iterable[str] = (),
    ) -> List[str]:
        reasons: List[str] = []
        if self.status != MEMORY_RECALLABLE_STATUS:
            reasons.append(f"status:{self.status}")
        if self.is_expired():
            reasons.append("expired")
        if not self.is_valid_at():
            reasons.append("not_yet_valid")
        if self.trust_label != "trusted":
            reasons.append("untrusted" if self.trust_label == "untrusted" else "trust:not_trusted")
        if not memory_has_provenance(self.to_dict()):
            reasons.append("missing_provenance")
        unresolved = set(unresolved_conflicts)
        if self.id in unresolved or unresolved.intersection(self.conflicts_with):
            reasons.append("unresolved_conflict")
        if self.id in set(superseded_ids):
            reasons.append("superseded")
        if self.id in set(invalid_ids):
            reasons.append("invalid_relationship")
        return reasons

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["body"] = value.pop("content")
        value["slug"] = self.name
        return value
