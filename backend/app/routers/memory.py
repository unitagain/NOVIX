"""
Creative memory governance endpoints.
创作记忆治理端点：候选查看、确认、拒绝和状态更新。
"""

from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_creative_memory_storage, get_pending_action_storage
from app.storage.creative_memory import MEMORY_STATUSES
from app.services.memory_lifecycle_service import MemoryLifecycleService
from app.utils.permissions import permission_for

router = APIRouter(prefix="/projects/{project_id}/memory", tags=["memory"])
memory_storage = get_creative_memory_storage()
pending_storage = get_pending_action_storage()


def _lifecycle() -> MemoryLifecycleService:
    return MemoryLifecycleService(memory_storage)


class MemoryStatusRequest(BaseModel):
    """Update a memory record lifecycle status."""

    status: str = Field(..., description="active | needs_review | superseded | rejected")
    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


class MemoryApprovalRequest(BaseModel):
    """Approval payload for high-impact memory lifecycle changes."""

    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


class MemoryConflictRequest(BaseModel):
    conflicts_with: List[str] = Field(default_factory=list)
    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


class MemorySupersedeRequest(BaseModel):
    new_slug: str
    description: Optional[str] = None
    body: Optional[str] = None
    confidence: Optional[float] = None
    confirmed_by: str = "user"
    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


def _memory_target(slug: str) -> dict:
    return {"type": "memory", "slug": str(slug or "")}


def _approval_payload(status: str) -> dict:
    return {"status": str(status or "")}


@router.get("")
async def list_memory(project_id: str, status: Optional[str] = None):
    """List memory headers. Defaults to active records only."""
    statuses: Optional[List[str]]
    if status is None:
        statuses = None
    elif status == "*":
        statuses = ["*"]
    else:
        if status not in MEMORY_STATUSES:
            raise HTTPException(status_code=400, detail="invalid memory status")
        statuses = [status]
    items = await memory_storage.list_headers(project_id, statuses=statuses)
    return {"success": True, "items": items}


@router.get("/review")
async def list_memory_review_items(project_id: str):
    """List AI-derived memory candidates waiting for user review."""
    items = await memory_storage.list_review_items(project_id)
    return {"success": True, "items": items, "permission": permission_for("confirm_memory")}


@router.get("/governance")
async def memory_governance_health(project_id: str):
    """Return memory review backlog and auto-activation health metrics."""
    return {"success": True, "metrics": await memory_storage.governance_metrics(project_id)}


@router.get("/{slug}")
async def get_memory(project_id: str, slug: str):
    """Read one memory record including body and governance metadata."""
    item = await memory_storage.read_memory(project_id, slug)
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "item": item}


@router.post("/{slug}/confirm")
async def confirm_memory(project_id: str, slug: str, request: MemoryApprovalRequest = Body(default_factory=MemoryApprovalRequest)):
    """Confirm a candidate memory into the active recall set."""
    operation = "confirm_memory"
    target = _memory_target(slug)
    payload = _approval_payload("active")
    if not (request.approval_action_id and request.approval_token):
        if not await memory_storage.read_memory(project_id, slug):
            raise HTTPException(status_code=404, detail="Memory not found")
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="confirm memory",
        )
        return {"success": False, "pending_action": action, "permission": permission_for(operation)}
    await pending_storage.consume_action(
        project_id,
        action_id=request.approval_action_id,
        token=request.approval_token,
        operation=operation,
        target=target,
        payload=payload,
    )
    ok = await _lifecycle().confirm(project_id, slug)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "status": "active", "permission": permission_for("confirm_memory")}


