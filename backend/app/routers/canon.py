"""
Canon Router / 事实表路由
Canon management endpoints (facts, timeline, character states)
事实表管理端点（事实、时间线、角色状态）
"""

from fastapi import APIRouter, Body, HTTPException
from typing import List, Optional
import re
from app.schemas.canon import Fact, TimelineEvent, CharacterState
from pydantic import BaseModel
from app.dependencies import get_canon_storage, get_pending_action_storage
from app.utils.permissions import permission_for

router = APIRouter(prefix="/projects/{project_id}/canon", tags=["canon"])
canon_storage = get_canon_storage()
pending_storage = get_pending_action_storage()


class FactUpdate(BaseModel):
    """Payload for partial fact updates."""

    title: Optional[str] = None
    content: Optional[str] = None
    statement: Optional[str] = None
    source: Optional[str] = None
    introduced_in: Optional[str] = None
    confidence: Optional[float] = None
    source_type: Optional[str] = None
    trust_label: Optional[str] = None


class ManualFactCreate(BaseModel):
    """Manual fact creation payload (server assigns ID)."""

    statement: Optional[str] = None
    content: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    introduced_in: Optional[str] = None
    confidence: Optional[float] = None
    source_type: Optional[str] = None
    trust_label: Optional[str] = None


class ConfirmFactsRequest(BaseModel):
    """Phase 14：作者确认 AI 抽取的 needs_review 事实为 confirmed。"""

    fact_ids: List[str]
    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


class RejectFactsRequest(BaseModel):
    """Reject AI-derived facts while preserving source trace."""

    fact_ids: List[str]
    approval_action_id: Optional[str] = None
    approval_token: Optional[str] = None


def _fact_target(fact_ids: List[str]) -> dict:
    return {"type": "canon_facts", "ids": sorted(str(item) for item in (fact_ids or []))}


def _approval_payload(status: str) -> dict:
    return {"status": status}


# Facts / 事实
@router.get("/facts")
async def get_all_facts(project_id: str) -> List[Fact]:
    """Get all facts / 获取所有事实"""
    return await canon_storage.get_all_facts(project_id)


@router.post("/facts")
async def add_fact(project_id: str, fact: Fact):
    """Add a new fact / 添加新事实"""
    await canon_storage.add_fact(project_id, fact)
    return {"success": True, "message": "Fact added"}


@router.post("/facts/confirm")
async def confirm_facts(project_id: str, request: ConfirmFactsRequest = Body(default_factory=ConfirmFactsRequest)):
    """Phase 14：把 needs_review 事实确认为 confirmed（作者审核后进入强约束主 canon）。"""
    operation = "confirm_facts"
    target = _fact_target(request.fact_ids)
    payload = _approval_payload("confirmed")
    if not (request.approval_action_id and request.approval_token):
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="confirm canon facts",
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
    confirmed = await canon_storage.confirm_facts(project_id, request.fact_ids)
    return {"success": True, "confirmed": confirmed, "permission": permission_for("confirm_facts")}


@router.get("/facts/review")
async def list_fact_review_items(project_id: str):
    """List AI-derived canon facts waiting for user review."""
    items = await canon_storage.list_facts_by_status(project_id, ["needs_review", "inferred"])
    return {"success": True, "items": items, "permission": permission_for("confirm_facts")}


