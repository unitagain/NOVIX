# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  会话路由 - 写作会话管理端点
  Session Router - Writing session management endpoints including start,
  feedback processing, and orchestrator lifecycle management.
"""

from typing import Dict, List, Optional, Literal
import time
from collections import OrderedDict

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.orchestrator import Orchestrator, SessionStatus
from app.routers.websocket import broadcast_progress
from app.schemas.draft import ChapterSummary
from app.utils.language import normalize_language
from app.utils.text import normalize_for_compare

router = APIRouter(tags=["session"])

# ========================================================================
# 写作编排器管理 / Writing Orchestrator Management
# ========================================================================

# Per-project orchestrator pool with TTL eviction
# 每个项目独立的 orchestrator 实例池，带 TTL 淘汰
_MAX_POOL_SIZE = 20
_TTL_SECONDS = 3600  # 1 hour
_orchestrators: OrderedDict[str, Orchestrator] = OrderedDict()
_last_access: Dict[str, float] = {}


def _evict_stale() -> None:
    """删除超过TTL的编排器 / Remove orchestrators that have not been accessed within TTL.

    Implements LRU eviction with TTL timeout. Removes stale instances and enforces
    hard pool size limit.
    """
    now = time.monotonic()
    stale = [k for k, t in _last_access.items() if now - t > _TTL_SECONDS]
    for k in stale:
        _orchestrators.pop(k, None)
        _last_access.pop(k, None)
    # Also enforce hard cap
    while len(_orchestrators) > _MAX_POOL_SIZE:
        oldest_key, _ = _orchestrators.popitem(last=False)
        _last_access.pop(oldest_key, None)


def get_orchestrator(project_id: str, request_language: Optional[str] = None) -> Orchestrator:
    """获取或创建项目的编排器实例 / Get or create orchestrator instance for a specific project.

    Manages per-project orchestrator instances with LRU/TTL eviction.
    Ensures each project has its own orchestrator with WebSocket progress callback.

    Args:
        project_id: 项目ID / Project identifier.

    Returns:
        编排器实例 / Orchestrator instance for the project.
    """

    async def _progress_callback(payload: dict) -> None:
        proj = payload.get("project_id")
        if not proj:
            return
        await broadcast_progress(proj, payload)

    _evict_stale()

    explicit = normalize_language(request_language, default="")
    if explicit not in {"zh", "en"}:
        explicit = ""

    if project_id not in _orchestrators:
        # Read language from project.yaml for bilingual support
        language = "zh"
        try:
            from pathlib import Path
            import yaml
            from app.config import settings

            project_yaml = Path(settings.data_dir) / project_id / "project.yaml"
            if project_yaml.exists():
                data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
                language = normalize_language(data.get("language"), default="zh")
        except Exception:
            pass
        if explicit:
            language = explicit
        _orchestrators[project_id] = Orchestrator(progress_callback=_progress_callback, language=language)
    else:
        _orchestrators[project_id].progress_callback = _progress_callback
        if explicit:
            _orchestrators[project_id].set_language(explicit)
        _orchestrators.move_to_end(project_id)
    _last_access[project_id] = time.monotonic()
    return _orchestrators[project_id]


class StartSessionRequest(BaseModel):
    """Request body for starting a session."""

    dialog_max_chars: Literal[2000, 6000] = Field(2000, description="Dialog max chars tier: 2000 | 6000")
    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapter: str = Field(..., min_length=1, max_length=50, description="Chapter ID")
    chapter_title: str = Field(..., min_length=1, max_length=200, description="Chapter title")
    chapter_goal: str = Field(..., min_length=1, max_length=6000, description="Chapter goal")
    target_word_count: int = Field(3000, ge=100, le=50000, description="Target word count")
    character_names: Optional[List[str]] = Field(None, description="Character names")

    @model_validator(mode="after")
    def _validate_chapter_goal_by_tier(self):
        max_chars = int(self.dialog_max_chars)
        if len(self.chapter_goal or "") > max_chars:
            raise ValueError(f"chapter_goal exceeds dialog_max_chars ({max_chars})")
        return self


class FeedbackRequest(BaseModel):
    """Request body for submitting feedback."""

    dialog_max_chars: Literal[2000, 6000] = Field(2000, description="Dialog max chars tier: 2000 | 6000")
    chapter: str = Field(..., min_length=1, max_length=50, description="Chapter ID")
    feedback: str = Field(..., min_length=1, max_length=6000, description="User feedback")
    action: str = Field("revise", description="Action: revise or confirm")
    rejected_entities: Optional[List[str]] = Field(None, description="Rejected entity names")

    @model_validator(mode="after")
    def _validate_feedback_by_tier(self):
        max_chars = int(self.dialog_max_chars)
        if len(self.feedback or "") > max_chars:
            raise ValueError(f"feedback exceeds dialog_max_chars ({max_chars})")
        return self


class EditSuggestRequest(BaseModel):
    """Request body for suggesting an edit on current (unsaved) content."""

    dialog_max_chars: Literal[2000, 6000] = Field(2000, description="Dialog max chars tier: 2000 | 6000")
    chapter: Optional[str] = Field(None, max_length=50, description="Chapter ID (optional)")
    content: str = Field(..., min_length=1, max_length=500000, description="Current content to edit (may be unsaved)")
    instruction: str = Field(..., min_length=1, max_length=6000, description="Edit instruction")
    rejected_entities: Optional[List[str]] = Field(None, description="Rejected entity names")
    context_mode: Optional[str] = Field(
        "quick",
        description="Context mode: quick (use memory pack) | full (rebuild memory pack)",
    )
    selection_text: Optional[str] = Field(
        None,
        description="Optional selection text for selection-scoped editing (for validation / context).",
    )
    selection_start: Optional[int] = Field(
        None,
        description="Optional selection start offset (0-based, in normalized \\n text).",
    )
    selection_end: Optional[int] = Field(
        None,
        description="Optional selection end offset (0-based, in normalized \\n text).",
    )

    @model_validator(mode="after")
    def _validate_instruction_by_tier(self):
        max_chars = int(self.dialog_max_chars)
        if len(self.instruction or "") > max_chars:
            raise ValueError(f"instruction exceeds dialog_max_chars ({max_chars})")
        return self


class QuestionAnswer(BaseModel):
    """Answer to a pre-writing question."""

    type: str = Field(..., description="Question type")
    question: Optional[str] = Field(None, description="Question text")
    key: Optional[str] = Field(None, description="Stable question key")
    answer: str = Field(..., description="User answer")


class AnswerQuestionsRequest(BaseModel):
    """Request to answer pre-writing questions."""

    dialog_max_chars: Literal[2000, 6000] = Field(2000, description="Dialog max chars tier: 2000 | 6000")
    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapter: str = Field(..., description="Chapter ID")
    chapter_title: str = Field(..., description="Chapter title")
    chapter_goal: str = Field(..., description="Chapter goal")
    target_word_count: int = Field(3000, description="Target word count")
    character_names: Optional[List[str]] = Field(None, description="Character names")
    answers: List[QuestionAnswer] = Field(default_factory=list, description="Answers")

    @model_validator(mode="after")
    def _validate_chapter_goal_by_tier(self):
        max_chars = int(self.dialog_max_chars)
        if len(self.chapter_goal or "") > max_chars:
            raise ValueError(f"chapter_goal exceeds dialog_max_chars ({max_chars})")
        return self


@router.post("/projects/{project_id}/session/start")
async def start_session(project_id: str, request: StartSessionRequest):
    """Start a new writing session."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.start_session(
        project_id=project_id,
        chapter=request.chapter,
        chapter_title=request.chapter_title,
        chapter_goal=request.chapter_goal,
        target_word_count=request.target_word_count,
        character_names=request.character_names,
    )


