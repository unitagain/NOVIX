# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  同人创作路由 - 提供 Wiki 搜索、爬取和卡片生成 API，支持批量同人导入和构建。
  Fanfiction router - Provides Wiki search, crawling, and character card generation APIs for fanfiction import with batch processing support.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from pathlib import Path
from app.services.search_service import search_service
from app.services.crawler_service import crawler_service
from app.agents.archivist import ArchivistAgent
from app.llm_gateway.gateway import get_gateway
from app.dependencies import get_card_storage, get_canon_storage, get_draft_storage
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/fanfiction", tags=["fanfiction"])

card_storage = get_card_storage()
canon_storage = get_canon_storage()
draft_storage = get_draft_storage()


def _is_http_url(url: str) -> bool:
    """Allow any http/https URL for manual crawling/analysis."""
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def _normalize_language(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if raw.startswith("en"):
        return "en"
    if raw.startswith("zh"):
        return "zh"
    return None


async def _resolve_project_language(project_id: str, request_language: Optional[str] = None) -> str:
    """Resolve writing language from project metadata."""
    explicit = _normalize_language(request_language)
    if explicit:
        return explicit
    pid = str(project_id or "").strip()
    if not pid:
        return "zh"
    try:
        project_file = Path(card_storage.data_dir) / pid / "project.yaml"
        if not project_file.exists():
            return "zh"
        data = await card_storage.read_yaml(project_file) or {}
        language = _normalize_language(data.get("language"))
        return language or "zh"
    except Exception as exc:
        logger.warning("Resolve fanfiction project language failed: %s", exc)
        return "zh"


# Schema definitions
class SearchRequest(BaseModel):
    query: str
    engine: str = "moegirl"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str


class PreviewRequest(BaseModel):
    url: str


class PreviewResponse(BaseModel):
    success: bool
    title: Optional[str] = None
    content: Optional[str] = None
    links: List[Dict[str, str]] = []
    is_list_page: bool = False
    error: Optional[str] = None


class ExtractRequest(BaseModel):
    project_id: str
    language: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    max_cards: Optional[int] = None


class BatchExtractRequest(BaseModel):
    project_id: str
    language: Optional[str] = None
    urls: List[str]


class ExtractResponse(BaseModel):
    success: bool
    proposals: List[Dict] = []
    error: Optional[str] = None


@router.post("/search", response_model=List[SearchResult])
async def search_wikis(request: SearchRequest):
    """Search for relevant Wiki pages"""
    # 搜索源固定为萌娘百科（保证稳定、避免引入不受控站点）
    results = search_service.search_wiki(request.query, engine=request.engine, max_results=10)
    return [SearchResult(**r) for r in results]


@router.post("/preview", response_model=PreviewResponse)
async def preview_page(request: PreviewRequest):
    """
    Scrape a Wiki page and return preview

    Args:
        url: URL of the wiki page

    Returns:
        Page content and metadata
    """
    try:
        if not _is_http_url(request.url):
            return PreviewResponse(success=False, error="仅支持 http/https 链接。")
        result = crawler_service.scrape_page(request.url)

        if not result["success"]:
            return PreviewResponse(success=False, error=result.get("error", "Unknown error"))

        return PreviewResponse(
            success=True,
            title=result["title"],
            content=result["content"],
            links=result["links"],
            is_list_page=result["is_list_page"],
        )
    except Exception:
        return PreviewResponse(success=False, error="Preview request failed")


@router.post("/extract", response_model=ExtractResponse)
async def extract_cards(request: ExtractRequest):
    """Extract a single card summary for a page"""
    try:
        title = request.title or ""
        content = request.content or ""
        url = request.url or ""

        if url:
            if not _is_http_url(url):
                return {"success": False, "error": "仅支持 http/https 链接。", "proposals": []}
            crawl_result = crawler_service.scrape_page(url)
            if not crawl_result.get("success"):
                return {"success": False, "error": crawl_result.get("error", "Crawl failed"), "proposals": []}
            title = crawl_result.get("title") or title
            content = crawl_result.get("llm_content") or crawl_result.get("content") or content

        if not content:
            return {"success": False, "error": "没有可提取的内容。", "proposals": []}

        language = await _resolve_project_language(request.project_id, request.language)
        agent = ArchivistAgent(
            gateway=get_gateway(),
            card_storage=card_storage,
            canon_storage=canon_storage,
            draft_storage=draft_storage,
            language=language,
        )

        proposal = await agent.extract_fanfiction_card(title=title, content=content)
        proposal["source_url"] = url

        return {
            "success": True,
            "proposals": [proposal],
        }
    except Exception as e:
        logger.error("Extraction failed: %s", e, exc_info=True)
        return {"success": False, "error": "Extraction request failed", "proposals": []}


@router.post("/extract/batch", response_model=ExtractResponse)
async def batch_extract_cards(request: BatchExtractRequest):
    """Batch extraction for multiple pages (one card per page)"""
    try:
        max_batch = 80
        urls = request.urls[:max_batch]
        if len(request.urls) > max_batch:
            return {
                "success": False,
                "error": f"一次最多提取 {max_batch} 个链接，请分批操作。",
                "proposals": [],
            }
        invalid = [u for u in urls if not _is_http_url(u)]
        if invalid:
            return {
                "success": False,
                "error": "存在非 http/https 链接，请取消勾选后重试。",
                "proposals": [],
            }
        results = await crawler_service.scrape_pages_concurrent(urls)

        language = await _resolve_project_language(request.project_id, request.language)
        agent = ArchivistAgent(
            gateway=get_gateway(),
            card_storage=card_storage,
            canon_storage=canon_storage,
            draft_storage=draft_storage,
            language=language,
        )

        proposals: List[Dict[str, Any]] = []
        for page in results:
            if not page.get("success"):
                continue
            title = page.get("title") or ""
            content = page.get("llm_content") or page.get("content") or ""
            if not content:
                continue
            proposal = await agent.extract_fanfiction_card(title=title, content=content)
            proposal["source_url"] = page.get("url")
            proposals.append(proposal)

        if not proposals:
            return {"success": False, "error": "No extractable pages", "proposals": []}

        return {"success": True, "proposals": proposals}

    except Exception as e:
        logger.error("Batch extraction failed: %s", e, exc_info=True)
        return {"success": False, "error": "Batch extraction request failed", "proposals": []}
