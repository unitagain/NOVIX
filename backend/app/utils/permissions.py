# -*- coding: utf-8 -*-
"""统一的权限、信任与副作用决策契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class PermissionLevel(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


PERMISSION_POLICY = {
    "lookup_card": "allow",
    "query_canon": "allow",
    "query_relations": "allow",
    "read_chapter": "allow",
    "search_prose": "allow",
    "read_outline": "allow",
    # 大纲写入与常规正文编辑同级（allow）：作者规划是可读可回溯的普通文本资产，
    # 写入绑定 revision、在对话流的工具轨迹中可见；ask 对 agent actor 会降级为 deny。
    "edit_outline": "allow",
    "review": "allow",
    "write_content": "allow",
    "create_chapter": "allow",
    "write_chapter": "ask",
    "edit_chapter": "ask",
    "edit_lines": "allow",
    "finish_turn": "allow",
    "add_fact": "ask",
    "confirm_facts": "ask",
    "reject_facts": "ask",
    "update_card": "ask",
    "import_tavern_card": "ask",
    "export_tavern_card": "allow",
    "save_draft": "ask",
    "write_memory": "ask",
    "confirm_memory": "ask",
    "reject_memory": "ask",
    "supersede_memory": "ask",
    "fanfiction_search": "ask",
    "external_file_read": "ask",
    "third_party_tool": "ask",
    "delete_project": "deny",
    "delete_chapter": "deny",
    "delete_fact": "deny",
    "delete_memory": "deny",
    "overwrite_canon": "deny",
    "bulk_update_canon": "deny",
}

_RANK = {PermissionLevel.ALLOW: 0, PermissionLevel.ASK: 1, PermissionLevel.DENY: 2}
_BACKGROUND_ACTORS = {"agent", "background", "worker", "scheduler", "system_background"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _level(value: Any, *, default: PermissionLevel = PermissionLevel.ASK) -> PermissionLevel:
    try:
        return PermissionLevel(str(value or "").lower())
    except ValueError:
        return default


def strictest(*levels: Any) -> PermissionLevel:
    """按 deny > ask > allow 合并权限。"""

    normalized = (_level(level) for level in levels)
    return max(normalized, key=lambda item: _RANK[item], default=PermissionLevel.ASK)


@dataclass(frozen=True)
class PermissionDecision:
    operation: str
    resource_scope: Mapping[str, Any] = field(default_factory=dict)
    payload_fingerprint: str = ""
    trust_context: Mapping[str, Any] = field(default_factory=dict)
    parent_restrictions: Mapping[str, Any] = field(default_factory=dict)
    actor: str = "local_user"
    level: PermissionLevel = PermissionLevel.ASK
    reasons: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        data = asdict(self)
        data["level"] = self.level.value
        return stable_fingerprint(data)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.value
        data["fingerprint"] = self.fingerprint
        return data


def decide_permission(
    operation: str,
    *,
    resource_scope: Mapping[str, Any] | None = None,
    payload: Any = None,
    payload_fingerprint: str = "",
    trust_context: Mapping[str, Any] | None = None,
    parent_restrictions: Mapping[str, Any] | None = None,
    actor: str = "local_user",
    base_permission: str | None = None,
    read_only: bool = False,
    external: bool = False,
) -> PermissionDecision:
    """生成不可变权限决策；所有 metadata 与执行门禁均应调用此 owner。"""

    operation = str(operation or "")
    trust = dict(trust_context or {})
    parent = dict(parent_restrictions or {})
    actor = str(actor or "local_user")
    levels: list[Any] = [base_permission or PERMISSION_POLICY.get(operation, PermissionLevel.ASK.value)]
    reasons = ["policy"]
    parent_level = parent.get("level") or parent.get("permission") or parent.get(operation)
    if parent_level:
        levels.append(parent_level)
        reasons.append("parent_permission")
    if external or parent.get("external") or parent.get("egress") == "deny":
        levels.append(PermissionLevel.DENY if parent.get("egress") == "deny" else PermissionLevel.ASK)
        reasons.append("external_or_egress")
    untrusted = bool(trust.get("consumed_untrusted") or trust.get("trust_label") in {"untrusted", "unknown"})
    if untrusted and not read_only:
        levels.append(PermissionLevel.ASK)
        reasons.append("untrusted_write")
    if parent.get("trust_label") in {"untrusted", "unknown"} and not read_only:
        levels.append(PermissionLevel.ASK)
        reasons.append("parent_trust")
    level = strictest(*levels)
    if actor.lower() in _BACKGROUND_ACTORS and level is PermissionLevel.ASK:
        level = PermissionLevel.DENY
        reasons.append("approval_unavailable")
    return PermissionDecision(
        operation=operation,
        resource_scope=dict(resource_scope or {}),
        payload_fingerprint=payload_fingerprint or stable_fingerprint(payload if payload is not None else {}),
        trust_context=trust,
        parent_restrictions=parent,
        actor=actor,
        level=level,
        reasons=tuple(reasons),
    )


def permission_for(operation: str) -> str:
    return decide_permission(operation).level.value


def is_allowed(operation: str) -> bool:
    return permission_for(operation) == PermissionLevel.ALLOW.value


def requires_confirmation(operation: str) -> bool:
    return permission_for(operation) == PermissionLevel.ASK.value


def is_denied(operation: str) -> bool:
    return permission_for(operation) == PermissionLevel.DENY.value
