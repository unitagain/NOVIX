"""
导出路由 / Export router
"""

from __future__ import annotations

from typing import List
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.export_service import export_service

router = APIRouter(prefix="/projects/{project_id}/export", tags=["export"])


class ExportRequest(BaseModel):
    """导出请求 / Export request."""

    chapter_ids: List[str] = Field(default_factory=list)
    format: str = Field(default="txt")
    include_chapter_titles: bool = Field(default=True)


def _build_content_disposition(filename: str) -> str:
    """
    构建兼容中文文件名的下载头 / Build Content-Disposition with UTF-8 filename support.

    Starlette headers must be latin-1 encodable, so we provide:
    - filename: ASCII fallback
    - filename*: RFC 5987 UTF-8 encoded filename
    """
    raw = str(filename or "").strip() or "wenshape_export.txt"
    safe_ascii = "".join(ch if ord(ch) < 128 else "_" for ch in raw)
    safe_ascii = safe_ascii.replace('"', "_")
    if not safe_ascii.strip("._ "):
        safe_ascii = "wenshape_export.txt"
    utf8_name = quote(raw.encode("utf-8"))
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{utf8_name}"


@router.post("")
async def export_project_content(project_id: str, body: ExportRequest) -> Response:
    """导出章节正文 / Export chapter content."""
    artifact = await export_service.export_chapters(
        project_id=project_id,
        chapter_ids=body.chapter_ids,
        fmt=body.format,
        include_chapter_titles=body.include_chapter_titles,
    )
    headers = {
        "Content-Disposition": _build_content_disposition(artifact.filename),
    }
    return Response(content=artifact.content, media_type=artifact.media_type, headers=headers)