@router.get("/projects/{project_id}/session/status")
async def get_session_status(project_id: str):
    """Get current session status."""
    orchestrator = get_orchestrator(project_id)
    status = orchestrator.get_status()

    if status["project_id"] != project_id:
        return {"status": "idle", "message": "No active session for this project"}

    return status


@router.post("/projects/{project_id}/session/feedback")
async def submit_feedback(project_id: str, request: FeedbackRequest):
    """Submit user feedback."""
    orchestrator = get_orchestrator(project_id)
    return await orchestrator.process_feedback(
        project_id=project_id,
        chapter=request.chapter,
        feedback=request.feedback,
        action=request.action,
        rejected_entities=request.rejected_entities,
    )


@router.post("/projects/{project_id}/session/edit-suggest")
async def suggest_edit(project_id: str, request: EditSuggestRequest):
    """Suggest a diff-style revision without persisting it."""
    try:
        orchestrator = get_orchestrator(project_id)
        memory_pack_payload = None
        if request.chapter:
            mode = str(request.context_mode or "quick").strip().lower()
            force_refresh = mode == "full"
            memory_pack_payload = await orchestrator.ensure_memory_pack(
                project_id=project_id,
                chapter=request.chapter,
                chapter_goal="",
                scene_brief=None,
                user_feedback=request.instruction,
                force_refresh=force_refresh,
                source="editor",
                chapter_text_override=request.content,
            )
        if request.selection_start is not None and request.selection_end is not None:
            revised = await orchestrator.editor.suggest_revision_selection_range(
                project_id=project_id,
                original_draft=request.content,
                selection_start=request.selection_start,
                selection_end=request.selection_end,
                selection_text=request.selection_text,
                user_feedback=request.instruction,
                rejected_entities=request.rejected_entities or [],
                memory_pack=memory_pack_payload,
            )
        elif request.selection_text:
            # Backward compatible path: selection by substring matching (less reliable).
            revised = await orchestrator.editor.suggest_revision_selection(
                project_id=project_id,
                original_draft=request.content,
                selection_text=request.selection_text,
                selection_occurrence=1,
                user_feedback=request.instruction,
                rejected_entities=request.rejected_entities or [],
                memory_pack=memory_pack_payload,
            )
        else:
            revised = await orchestrator.editor.suggest_revision(
                project_id=project_id,
                original_draft=request.content,
                user_feedback=request.instruction,
                rejected_entities=request.rejected_entities or [],
                memory_pack=memory_pack_payload,
            )
        original_norm = normalize_for_compare(request.content)
        revised_norm = normalize_for_compare(revised)
        if revised_norm == original_norm:
            return {
                "success": False,
                "error": "未能生成可应用的差异修改：请在指令中复制粘贴要修改的原句/段落，或使用\u201c选区编辑\u201d进行精确定位。",
            }
        return {"success": True, "revised_content": revised, "word_count": len(revised)}
    except ValueError as exc:
        # Expected: patch ops could not be applied, surface as user-facing error (no 500).
        return {"success": False, "error": str(exc)}


