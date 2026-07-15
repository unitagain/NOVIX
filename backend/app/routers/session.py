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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.orchestrator import Orchestrator, SessionStatus
from app.error_contract import error_envelope
from app.agents.agent_task import MergePolicy
from app.routers.websocket import broadcast_progress
from app.schemas.draft import ChapterSummary
from app.utils.language import normalize_language
from app.utils.text import normalize_for_compare
from app.jobs.runtime import enqueue_session_compact

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
        except (OSError, UnicodeError, TypeError, ValueError):
            pass
        if explicit:
            language = explicit
        _orchestrators[project_id] = Orchestrator(progress_callback=_progress_callback, language=language)
    else:
        _orchestrators[project_id].set_progress_callback(_progress_callback)
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


class ClassifyIntentRequest(BaseModel):
    """Request for vibe-writing intent classification (Phase 5).

    前端单输入框只需发：用户这句话 + 上下文信号（是否选中正文 / 是否已有草稿）；
    后端判定 write/edit 并回传决策，前端据此调用既有 /session/start 或 /session/edit-suggest。
    """

    chapter: Optional[str] = Field(None, max_length=50, description="Chapter ID")
    message: str = Field(..., min_length=1, max_length=6000, description="User chat message")
    has_selection: bool = Field(False, description="Whether the editor currently has a selection")
    has_draft: bool = Field(False, description="Whether the chapter already has a draft")


class PlanRequest(BaseModel):
    """Request for Phase 11 plan generation（把复杂指令拆成串行 todo）。"""

    goal: str = Field(..., min_length=1, max_length=6000, description="Complex writing instruction to plan")
    context_hint: str = Field("", max_length=4000, description="Optional context hint")


class ChatTurnRequest(BaseModel):
    """Request for Phase 12 unified chat-turn entry（单 Writer 主循环统一对话入口）。"""

    chapter: Optional[str] = Field(None, max_length=50, description="Chapter ID")
    message: str = Field(..., min_length=1, max_length=6000, description="User chat message")
    has_selection: bool = Field(False, description="Editor has a selection")
    has_draft: bool = Field(
        False,
        description="Compatibility hint only; unified chat routing verifies draft state in backend storage",
    )
    target_word_count: int = Field(3000, ge=100, le=20000, description="Target word count for write")
    auto_execute_plan: bool = Field(False, description="Auto-execute plan after generation")
    thinking: bool = Field(False, description="Enable deep thinking (provider param toggle) for this turn")
    fallback_approval_action_id: Optional[str] = Field(
        None,
        max_length=120,
        description="Pending fallback approval action id returned by a previous chat turn",
    )
    fallback_approval_token: Optional[str] = Field(
        None,
        max_length=200,
        description="Single-use fallback approval token returned by a previous chat turn",
    )


class AppendMessageRequest(BaseModel):
    """追加一条对话消息到持久历史（Git-Native sessions/conversation.jsonl）。"""

    role: str = Field("user", description="user | assistant | system")
    content: str = Field("", max_length=200000, description="Message content")
    type: Optional[str] = Field(None, max_length=40, description="Optional message kind, e.g. summary")
    ts: Optional[int] = Field(None, description="Client timestamp in ms; server fills if absent")


# 对话历史 compact 触发阈值（消息数）：超过则后台压缩早期轮次 + 提炼作者偏好 → creative_memory。
_HISTORY_COMPACT_TRIGGER = 120
_HISTORY_KEEP_RECENT = 40


async def _compact_with_plan(orchestrator: Orchestrator, project_id: str) -> dict:
    return await orchestrator.application.commands.run(
        project_id=project_id,
        chapter="",
        intent="compact",
        route_path="compress",
        target_word_count=512,
        operation=lambda: orchestrator.application.conversation.compact(
            project_id, keep_recent=_HISTORY_KEEP_RECENT, trigger_at=_HISTORY_COMPACT_TRIGGER
        ),
    )


