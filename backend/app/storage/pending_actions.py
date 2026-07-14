# -*- coding: utf-8 -*-
"""File-backed pending action and approval ledger storage."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.storage.base import BaseStorage
from app.utils.permissions import decide_permission


ACTION_STATUSES = ("pending", "approved", "rejected", "expired")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        text = str(value or "")
        if not text:
            return None
        return datetime.fromisoformat(text)
    except Exception:
        return None


def target_hash(operation: str, target: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Hash the action contract so approvals cannot be replayed for another target."""

    return decide_permission(operation, resource_scope=target, payload=payload).fingerprint


class PendingActionStorage(BaseStorage):
    """Persist pending actions and approval ledger per project."""

    def _actions_path(self, project_id: str):
        return self.get_project_path(project_id) / "governance" / "pending_actions.jsonl"

    async def create_action(
        self,
        project_id: str,
        *,
        operation: str,
        target: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
        reason: str = "",
        expires_in_seconds: int = 86400,
        trust_context: Optional[Dict[str, Any]] = None,
        parent_restrictions: Optional[Dict[str, Any]] = None,
        actor: str = "local_user",
    ) -> Dict[str, Any]:
        now = _utc_now()
        payload = dict(payload or {})
        target = dict(target or {})
        decision = decide_permission(
            operation,
            resource_scope=target,
            payload=payload,
            trust_context=trust_context,
            parent_restrictions=parent_restrictions,
            actor=actor,
        )
        if decision.level.value == "deny":
            raise PermissionError("permission_denied")
        action = {
            "id": f"act_{int(now.timestamp() * 1000)}_{secrets.token_hex(4)}",
            "operation": str(operation or ""),
            "target": target,
            "payload": payload,
            "reason": str(reason or ""),
            "status": "pending",
            "token": secrets.token_urlsafe(24),
            "target_hash": decision.fingerprint,
            "decision": decision.to_dict(),
            "decision_fingerprint": decision.fingerprint,
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=max(60, int(expires_in_seconds or 86400)))),
            "ledger": [],
        }
        await self.append_jsonl(self._actions_path(project_id), action)
        return dict(action)

    async def list_actions(self, project_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        items = await self.read_jsonl(self._actions_path(project_id))
        normalized: List[Dict[str, Any]] = []
        now = _utc_now()
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("status") == "pending":
                expires_at = _parse_dt(item.get("expires_at"))
                if expires_at and expires_at < now:
                    item = dict(item)
                    item["status"] = "expired"
                    item["updated_at"] = _iso(now)
                    changed = True
            if status and item.get("status") != status:
                normalized.append(item)
                continue
            normalized.append(item)
        if changed:
            await self.write_jsonl(self._actions_path(project_id), normalized)
        return [dict(item) for item in normalized if not status or item.get("status") == status]

    async def get_action(self, project_id: str, action_id: str) -> Optional[Dict[str, Any]]:
        for item in await self.list_actions(project_id):
            if str(item.get("id")) == str(action_id):
                return dict(item)
        return None

    async def consume_action(
        self,
        project_id: str,
        *,
        action_id: str,
        token: str,
        operation: str,
        target: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
        actor: str = "local_user",
        trust_context: Optional[Dict[str, Any]] = None,
        parent_restrictions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Validate and approve a pending action. Returns the approved action."""

        items = await self.read_jsonl(self._actions_path(project_id))
        decision = decide_permission(
            operation,
            resource_scope=dict(target or {}),
            payload=dict(payload or {}),
            trust_context=trust_context,
            parent_restrictions=parent_restrictions,
            actor=actor,
        )
        expected_hash = decision.fingerprint
        now = _utc_now()
        approved: Optional[Dict[str, Any]] = None
        next_items: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id")) != str(action_id):
                next_items.append(item)
                continue
            if item.get("status") != "pending":
                raise ValueError(f"action_not_pending:{item.get('status')}")
            expires_at = _parse_dt(item.get("expires_at"))
            if expires_at and expires_at < now:
                item = dict(item)
                item["status"] = "expired"
                item["updated_at"] = _iso(now)
                next_items.append(item)
                raise ValueError("action_expired")
            if str(item.get("token") or "") != str(token or ""):
                raise ValueError("invalid_approval_token")
            if str(item.get("operation") or "") != str(operation or ""):
                raise ValueError("operation_mismatch")
            if str(item.get("target_hash") or "") != expected_hash:
                raise ValueError("target_hash_mismatch")
            if str(item.get("decision_fingerprint") or item.get("target_hash") or "") != decision.fingerprint:
                raise ValueError("decision_fingerprint_mismatch")
            item = dict(item)
            item["status"] = "approved"
            item["updated_at"] = _iso(now)
            ledger = list(item.get("ledger") or [])
            ledger.append({"event": "approved", "actor": str(actor or "local_user"), "at": _iso(now)})
            item["ledger"] = ledger
            approved = dict(item)
            next_items.append(item)
        if approved is None:
            raise ValueError("action_not_found")
        await self.write_jsonl(self._actions_path(project_id), next_items)
        return approved

    async def reject_action(self, project_id: str, action_id: str, *, actor: str = "local_user") -> bool:
        items = await self.read_jsonl(self._actions_path(project_id))
        now = _utc_now()
        changed = False
        out: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict) and str(item.get("id")) == str(action_id) and item.get("status") == "pending":
                item = dict(item)
                item["status"] = "rejected"
                item["updated_at"] = _iso(now)
                ledger = list(item.get("ledger") or [])
                ledger.append({"event": "rejected", "actor": str(actor or "local_user"), "at": _iso(now)})
                item["ledger"] = ledger
                changed = True
            out.append(item)
        if changed:
            await self.write_jsonl(self._actions_path(project_id), out)
        return changed
