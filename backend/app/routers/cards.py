"""
Cards Router / 卡片路由
Card management endpoints (characters, world, style, rules)
卡片管理端点（角色、世界观、文风、规则）
"""

from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.card import CharacterCard, WorldCard, StyleCard, RulesCard
from app.storage import CardStorage

router = APIRouter(prefix="/projects/{project_id}/cards", tags=["cards"])
card_storage = CardStorage()


# Character Cards / 角色卡
@router.get("/characters")
async def list_character_cards(project_id: str) -> List[str]:
    """List all character card names / 列出所有角色卡名称"""
    return await card_storage.list_character_cards(project_id)


@router.get("/characters/{character_name}")
async def get_character_card(project_id: str, character_name: str):
    """Get a character card / 获取角色卡"""
    card = await card_storage.get_character_card(project_id, character_name)
    if not card:
        raise HTTPException(status_code=404, detail="Character card not found")
    return card


@router.post("/characters")
async def create_character_card(project_id: str, card: CharacterCard):
    """Create a character card / 创建角色卡"""
    await card_storage.save_character_card(project_id, card)
    return {"success": True, "message": "Character card created"}


@router.put("/characters/{character_name}")
async def update_character_card(
    project_id: str,
    character_name: str,
    card: CharacterCard
):
    """Update a character card / 更新角色卡"""
    # Ensure name matches / 确保名称匹配
    card.name = character_name
    await card_storage.save_character_card(project_id, card)
    return {"success": True, "message": "Character card updated"}


@router.delete("/characters/{character_name}")
async def delete_character_card(project_id: str, character_name: str):
    """Delete a character card / 删除角色卡"""
    success = await card_storage.delete_character_card(project_id, character_name)
    if not success:
        raise HTTPException(status_code=404, detail="Character card not found")
    return {"success": True, "message": "Character card deleted"}


# World Cards / 世界观卡
@router.get("/world")
async def list_world_cards(project_id: str) -> List[str]:
    """List all world card names / 列出所有世界观卡名称"""
    return await card_storage.list_world_cards(project_id)


@router.get("/world/{card_name}")
async def get_world_card(project_id: str, card_name: str):
    """Get a world card / 获取世界观卡"""
    card = await card_storage.get_world_card(project_id, card_name)
    if not card:
        raise HTTPException(status_code=404, detail="World card not found")
    return card


@router.post("/world")
async def create_world_card(project_id: str, card: WorldCard):
    """Create a world card / 创建世界观卡"""
    await card_storage.save_world_card(project_id, card)
    return {"success": True, "message": "World card created"}


@router.put("/world/{card_name}")
async def update_world_card(project_id: str, card_name: str, card: WorldCard):
    """Update a world card / 更新世界观卡"""
    card.name = card_name
    await card_storage.save_world_card(project_id, card)
    return {"success": True, "message": "World card updated"}


# Style Card / 文风卡
@router.get("/style")
async def get_style_card(project_id: str):
    """Get style card / 获取文风卡"""
    card = await card_storage.get_style_card(project_id)
    if not card:
        raise HTTPException(status_code=404, detail="Style card not found")
    return card


@router.put("/style")
async def update_style_card(project_id: str, card: StyleCard):
    """Update style card / 更新文风卡"""
    await card_storage.save_style_card(project_id, card)
    return {"success": True, "message": "Style card updated"}


# Rules Card / 规则卡
@router.get("/rules")
async def get_rules_card(project_id: str):
    """Get rules card / 获取规则卡"""
    card = await card_storage.get_rules_card(project_id)
    if not card:
        raise HTTPException(status_code=404, detail="Rules card not found")
    return card


@router.put("/rules")
async def update_rules_card(project_id: str, card: RulesCard):
    """Update rules card / 更新规则卡"""
    await card_storage.save_rules_card(project_id, card)
    return {"success": True, "message": "Rules card updated"}
