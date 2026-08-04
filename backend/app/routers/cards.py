# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  卡片路由 - 角色和世界观卡片管理
  Cards Router - Character and world card management endpoints
  Provides CRUD operations for character cards, world cards, and style cards.
"""

from typing import Any, Dict, List, Optional

import asyncio

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.schemas.card import CharacterCard, WorldCard, StyleCard, RelationsDocument
from app.llm_gateway import get_gateway
from app.agents import ArchivistAgent
from app.dependencies import (
    get_card_storage,
    get_canon_storage,
    get_character_relation_storage,
    get_draft_storage,
)
from app.services.tavern_cards import (
    MAX_PAYLOAD_BYTES,
    TavernImportError,
    TavernImportPlan,
    build_character_export,
    build_lorebook_export,
    parse_tavern_asset,
)
from app.utils.language import normalize_language
from app.utils.path_safety import sanitize_id
from app.utils.trust import permission_with_trust

router = APIRouter(prefix="/projects/{project_id}/cards", tags=["cards"])
card_storage = get_card_storage()
relation_storage = get_character_relation_storage()


class StyleExtractRequest(BaseModel):
    """
    风格提取请求 / Request body for style extraction.

    Attributes:
        content (str): 样本文本用于风格提取 / Sample text for style extraction.
    """

    language: Optional[str] = Field(
        None,
        description="Writing language override: zh/en or locale-like values",
    )
    content: str = Field(..., description="Sample text for style extraction")


async def _resolve_project_language(project_id: str, request_language: Optional[str]) -> str:
    explicit = normalize_language(request_language, default="")
    if explicit in {"zh", "en"}:
        return explicit

    try:
        from pathlib import Path

        project_yaml = Path(card_storage.data_dir) / project_id / "project.yaml"
        if not project_yaml.exists():
            return "zh"
        data = await card_storage.read_yaml(project_yaml)
        return normalize_language((data or {}).get("language"), default="zh")
    except Exception:
        return "zh"


@router.get("/characters")
async def list_character_cards(project_id: str) -> List[str]:
    """列出所有角色卡片名称 / List all character card names.

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        角色卡片名称列表 / List of character card names.
    """
    return await card_storage.list_character_cards(project_id)


@router.get("/characters/index")
async def list_character_cards_index(project_id: str) -> List[CharacterCard]:
    """列出所有角色卡片及其元数据（单个请求） / List all character cards with metadata (single request).

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        角色卡片列表 / List of CharacterCard objects.
    """
    names = await card_storage.list_character_cards(project_id)
    if not names:
        return []

    async def _safe_get(name: str) -> Optional[CharacterCard]:
        try:
            return await card_storage.get_character_card(project_id, name)
        except (FileNotFoundError, ValueError, KeyError):
            return None

    results = await asyncio.gather(*[_safe_get(name) for name in names])
    return [card for card in results if card]


@router.get("/characters/{character_name}")
async def get_character_card(project_id: str, character_name: str):
    """获取特定角色卡片 / Get a character card.

    Args:
        project_id: 项目ID / Project identifier.
        character_name: 角色名称 / Character name.

    Returns:
        角色卡片对象 / CharacterCard object.

    Raises:
        HTTPException: 404 if card not found, 400 if name invalid.
    """
    try:
        sanitize_id(character_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid character name")
    card = await card_storage.get_character_card(project_id, character_name)
    if not card:
        raise HTTPException(status_code=404, detail="Character card not found")
    return card


@router.post("/characters")
async def create_character_card(project_id: str, card: CharacterCard):
    """创建角色卡片 / Create a character card.

    Args:
        project_id: 项目ID / Project identifier.
        card: 角色卡片数据 / CharacterCard object.

    Returns:
        成功消息 / Success response.
    """
    await card_storage.save_character_card(project_id, card)
    return {"success": True, "message": "Character card created"}


@router.put("/characters/{character_name}")
async def update_character_card(project_id: str, character_name: str, card: CharacterCard):
    """更新角色卡片 / Update a character card.

    Args:
        project_id: 项目ID / Project identifier.
        character_name: 角色名称 / Character name.
        card: 更新后的卡片数据 / Updated CharacterCard object.

    Returns:
        成功消息 / Success response.
    """
    card.name = character_name
    await card_storage.save_character_card(project_id, card)
    return {"success": True, "message": "Character card updated"}


@router.delete("/characters/{character_name}")
async def delete_character_card(project_id: str, character_name: str):
    """删除角色卡片 / Delete a character card.

    Args:
        project_id: 项目ID / Project identifier.
        character_name: 角色名称 / Character name.

    Returns:
        成功消息 / Success response.

    Raises:
        HTTPException: 404 if card not found, 400 if name invalid.
    """
    try:
        sanitize_id(character_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid character name")
    success = await card_storage.delete_character_card(project_id, character_name)
    if not success:
        raise HTTPException(status_code=404, detail="Character card not found")
    return {"success": True, "message": "Character card deleted"}


@router.get("/relations")
async def get_character_relations(project_id: str) -> RelationsDocument:
    """读取角色关系图谱 / Get the authored character relation graph.

    关系边是**作者设定**（与角色卡同层），不是 Canon 中档案员从正文抽取的关系事实。
    文件不存在时返回空文档，前端据此绘制空画布。

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        关系文档（设定边 + 画布布局） / Relation document with edges and layout.
    """
    document = await relation_storage.load_document(project_id)
    return RelationsDocument.model_validate(document)


@router.put("/relations")
async def update_character_relations(project_id: str, document: RelationsDocument) -> RelationsDocument:
    """整文档覆盖写角色关系图谱 / Replace the authored character relation graph.

    画布天然持有全图状态，因此按整文档写入；校验规则由
    ``storage/character_relations.py`` 单一 owner 执行，任一条边不合法则整体拒绝
    （不落盘、不半写）。权限上等同卡片 CRUD——这是用户直连 UI 的编辑动作，
    ``PermissionDecision`` 治理的是 Agent 发起的副作用。

    Args:
        project_id: 项目ID / Project identifier.
        document: 完整关系文档 / Full relation document.

    Returns:
        实际落盘的关系文档（含服务端生成的边 id） / Persisted relation document.

    Raises:
        HTTPException: 400 if any edge violates the relation contract.
    """
    existing_characters = await card_storage.list_character_cards(project_id)
    payload = document.model_dump(by_alias=True, exclude_none=True)
    errors = relation_storage.validate_document(payload, existing_characters)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "relation_document_invalid", "errors": errors},
        )
    saved = await relation_storage.save_document(
        project_id, payload, existing_characters=existing_characters
    )
    return RelationsDocument.model_validate(saved)


@router.get("/world")
async def list_world_cards(project_id: str) -> List[str]:
    """列出所有世界观卡片名称 / List all world card names.

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        世界观卡片名称列表 / List of world card names.
    """
    return await card_storage.list_world_cards(project_id)


@router.get("/world/index")
async def list_world_cards_index(project_id: str) -> List[WorldCard]:
    """列出所有世界观卡片及其元数据（单个请求） / List all world cards with metadata (single request).

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        世界观卡片列表 / List of WorldCard objects.
    """
    names = await card_storage.list_world_cards(project_id)
    if not names:
        return []

    async def _safe_get(name: str) -> Optional[WorldCard]:
        try:
            return await card_storage.get_world_card(project_id, name)
        except (FileNotFoundError, ValueError, KeyError):
            return None

    results = await asyncio.gather(*[_safe_get(name) for name in names])
    return [card for card in results if card]


@router.get("/world/{card_name}")
async def get_world_card(project_id: str, card_name: str):
    """Get a world card."""
    try:
        sanitize_id(card_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid card name")
    card = await card_storage.get_world_card(project_id, card_name)
    if not card:
        raise HTTPException(status_code=404, detail="World card not found")
    return card


@router.post("/world")
async def create_world_card(project_id: str, card: WorldCard):
    """Create a world card."""
    await card_storage.save_world_card(project_id, card)
    return {"success": True, "message": "World card created"}


@router.put("/world/{card_name}")
async def update_world_card(project_id: str, card_name: str, card: WorldCard):
    """Update a world card."""
    card.name = card_name
    await card_storage.save_world_card(project_id, card)
    return {"success": True, "message": "World card updated"}


@router.delete("/world/{card_name}")
async def delete_world_card(project_id: str, card_name: str):
    """Delete a world card."""
    try:
        sanitize_id(card_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid card name")
    success = await card_storage.delete_world_card(project_id, card_name)
    if not success:
        raise HTTPException(status_code=404, detail="World card not found")
    return {"success": True, "message": "World card deleted"}


@router.get("/style")
async def get_style_card(project_id: str):
    """Get style card."""
    card = await card_storage.get_style_card(project_id)
    if not card:
        raise HTTPException(status_code=404, detail="Style card not found")
    return card


@router.put("/style")
async def update_style_card(project_id: str, card: StyleCard):
    """Update style card."""
    await card_storage.save_style_card(project_id, card)
    return {"success": True, "message": "Style card updated"}


@router.post("/style/extract")
async def extract_style_card(project_id: str, request: StyleExtractRequest):
    """Extract style guidance from sample text."""
    content = (request.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    language = await _resolve_project_language(project_id, request.language)
    gateway = get_gateway()
    archivist = ArchivistAgent(
        gateway,
        card_storage,
        get_canon_storage(),
        get_draft_storage(),
        language=language,
    )
    style_text = await archivist.extract_style_profile(content)
    return {"style": style_text}


class TavernImportResponse(BaseModel):
    """
    Tavern 导入结果 / Tavern import result.

    ``committed=False`` 时为 dry-run 预案，未写入任何文件。
    """

    committed: bool = Field(..., description="Whether cards were written to disk")
    permission: str = Field(..., description="Permission decision for this import")
    plan: TavernImportPlan = Field(..., description="Parsed import plan")
    created_characters: List[str] = Field(default_factory=list, description="Character cards written")
    created_world_cards: List[str] = Field(default_factory=list, description="World cards written")
    skipped_existing: List[str] = Field(default_factory=list, description="Cards skipped because they already exist")


@router.post("/import/tavern")
async def import_tavern_cards(
    project_id: str,
    file: UploadFile = File(..., description="SillyTavern character card (.json/.png) or lorebook (.json)"),
    commit: bool = Query(False, description="Write cards to disk; default is a dry-run preview"),
    overwrite: bool = Query(False, description="Overwrite cards that already exist"),
) -> TavernImportResponse:
    """导入 SillyTavern 角色卡 / 世界书 / Import a SillyTavern character card or lorebook.

    两步式：默认 ``commit=false`` 只返回预案（含被丢弃字段清单）供确认，
    确认后以 ``commit=true`` 重放同一文件才落盘——这是 ``import_tavern_card``
    权限为 ``ask`` 在 API 边界上的实现。导入内容按不可信外部数据处理：
    prompt 装配指令与越狱字段在解析阶段即被剥离，永不进入上下文装配路径。

    Args:
        project_id: 项目ID / Project identifier.
        file: 上传的卡片或世界书文件 / Uploaded card or lorebook file.
        commit: 是否落盘 / Whether to persist.
        overwrite: 是否覆盖同名卡片 / Whether to overwrite same-named cards.

    Returns:
        导入结果 / Import outcome including the plan and dropped-field report.

    Raises:
        HTTPException: 400 if the payload is oversized, malformed or unsupported.
    """
    raw = await file.read(MAX_PAYLOAD_BYTES + 1)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=400, detail="tavern_import_payload_too_large")

    try:
        plan = parse_tavern_asset(raw, filename=file.filename or "")
    except TavernImportError as exc:
        # detail 只回传稳定的机器可读错误码，不外泄原始异常文本。
        detail = exc.args[0] if exc.args else "tavern_import_unrecognized_format"
        raise HTTPException(status_code=400, detail=detail) from exc

    permission = permission_with_trust("import_tavern_card", consumed_untrusted=True)
    if not commit:
        return TavernImportResponse(committed=False, permission=permission, plan=plan)

    existing_characters = set(await card_storage.list_character_cards(project_id))
    existing_world = set(await card_storage.list_world_cards(project_id))
    created_characters: List[str] = []
    created_world_cards: List[str] = []
    skipped: List[str] = []

    for card in plan.characters:
        if card.name in existing_characters and not overwrite:
            skipped.append(card.name)
            continue
        await card_storage.save_character_card(project_id, card)
        created_characters.append(card.name)

    for world_card in plan.world_cards:
        if world_card.name in existing_world and not overwrite:
            skipped.append(world_card.name)
            continue
        await card_storage.save_world_card(project_id, world_card)
        created_world_cards.append(world_card.name)

    return TavernImportResponse(
        committed=True,
        permission=permission,
        plan=plan,
        created_characters=created_characters,
        created_world_cards=created_world_cards,
        skipped_existing=skipped,
    )


@router.get("/export/tavern/characters/{character_name}")
async def export_tavern_character(
    project_id: str,
    character_name: str,
    include_world: bool = Query(False, description="Embed world cards as the character's lorebook"),
) -> Dict[str, Any]:
    """导出角色卡为 Character Card V3 / Export a character card as Character Card V3.

    口吻项写入 ``mes_example``；``system_prompt`` 与 ``post_history_instructions``
    始终为空——WenShape 不产出这类指令，也不把它们写进对外流通的文件。
    """
    try:
        sanitize_id(character_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid character name")
    card = await card_storage.get_character_card(project_id, character_name)
    if not card:
        raise HTTPException(status_code=404, detail="Character card not found")

    world_cards: List[WorldCard] = []
    if include_world:
        names = await card_storage.list_world_cards(project_id)
        loaded = await asyncio.gather(*[card_storage.get_world_card(project_id, name) for name in names])
        world_cards = [item for item in loaded if item]
    return build_character_export(card, world_cards=world_cards)


@router.get("/export/tavern/lorebook")
async def export_tavern_lorebook(project_id: str) -> Dict[str, Any]:
    """导出全部世界观卡为独立世界书 / Export all world cards as a standalone lorebook."""
    names = await card_storage.list_world_cards(project_id)
    loaded = await asyncio.gather(*[card_storage.get_world_card(project_id, name) for name in names])
    cards = [item for item in loaded if item]
    return build_lorebook_export(cards, name=f"{project_id} Lorebook")
