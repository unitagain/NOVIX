"""
Drafts Router / 草稿路由
Draft and summary management endpoints
草稿和摘要管理端点
"""

from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.draft import Draft, SceneBrief, ReviewResult, ChapterSummary
from app.storage import DraftStorage

router = APIRouter(prefix="/projects/{project_id}/drafts", tags=["drafts"])
draft_storage = DraftStorage()


@router.get("")
async def list_chapters(project_id: str) -> List[str]:
    """List all chapters / 列出所有章节"""
    return await draft_storage.list_chapters(project_id)


@router.get("/{chapter}/scene-brief")
async def get_scene_brief(project_id: str, chapter: str):
    """Get scene brief / 获取场景简报"""
    brief = await draft_storage.get_scene_brief(project_id, chapter)
    if not brief:
        raise HTTPException(status_code=404, detail="Scene brief not found")
    return brief


@router.get("/{chapter}/versions")
async def list_draft_versions(project_id: str, chapter: str) -> List[str]:
    """List all draft versions for a chapter / 列出章节的所有草稿版本"""
    return await draft_storage.list_draft_versions(project_id, chapter)


@router.get("/{chapter}/{version}")
async def get_draft(project_id: str, chapter: str, version: str):
    """Get a specific draft version / 获取特定版本的草稿"""
    draft = await draft_storage.get_draft(project_id, chapter, version)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.get("/{chapter}/review")
async def get_review(project_id: str, chapter: str):
    """Get review result / 获取审稿结果"""
    review = await draft_storage.get_review(project_id, chapter)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.get("/{chapter}/final")
async def get_final_draft(project_id: str, chapter: str):
    """Get final draft / 获取成稿"""
    final = await draft_storage.get_final_draft(project_id, chapter)
    if not final:
        raise HTTPException(status_code=404, detail="Final draft not found")
    return {"content": final, "word_count": len(final)}


@router.get("/{chapter}/summary")
async def get_chapter_summary(project_id: str, chapter: str):
    """Get chapter summary / 获取章节摘要"""
    summary = await draft_storage.get_chapter_summary(project_id, chapter)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


@router.post("/{chapter}/summary")
async def save_chapter_summary(project_id: str, chapter: str, summary: ChapterSummary):
    """Save chapter summary / 保存章节摘要"""
    await draft_storage.save_chapter_summary(project_id, summary)
    return {"success": True, "message": "Summary saved"}


@router.delete("/{chapter}")
async def delete_chapter(project_id: str, chapter: str):
    """Delete all chapter drafts and related artifacts / 删除章节所有内容"""
    deleted = await draft_storage.delete_chapter(project_id, chapter)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"success": True}
