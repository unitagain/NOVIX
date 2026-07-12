"""Pending action governance endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.dependencies import get_pending_action_storage, get_creative_memory_storage

router = APIRouter(prefix="/projects/{project_id}/actions", tags=["actions"])
pending_storage = get_pending_action_storage()
memory_storage = get_creative_memory_storage()


class RejectActionRequest(BaseModel):
    actor: Optional[str] = "local_user"


@router.get("")
async def list_pending_actions(project_id: str, status: Optional[str] = None):
    """List pending/approved/rejected actions for local audit surfaces."""
    return {"success": True, "items": await pending_storage.list_actions(project_id, status=status)}


@router.get("/{action_id}")
async def get_pending_action(project_id: str, action_id: str):
    """Read one pending action by id."""
    item = await pending_storage.get_action(project_id, action_id)
    if not item:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"success": True, "item": item}


@router.post("/{action_id}/reject")
async def reject_pending_action(project_id: str, action_id: str, request: RejectActionRequest):
    """Reject a pending action without executing the underlying write."""
    ok = await pending_storage.reject_action(project_id, action_id, actor=request.actor or "local_user")
    if not ok:
        raise HTTPException(status_code=404, detail="Pending action not found")
    return {"success": True, "status": "rejected"}


@router.get("/governance/health")
async def governance_health(project_id: str):
    """Return governance backlog metrics used by UI health panels."""
    pending = await pending_storage.list_actions(project_id, status="pending")
    return {
        "success": True,
        "metrics": {
            "pending_actions": len(pending),
            "memory": await memory_storage.governance_metrics(project_id),
        },
    }
