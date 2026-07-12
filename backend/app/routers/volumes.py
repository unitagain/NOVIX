# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  分卷路由 - 提供分卷的 CRUD API 端点和相关管理操作。
  Volumes router - Provides CRUD API endpoints and management operations for story volumes/parts.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.dependencies import get_draft_storage, get_volume_storage
from app.schemas.volume import Volume, VolumeCreate, VolumeSummary, VolumeStats
from app.utils.logger import get_logger
from app.services.volume_stats_service import VolumeStatsService

logger = get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/volumes", tags=["volumes"])
volume_storage = get_volume_storage()
volume_stats_service = VolumeStatsService(volume_storage=volume_storage, draft_storage=get_draft_storage())


@router.get("", response_model=List[Volume])
async def list_volumes(project_id: str):
    """列出项目的所有分卷"""
    return await volume_storage.list_volumes(project_id)


@router.post("", response_model=Volume)
async def create_volume(project_id: str, volume_create: VolumeCreate):
    """创建新分卷"""
    volume = await volume_storage.create_volume(project_id, volume_create)
    logger.info("Created volume %s for project %s", volume.id, project_id)
    return volume


@router.get("/{volume_id}", response_model=Volume)
async def get_volume(project_id: str, volume_id: str):
    """获取分卷信息"""
    volume = await volume_storage.get_volume(project_id, volume_id)
    if not volume:
        raise HTTPException(status_code=404, detail=f"Volume {volume_id} not found")
    return volume


@router.put("/{volume_id}", response_model=Volume)
async def update_volume(project_id: str, volume_id: str, volume_update: VolumeCreate):
    """更新分卷信息"""
    volume = await volume_storage.get_volume(project_id, volume_id)
    if not volume:
        raise HTTPException(status_code=404, detail=f"Volume {volume_id} not found")
    volume.title = volume_update.title
    volume.summary = volume_update.summary
    if volume_update.order:
        volume.order = volume_update.order
    updated_volume = await volume_storage.update_volume(project_id, volume)
    logger.info("Updated volume %s for project %s", volume_id, project_id)
    return updated_volume


@router.delete("/{volume_id}")
async def delete_volume(project_id: str, volume_id: str):
    """删除分卷"""
    if volume_id == "V1":
        raise HTTPException(status_code=400, detail="Default volume V1 cannot be deleted")
    success = await volume_storage.delete_volume(project_id, volume_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Volume {volume_id} not found")
    logger.info("Deleted volume %s from project %s", volume_id, project_id)
    return {"success": True, "message": f"Volume {volume_id} deleted"}


@router.get("/{volume_id}/summary", response_model=VolumeSummary)
async def get_volume_summary(project_id: str, volume_id: str):
    """获取分卷摘要"""
    summary = await volume_storage.get_volume_summary(project_id, volume_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"Summary for volume {volume_id} not found")
    return summary


@router.put("/{volume_id}/summary", response_model=VolumeSummary)
async def save_volume_summary(project_id: str, volume_id: str, summary: VolumeSummary):
    """保存分卷摘要"""
    volume = await volume_storage.get_volume(project_id, volume_id)
    if not volume:
        raise HTTPException(status_code=404, detail=f"Volume {volume_id} not found")
    summary.volume_id = volume_id
    await volume_storage.save_volume_summary(project_id, summary)
    logger.info("Saved summary for volume %s", volume_id)
    return summary


@router.get("/{volume_id}/stats", response_model=VolumeStats)
async def get_volume_stats(project_id: str, volume_id: str):
    """获取分卷统计信息"""
    stats = await volume_stats_service.get(project_id, volume_id)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Volume {volume_id} not found")
    return stats


class RefreshSummaryRequest(BaseModel):
    """刷新卷摘要请求"""

    volume_ids: List[str]


@router.post("/refresh-summaries")
async def refresh_volume_summaries(project_id: str, request: RefreshSummaryRequest):
    """
    AI 重新生成选定分卷的摘要 / AI-regenerate volume summaries for selected volumes.

    Uses the archivist agent to aggregate chapter summaries into volume-level summaries.
    This is an LLM operation that may take 10-30 seconds per volume.
    """
    from app.routers.session import get_orchestrator

    volume_ids = [v.strip() for v in (request.volume_ids or []) if v.strip()]
    if not volume_ids:
        raise HTTPException(status_code=400, detail="No volume IDs provided")

    orchestrator = get_orchestrator(project_id)
    return await orchestrator.application.volumes.refresh(project_id, volume_ids)
