# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  文本分块搜索路由 - 提供章节草稿文本分块索引和 BM25 搜索 API。
  Text chunk search router - Provides text chunk indexing and BM25 search APIs for chapter drafts with filtering and rebuild capabilities.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.text_chunk_service import text_chunk_service

router = APIRouter(prefix="/projects/{project_id}/text-chunks", tags=["text_chunks"])


class TextChunkSearchRequest(BaseModel):
    query: str
    limit: int = 8
    chapters: Optional[List[str]] = None
    exclude_chapters: Optional[List[str]] = None
    rebuild: bool = False


class TextChunkSearchResponse(BaseModel):
    results: List[Dict[str, Any]]


@router.post("/search", response_model=TextChunkSearchResponse)
async def search_text_chunks(project_id: str, request: TextChunkSearchRequest):
    """Search text chunks.

    Args:
        project_id: Target project id.
        request: Search request payload.

    Returns:
        Search results.
    """
    results = await text_chunk_service.search(
        project_id=project_id,
        query=request.query,
        limit=request.limit,
        chapters=request.chapters,
        exclude_chapters=request.exclude_chapters,
        rebuild=request.rebuild,
    )
    return {"results": results}


@router.post("/rebuild")
async def rebuild_text_chunk_index(project_id: str):
    """Rebuild text chunk index.

    Args:
        project_id: Target project id.

    Returns:
        Rebuild metadata.
    """
    meta = await text_chunk_service.build_index(project_id, force=True)
    return {"success": True, "meta": meta.model_dump(mode="json")}