@router.post("/projects/{project_id}/session/answer-questions")
async def answer_questions(project_id: str, request: AnswerQuestionsRequest):
    """Continue session after answering pre-writing questions."""
    orchestrator = get_orchestrator(project_id, request.language)
    answers = [item.model_dump() for item in request.answers]
    return await orchestrator.answer_questions(
        project_id=project_id,
        chapter=request.chapter,
        chapter_title=request.chapter_title,
        chapter_goal=request.chapter_goal,
        target_word_count=request.target_word_count,
        answers=answers,
        character_names=request.character_names,
    )


@router.post("/projects/{project_id}/session/cancel")
async def cancel_session(project_id: str):
    """Cancel current session at any stage."""
    orchestrator = get_orchestrator(project_id)

    # 设置通用取消标志，让所有阶段的下一个检查点能感知到取消
    # Set cancel flag so every stage checkpoint can detect it
    orchestrator._cancelled = True

    # 同时取消正在进行的流任务（流式写作阶段）
    # Also cancel the active stream task if writing is in progress
    if orchestrator._stream_task:
        orchestrator._stream_task.cancel()
        orchestrator._stream_task = None

    orchestrator.current_status = SessionStatus.IDLE
    orchestrator.current_project_id = None
    orchestrator.current_chapter = None

    await broadcast_progress(
        project_id,
        {
            "type": "cancelled",
            "status": SessionStatus.IDLE.value,
            "message": "Session cancelled",
            "project_id": project_id,
            "chapter": None,
            "iteration": 0,
        },
    )

    return {"success": True, "message": "Session cancelled"}