@router.post("/{slug}/reject")
async def reject_memory(project_id: str, slug: str, request: MemoryApprovalRequest = Body(default_factory=MemoryApprovalRequest)):
    """Reject a candidate memory while keeping the file for traceability."""
    operation = "reject_memory"
    target = _memory_target(slug)
    payload = _approval_payload("rejected")
    if not (request.approval_action_id and request.approval_token):
        if not await memory_storage.read_memory(project_id, slug):
            raise HTTPException(status_code=404, detail="Memory not found")
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="reject memory",
        )
        return {"success": False, "pending_action": action, "permission": permission_for(operation)}
    await pending_storage.consume_action(
        project_id,
        action_id=request.approval_action_id,
        token=request.approval_token,
        operation=operation,
        target=target,
        payload=payload,
    )
    ok = await _lifecycle().reject(project_id, slug)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "status": "rejected", "permission": permission_for("reject_memory")}


@router.post("/{slug}/status")
async def update_memory_status(project_id: str, slug: str, request: MemoryStatusRequest):
    """Set a memory lifecycle status explicitly."""
    if request.status not in MEMORY_STATUSES:
        raise HTTPException(status_code=400, detail="invalid memory status")
    operation = {
        "active": "confirm_memory",
        "rejected": "reject_memory",
        "superseded": "supersede_memory",
    }.get(request.status, "write_memory")
    if permission_for(operation) == "ask" and not (request.approval_action_id and request.approval_token):
        if not await memory_storage.read_memory(project_id, slug):
            raise HTTPException(status_code=404, detail="Memory not found")
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=_memory_target(slug),
            payload=_approval_payload(request.status),
            reason=f"set memory status:{request.status}",
        )
        return {"success": False, "pending_action": action, "permission": permission_for(operation)}
    if permission_for(operation) == "ask":
        await pending_storage.consume_action(
            project_id,
            action_id=request.approval_action_id or "",
            token=request.approval_token or "",
            operation=operation,
            target=_memory_target(slug),
            payload=_approval_payload(request.status),
        )
    ok = await _lifecycle().transition(project_id, slug, request.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "status": request.status, "permission": permission_for(operation)}


@router.post("/{slug}/conflicts")
async def update_memory_conflicts(project_id: str, slug: str, request: MemoryConflictRequest):
    """Set an explicit conflict set; active participants are isolated from recall."""
    operation = "write_memory"
    target = _memory_target(slug)
    payload = {"conflicts_with": request.conflicts_with}
    if not (request.approval_action_id and request.approval_token):
        if not await memory_storage.read_memory(project_id, slug):
            raise HTTPException(status_code=404, detail="Memory not found")
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="set memory conflicts",
        )
        return {"success": False, "pending_action": action, "permission": permission_for(operation)}
    await pending_storage.consume_action(
        project_id,
        action_id=request.approval_action_id,
        token=request.approval_token,
        operation=operation,
        target=target,
        payload=payload,
    )
    ok = await _lifecycle().set_conflicts(project_id, slug, request.conflicts_with)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "conflicts_with": request.conflicts_with}


@router.post("/{slug}/supersede")
async def supersede_memory(project_id: str, slug: str, request: MemorySupersedeRequest):
    """Create a traceable replacement while retaining the previous record."""
    operation = "supersede_memory"
    target = _memory_target(slug)
    payload = {
        "new_slug": request.new_slug,
        "description": request.description,
        "body": request.body,
        "confidence": request.confidence,
        "confirmed_by": request.confirmed_by,
    }
    if permission_for(operation) == "ask" and not (request.approval_action_id and request.approval_token):
        if not await memory_storage.read_memory(project_id, slug):
            raise HTTPException(status_code=404, detail="Memory not found")
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="supersede memory",
        )
        return {"success": False, "pending_action": action, "permission": permission_for(operation)}
    if permission_for(operation) == "ask":
        await pending_storage.consume_action(
            project_id,
            action_id=request.approval_action_id or "",
            token=request.approval_token or "",
            operation=operation,
            target=target,
            payload=payload,
        )
    try:
        replacement = await _lifecycle().supersede(
            project_id,
            slug,
            request.new_slug,
            description=request.description,
            body=request.body,
            confidence=request.confidence,
            confirmed_by=request.confirmed_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"success": True, "status": "superseded", "replacement": replacement}
