"""
Canon Router / 事实表路由
Canon management endpoints (facts, timeline, character states)
事实表管理端点（事实、时间线、角色状态）
"""

from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.canon import Fact, TimelineEvent, CharacterState
from app.storage import CanonStorage

router = APIRouter(prefix="/projects/{project_id}/canon", tags=["canon"])
canon_storage = CanonStorage()


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
async def get_timeline_events_by_chapter(
    project_id: str,
    chapter: str
) -> List[TimelineEvent]:
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