class AnalyzeRequest(BaseModel):
    """Request body for chapter analysis."""

    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapter: str = Field(..., description="Chapter ID")
    content: Optional[str] = Field(None, description="Draft content")
    chapter_title: Optional[str] = Field(None, description="Chapter title")


class AnalysisPayload(BaseModel):
    """Structured analysis payload."""

    summary: ChapterSummary
    facts: List[dict] = Field(default_factory=list)
    timeline_events: List[dict] = Field(default_factory=list)
    character_states: List[dict] = Field(default_factory=list)
    proposals: List[dict] = Field(default_factory=list)


class SaveAnalysisRequest(BaseModel):
    """Request body for saving analysis output."""

    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapter: str = Field(..., description="Chapter ID")
    analysis: AnalysisPayload
    overwrite: bool = Field(False, description="Overwrite existing facts/cards")


class AnalyzeSyncRequest(BaseModel):
    """Request body for analysis sync."""

    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapters: List[str] = Field(default_factory=list, description="Chapter IDs")


class AnalyzeBatchRequest(BaseModel):
    """Request body for batch analysis."""

    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    chapters: List[str] = Field(default_factory=list, description="Chapter IDs")


class SaveAnalysisBatchItem(BaseModel):
    """Batch item for saving analysis."""

    chapter: str = Field(..., description="Chapter ID")
    analysis: AnalysisPayload


class SaveAnalysisBatchRequest(BaseModel):
    """Request body for saving analysis batch."""

    language: Optional[str] = Field(None, description="Writing language override: zh/en or locale-like values")
    items: List[SaveAnalysisBatchItem] = Field(default_factory=list)
    overwrite: bool = Field(False, description="Overwrite existing facts/cards")


@router.post("/projects/{project_id}/session/analyze")
async def analyze_chapter(project_id: str, request: AnalyzeRequest):
    """Analyze chapter content manually."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.analyze_chapter(
        project_id=project_id,
        chapter=request.chapter,
        content=request.content,
        chapter_title=request.chapter_title,
    )


@router.post("/projects/{project_id}/session/save-analysis")
async def save_analysis(project_id: str, request: SaveAnalysisRequest):
    """Persist analysis output (summary, facts, cards)."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.save_analysis(
        project_id=project_id,
        chapter=request.chapter,
        analysis=request.analysis.model_dump(),
        overwrite=request.overwrite,
    )


@router.post("/projects/{project_id}/session/analyze-sync")
async def analyze_sync(project_id: str, request: AnalyzeSyncRequest):
    """Batch analyze and overwrite summaries/facts/cards for selected chapters."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.analyze_sync(project_id, request.chapters)


@router.post("/projects/{project_id}/session/analyze-batch")
async def analyze_batch(project_id: str, request: AnalyzeBatchRequest):
    """Batch analyze chapters and return analysis payload."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.analyze_batch(project_id, request.chapters)


@router.post("/projects/{project_id}/session/save-analysis-batch")
async def save_analysis_batch(project_id: str, request: SaveAnalysisBatchRequest):
    """Persist analysis payload batch."""
    orchestrator = get_orchestrator(project_id, request.language)
    items = [{"chapter": item.chapter, "analysis": item.analysis.model_dump()} for item in request.items]
    return await orchestrator.save_analysis_batch(
        project_id=project_id,
        items=items,
        overwrite=request.overwrite,
    )