class ReviewRequest(BaseModel):
    """Request for on-demand consistency review (Phase 6 · Evaluator)。

    按需触发的一致性评审：对照 canon 给结构化问题清单，不自动改写。
    """

    chapter: str = Field(..., min_length=1, max_length=50, description="Chapter ID")
    content: str = Field(..., min_length=1, max_length=500000, description="Draft content to review")


class AgentTaskRequest(BaseModel):
    """Request for a P4 isolated worker task."""

    task_id: Optional[str] = Field(None, max_length=80, description="Optional client task id")
    kind: str = Field(..., description="retrieve | review | memory_extract | summarize | consistency_check")
    input: Dict[str, object] = Field(default_factory=dict, description="Task input payload")
    permissions: List[str] = Field(default_factory=list, description="Operations requested by the worker")
    budget: Dict[str, object] = Field(default_factory=dict, description="Token/time/result budget metadata")
    output_schema: Dict[str, object] = Field(default_factory=dict, description="Expected structured output shape")
    merge_policy: str = Field(MergePolicy.NO_MERGE.value, description="auto | user_confirm | no_merge")


@router.post("/projects/{project_id}/session/start")
async def start_session(project_id: str, request: StartSessionRequest):
    """Start a new writing session."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.application.commands.run(
        project_id=project_id,
        chapter=request.chapter,
        intent="write",
        route_path="fallback_workflow",
        target_word_count=request.target_word_count,
        operation=lambda: orchestrator.start_session(
            project_id=project_id,
            chapter=request.chapter,
            chapter_title=request.chapter_title,
            chapter_goal=request.chapter_goal,
            target_word_count=request.target_word_count,
            character_names=request.character_names,
        ),
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
    return await orchestrator.application.commands.run(
        project_id=project_id,
        chapter=request.chapter,
        intent="edit",
        route_path="fallback_workflow",
        operation=lambda: orchestrator.process_feedback(
            project_id=project_id,
            chapter=request.chapter,
            feedback=request.feedback,
            action=request.action,
            rejected_entities=request.rejected_entities,
        ),
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
            memory_pack_payload = await orchestrator.application.context.ensure_memory_pack(
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
        return {"success": False, "error": error_envelope(exc).to_dict()}


@router.post("/projects/{project_id}/session/classify-intent")
async def classify_intent(project_id: str, request: ClassifyIntentRequest):
    """Phase 5（vibe writing）：判定本轮 chat 意图（write/edit），供前端单输入框路由。

    后端只判定不执行：前端据返回的 action/scope 调用既有 /session/start 或 /session/edit-suggest，
    复用已验证的撰写/编辑路径，避免重复逻辑。判定结果同时经 WS 以 intent 事件透明展示。
    """
    orchestrator = get_orchestrator(project_id)
    decision = await orchestrator.decide_writing_action(
        project_id=project_id,
        chapter=request.chapter or "",
        message=request.message,
        has_selection=request.has_selection,
        has_draft=request.has_draft,
    )
    return {"success": True, **decision}


@router.post("/projects/{project_id}/session/plan")
async def create_plan(project_id: str, request: PlanRequest):
    """Phase 11：把复杂指令拆成串行 plan（todo）并持久化；返回 plan（含 steps）。

    无可拆步骤则 success=False，前端回退普通撰写/编辑。生成事件经 WS 透明展示。
    """
    orchestrator = get_orchestrator(project_id)
    plan = await orchestrator.application.plans.create_plan(
        project_id,
        goal=request.goal,
        context_hint=request.context_hint,
    )
    if not plan:
        return {"success": False, "error": "no_plan", "message": "指令未能拆出可执行步骤，请直接撰写或编辑。"}
    return {"success": True, "plan": plan}


@router.post("/projects/{project_id}/session/plan/{plan_id}/execute")
async def execute_plan(project_id: str, plan_id: str):
    """Phase 11：串行执行已生成的 plan（正文主线单线程；每步透明事件 + 可打断 + 断点续传）。"""
    orchestrator = get_orchestrator(project_id)
    return await orchestrator.application.plans.execute_plan(project_id, plan_id)


@router.post("/projects/{project_id}/session/chat")
async def chat_turn(project_id: str, request: ChatTurnRequest):
    """Phase 12：单 Writer 主循环统一入口。一句话 → 意图自判 write/edit/continue/plan → 路由执行。

    5 阶段写作能力保留为被路由调用的 human-in-the-loop 检查点（作者可随时打断/反问）。
    前端单输入框只需调本端点，无需自行分步调 classify-intent + start/edit-suggest。
    """
    orchestrator = get_orchestrator(project_id)
    return await orchestrator.run_chat_turn(
        project_id,
        request.chapter or "",
        request.message,
        has_selection=request.has_selection,
        has_draft=request.has_draft,
        target_word_count=request.target_word_count,
        auto_execute_plan=request.auto_execute_plan,
        thinking=request.thinking,
        fallback_approval_action_id=request.fallback_approval_action_id or "",
        fallback_approval_token=request.fallback_approval_token or "",
    )


@router.get("/projects/{project_id}/session/history")
async def get_session_history(project_id: str, limit: int = 0):
    """读取项目的持久化对话历史（Git-Native）。前端开项目时加载，取代脆弱的 localStorage 单点。"""
    orchestrator = get_orchestrator(project_id)
    messages = await orchestrator.application.conversation.load(project_id, limit=max(0, int(limit or 0)))
    return {"success": True, "messages": messages}


@router.post("/projects/{project_id}/session/history")
async def append_session_history(project_id: str, request: AppendMessageRequest):
    """追加一条对话消息到持久历史；过长时后台 compact（压缩早期轮次 + 提炼作者偏好）。"""
    orchestrator = get_orchestrator(project_id)
    item = await orchestrator.application.conversation.append(
        project_id,
        {"role": request.role, "content": request.content, "type": request.type, "ts": request.ts},
    )
    count = await orchestrator.session_history.count(project_id)
    should_compact = count > _HISTORY_COMPACT_TRIGGER
    queued_job = None
    if should_compact:
        queued_job = await enqueue_session_compact(project_id, history_count=count)
    return {
        "success": True,
        "item": item,
        "count": count,
        "compacting": should_compact,
        "compact_job_id": (queued_job or {}).get("id"),
    }


@router.post("/projects/{project_id}/session/history/compact")
async def compact_session_history(project_id: str):
    """显式触发对话压缩 + 偏好提炼（前端可在一轮结束后调用）。"""
    orchestrator = get_orchestrator(project_id)
    result = await _compact_with_plan(orchestrator, project_id)
    return {"success": True, **result}


@router.post("/projects/{project_id}/session/review")
async def review_consistency(project_id: str, request: ReviewRequest):
    """Phase 6（按需评审环）：对照 canon 给本章草稿做一致性评审（只报告不改写）。

    便宜确定性护栏报警 + 贵 LLM 评审；按需触发（用户点"查一致性/润色"），不增默认生成成本。
    """
    orchestrator = get_orchestrator(project_id)
    result = await orchestrator.archivist.review_consistency(
        project_id=project_id,
        chapter=request.chapter,
        draft=request.content,
    )
    return {"success": True, **result}


@router.get("/projects/{project_id}/session/history/compact/state")
async def compact_session_state(project_id: str):
    """Return the active context epoch without exposing conversation text."""
    orchestrator = get_orchestrator(project_id)
    return {"success": True, "context_epoch": await orchestrator.session_history.current_context_epoch(project_id)}


@router.get("/projects/{project_id}/session/history/compact/{artifact_id}")
async def get_compact_artifact(project_id: str, artifact_id: str):
    orchestrator = get_orchestrator(project_id)
    artifact = await orchestrator.session_history.read_compact_artifact(project_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Compact artifact not found")
    return {"success": True, "artifact": artifact}


@router.get("/projects/{project_id}/session/history/compact/{artifact_id}/recover")
async def recover_compact_sources(project_id: str, artifact_id: str):
    """Recover source events referenced by one compact artifact."""
    orchestrator = get_orchestrator(project_id)
    artifact = await orchestrator.session_history.read_compact_artifact(project_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Compact artifact not found")
    items = await orchestrator.session_history.recover_compact_sources(project_id, artifact_id)
    return {"success": True, "items": items, "count": len(items)}


@router.post("/projects/{project_id}/session/agent-task")
async def run_agent_task(project_id: str, request: AgentTaskRequest):
    """Run an isolated worker task without mutating the main Writer state."""
    orchestrator = get_orchestrator(project_id)
    result = await orchestrator.worker_task_service.run_task(
        project_id=project_id,
        task_id=request.task_id,
        kind=request.kind,
        input=request.input or {},
        permissions=request.permissions,
        budget=request.budget,
        output_schema=request.output_schema,
        merge_policy=request.merge_policy,
    )
    return {"success": result.status == "completed", "task": result.to_dict()}


@router.post("/projects/{project_id}/session/answer-questions")
async def answer_questions(project_id: str, request: AnswerQuestionsRequest):
    """Continue session after answering pre-writing questions."""
    orchestrator = get_orchestrator(project_id, request.language)
    answers = [item.model_dump() for item in request.answers]
    return await orchestrator.application.commands.run(
        project_id=project_id,
        chapter=request.chapter,
        intent="continue",
        route_path="fallback_workflow",
        target_word_count=request.target_word_count,
        operation=lambda: orchestrator.answer_questions(
            project_id=project_id,
            chapter=request.chapter,
            chapter_title=request.chapter_title,
            chapter_goal=request.chapter_goal,
            target_word_count=request.target_word_count,
            answers=answers,
            character_names=request.character_names,
        ),
    )


@router.post("/projects/{project_id}/session/cancel")
async def cancel_session(project_id: str):
    """Cancel current session at any stage."""
    orchestrator = get_orchestrator(project_id)

    # 设置通用取消标志，让所有阶段的下一个检查点能感知到取消
    # Set cancel flag so every stage checkpoint can detect it
    orchestrator.cancel_session()

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
    return await orchestrator.application.analysis.analyze_chapter(
        project_id=project_id,
        chapter=request.chapter,
        content=request.content,
        chapter_title=request.chapter_title,
    )


@router.post("/projects/{project_id}/session/save-analysis")
async def save_analysis(project_id: str, request: SaveAnalysisRequest):
    """Persist analysis output (summary, facts, cards)."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.application.analysis.save_analysis(
        project_id=project_id,
        chapter=request.chapter,
        analysis=request.analysis.model_dump(),
        overwrite=request.overwrite,
    )


@router.post("/projects/{project_id}/session/analyze-sync")
async def analyze_sync(project_id: str, request: AnalyzeSyncRequest):
    """Batch analyze and overwrite summaries/facts/cards for selected chapters."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.application.analysis.analyze_sync(project_id, request.chapters)


@router.post("/projects/{project_id}/session/analyze-batch")
async def analyze_batch(project_id: str, request: AnalyzeBatchRequest):
    """Batch analyze chapters and return analysis payload."""
    orchestrator = get_orchestrator(project_id, request.language)
    return await orchestrator.application.analysis.analyze_batch(project_id, request.chapters)


@router.post("/projects/{project_id}/session/save-analysis-batch")
async def save_analysis_batch(project_id: str, request: SaveAnalysisBatchRequest):
    """Persist analysis payload batch."""
    orchestrator = get_orchestrator(project_id, request.language)
    items = [{"chapter": item.chapter, "analysis": item.analysis.model_dump()} for item in request.items]
    return await orchestrator.application.analysis.save_analysis_batch(
        project_id=project_id,
        items=items,
        overwrite=request.overwrite,
    )