@router.post("/facts/reject")
async def reject_facts(project_id: str, request: RejectFactsRequest = Body(default_factory=RejectFactsRequest)):
    """Mark fact candidates as rejected instead of deleting their source chain."""
    operation = "reject_facts"
    target = _fact_target(request.fact_ids)
    payload = _approval_payload("rejected")
    if not (request.approval_action_id and request.approval_token):
        action = await pending_storage.create_action(
            project_id,
            operation=operation,
            target=target,
            payload=payload,
            reason="reject canon facts",
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
    rejected = await canon_storage.reject_facts(project_id, request.fact_ids)
    return {"success": True, "rejected": rejected, "permission": permission_for("reject_facts")}


@router.get("/conflicts")
async def list_conflict_reports(project_id: str):
    """List persisted conflict reports generated by finalize/review flows."""
    project_path = canon_storage.get_project_path(project_id)
    drafts_dir = project_path / "drafts"
    reports = []
    if drafts_dir.exists():
        for path in sorted(drafts_dir.glob("*/conflicts.yaml")):
            try:
                report = await canon_storage.read_yaml(path)
            except (OSError, UnicodeError, TypeError, ValueError):
                continue
            chapter = path.parent.name
            conflicts = report.get("conflicts") if isinstance(report, dict) else None
            if conflicts:
                reports.append({"chapter": chapter, "path": path.relative_to(project_path).as_posix(), "report": report})
    return {"success": True, "items": reports}


@router.post("/facts/manual")
async def add_manual_fact(project_id: str, payload: ManualFactCreate):
    """Add a new manual fact (auto ID) / 手动新增事实（自动分配ID）"""
    statement = (payload.statement or payload.content or "").strip()
    if not statement:
        raise HTTPException(status_code=400, detail="Fact statement is required")

    source = (payload.source or payload.introduced_in or "").strip()
    introduced_in = (payload.introduced_in or source).strip()
    if not introduced_in:
        raise HTTPException(status_code=400, detail="introduced_in is required")
    if not source:
        source = introduced_in

    confidence = payload.confidence if payload.confidence is not None else 1.0
    if not (0.0 <= float(confidence) <= 1.0):
        raise HTTPException(status_code=400, detail="confidence must be within [0, 1]")

    all_facts = await canon_storage.get_all_facts_raw(project_id)
    max_num = 0
    for item in all_facts:
        fid = str((item or {}).get("id", ""))
        match = re.match(r"^F(\d+)$", fid, re.IGNORECASE)
        if match:
            max_num = max(max_num, int(match.group(1)))
    fact_id = f"F{max_num + 1:04d}"

    fact_data = {
        "id": fact_id,
        "statement": statement,
        "source": source,
        "introduced_in": introduced_in,
        "confidence": float(confidence),
    }
    if payload.source_type is not None:
        fact_data["source_type"] = payload.source_type
    if payload.trust_label is not None:
        fact_data["trust_label"] = payload.trust_label
    if payload.title is not None:
        fact_data["title"] = payload.title
    if payload.content is not None:
        fact_data["content"] = payload.content

    await canon_storage.add_fact(project_id, Fact(**fact_data))

    return {"success": True, "message": "Fact added", "id": fact_id}


@router.get("/facts/by-id/{fact_id}")
async def get_fact_by_id(project_id: str, fact_id: str):
    """Get fact by ID."""
    fact = await canon_storage.get_fact(project_id, fact_id)
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")
    return fact


@router.put("/facts/by-id/{fact_id}")
async def update_fact(project_id: str, fact_id: str, payload: FactUpdate):
    """Update a fact by ID."""
    existing = await canon_storage.get_fact(project_id, fact_id)

    statement = payload.statement or payload.content or (existing.statement if existing else "")
    source = payload.source or (existing.source if existing else "")
    introduced_in = payload.introduced_in or (existing.introduced_in if existing else source)
    confidence = payload.confidence if payload.confidence is not None else (existing.confidence if existing else 1.0)
    content = payload.content if payload.content is not None else payload.statement

    fact_data = {
        "id": fact_id,
        "statement": statement,
        "source": source,
        "introduced_in": introduced_in,
        "confidence": confidence,
    }
    if payload.source_type is not None:
        fact_data["source_type"] = payload.source_type
    if payload.trust_label is not None:
        fact_data["trust_label"] = payload.trust_label
    if payload.title is not None:
        fact_data["title"] = payload.title
    if content is not None:
        fact_data["content"] = content
    updated = await canon_storage.update_fact(project_id, fact_data)
    if updated:
        return {"success": True, "message": "Fact updated"}

    # Create new fact when ID does not exist (e.g., summary-derived facts)
    all_facts = await canon_storage.get_all_facts_raw(project_id)
    max_num = 0
    for item in all_facts:
        fid = str(item.get("id", ""))
        match = re.match(r"^F(\d+)$", fid, re.IGNORECASE)
        if match:
            max_num = max(max_num, int(match.group(1)))
    new_id = f"F{max_num + 1:04d}"
    if fact_id.upper().startswith("S"):
        fact_data["summary_ref"] = fact_id
    fact_data["id"] = new_id

    await canon_storage.add_fact(project_id, Fact(**fact_data))
    return {"success": True, "message": "Fact created", "id": new_id}


@router.delete("/facts/by-id/{fact_id}")
async def delete_fact(project_id: str, fact_id: str):
    """Delete a fact by ID."""
    success = await canon_storage.delete_fact(project_id, fact_id)
    if not success:
        raise HTTPException(status_code=404, detail="Fact not found")
    return {"success": True, "message": "Fact deleted"}


@router.get("/facts/{chapter}")
async def get_facts_by_chapter(project_id: str, chapter: str) -> List[Fact]:
    """Get facts from a specific chapter / 获取特定章节的事实"""
    return await canon_storage.get_facts_by_chapter(project_id, chapter)


# Timeline / 时间线
@router.get("/timeline")
async def get_all_timeline_events(project_id: str) -> List[TimelineEvent]:
    """Get all timeline events / 获取所有时间线事件"""
    return await canon_storage.get_all_timeline_events(project_id)


@router.post("/timeline")
async def add_timeline_event(project_id: str, event: TimelineEvent):
    """Add a timeline event / 添加时间线事件"""
    await canon_storage.add_timeline_event(project_id, event)
    return {"success": True, "message": "Timeline event added"}


@router.get("/timeline/{chapter}")
async def get_timeline_events_by_chapter(project_id: str, chapter: str) -> List[TimelineEvent]:
    """Get timeline events from a specific chapter / 获取特定章节的时间线事件"""
    return await canon_storage.get_timeline_events_by_chapter(project_id, chapter)


# Character States / 角色状态
@router.get("/character-state")
async def get_all_character_states(project_id: str) -> List[CharacterState]:
    """Get all character states / 获取所有角色状态"""
    return await canon_storage.get_all_character_states(project_id)


@router.get("/character-state/{character_name}")
async def get_character_state(project_id: str, character_name: str):
    """Get state of a specific character / 获取特定角色的状态"""
    state = await canon_storage.get_character_state(project_id, character_name)
    if not state:
        raise HTTPException(status_code=404, detail="Character state not found")
    return state


@router.post("/character-state")
async def update_character_state(project_id: str, state: CharacterState):
    """Update character state / 更新角色状态"""
    await canon_storage.update_character_state(project_id, state)
    return {"success": True, "message": "Character state updated"}
