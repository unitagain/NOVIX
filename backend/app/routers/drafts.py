"""
Drafts Router / 草稿路由
Draft and summary management endpoints / 草稿与摘要管理接口
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas.draft import ChapterSummary
from app.dependencies import (
    get_draft_storage,
    get_canon_storage,
    get_memory_pack_storage,
    get_binding_storage,
)
from app.utils.chapter_id import normalize_chapter_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/drafts", tags=["drafts"])
draft_storage = get_draft_storage()
canon_storage = get_canon_storage()
memory_pack_storage = get_memory_pack_storage()
binding_storage = get_binding_storage()


@router.get("")
async def list_chapters(project_id: str) -> List[str]:
    """List all chapters / 列出所有章节"""
    return await draft_storage.list_chapters(project_id)


@router.get("/summaries", response_model=List[ChapterSummary])
async def list_chapter_summaries(project_id: str, volume_id: Optional[str] = None):
    """List chapter summaries / 列出章节摘要"""
    return await draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)


@router.get("/{chapter}/versions")
async def list_draft_versions(project_id: str, chapter: str) -> List[str]:
    """List all draft versions for a chapter / 列出章节草稿版本"""
    return await draft_storage.list_draft_versions(project_id, chapter)


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
    """
    Delete chapter artifacts with cascade cleanup.
    删除章节相关内容（级联清理关联数据）
    """
    deleted = await draft_storage.delete_chapter(project_id, chapter)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Cascade delete related data / 级联删除关联数据
    cascade_results = {}
    try:
        facts_deleted = await canon_storage.delete_facts_by_chapter(project_id, chapter)
        cascade_results["facts_deleted"] = facts_deleted
    except Exception as exc:
        logger.warning("Cascade delete facts failed for %s:%s: %s", project_id, chapter, exc)

    try:
        bindings_deleted = await binding_storage.delete_bindings(project_id, chapter)
        cascade_results["bindings_deleted"] = bindings_deleted
    except Exception as exc:
        logger.warning("Cascade delete bindings failed for %s:%s: %s", project_id, chapter, exc)

    try:
        pack_deleted = await memory_pack_storage.delete_pack(project_id, chapter)
        cascade_results["memory_pack_deleted"] = pack_deleted
    except Exception as exc:
        logger.warning("Cascade delete memory pack failed for %s:%s: %s", project_id, chapter, exc)

    return {"success": True, "cascade": cascade_results}


class UpdateContentRequest(BaseModel):
    content: str
    title: Optional[str] = None


class ReorderChaptersRequest(BaseModel):
    volume_id: str
    chapter_order: List[str]


@router.post("/reorder")
async def reorder_chapters(project_id: str, body: ReorderChaptersRequest):
    """Reorder chapters within a volume / 调整分卷内章节顺序"""
    try:
        updated = await draft_storage.reorder_chapters(
            project_id=project_id,
            volume_id=body.volume_id,
            chapter_order=body.chapter_order,
        )
        return {"success": True, "updated": [s.model_dump(mode="json") for s in updated]}
    except ValueError:
        raise HTTPException(status_code=400, detail="Draft update failed")


@router.put("/{chapter}/content")
async def update_draft_content(project_id: str, chapter: str, body: UpdateContentRequest):
    """
    Update draft content manually / 手动更新草稿内容
    """
    draft = await draft_storage.save_current_draft(
        project_id=project_id,
        chapter=chapter,
        content=body.content,
        word_count=len(body.content),
        create_prev_backup=True,
    )

    try:
        from app.services.chapter_binding_service import chapter_binding_service

        await chapter_binding_service.build_bindings(project_id, chapter, force=True)
    except Exception as exc:
        logger.warning("Failed to rebuild bindings for %s:%s: %s", project_id, chapter, exc)

    canonical = normalize_chapter_id(chapter) or draft.chapter or chapter
    if body.title is not None:
        summary = await draft_storage.get_chapter_summary(project_id, canonical)
        if summary:
            summary.title = body.title
            summary.word_count = len(body.content)
        else:
            summary = ChapterSummary(
                chapter=canonical,
                title=body.title,
                word_count=len(body.content),
            )
        await draft_storage.save_chapter_summary(project_id, summary)

    return {
        "success": True,
        "version": "current",
        "message": "Content saved",
        "chapter": canonical,
        "title": body.title,
    }


@router.put("/{chapter}/autosave")
async def autosave_draft_content(project_id: str, chapter: str, body: UpdateContentRequest):
    """Auto-save draft content / 自动保存草稿内容（覆盖写，不生成版本）"""
    draft = await draft_storage.save_current_draft(
        project_id=project_id,
        chapter=chapter,
        content=body.content,
        word_count=len(body.content),
        create_prev_backup=False,
    )

    canonical = normalize_chapter_id(chapter) or draft.chapter or chapter
    if body.title is not None:
        summary = await draft_storage.get_chapter_summary(project_id, canonical)
        if summary:
            summary.title = body.title
            summary.word_count = len(body.content)
        else:
            summary = ChapterSummary(
                chapter=canonical,
                title=body.title,
                word_count=len(body.content),
            )
        await draft_storage.save_chapter_summary(project_id, summary)

    return {
        "success": True,
        "version": "current",
        "message": "Content autosaved",
        "chapter": canonical,
        "title": body.title,
    }


# 注意：/{chapter}/{version} 通配路由必须在所有具体路由之后注册
# 否则会遮蔽 /{chapter}/final, /{chapter}/review 等具体路由
# FastAPI 按注册顺序从上到下匹配，先匹配先赢
@router.get("/{chapter}/{version}")
async def get_draft(project_id: str, chapter: str, version: str):
    """Get a specific draft version / 获取指定草稿版本"""
    draft = await draft_storage.get_draft(project_id, chapter, version)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft
