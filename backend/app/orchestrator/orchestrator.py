# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Orchestrator 编排器 - 协调多智能体写作工作流
  Coordinates the multi-agent writing workflow with support for research loops,
  writer context preparation, and real-time progress streaming.

  Heavy method groups are extracted into Mixin classes:
  - ContextMixin  (_context_mixin.py)  — memory-pack & writer-context preparation
  - AnalysisMixin (_analysis_mixin.py) — chapter analysis, canon persistence, card creation
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.llm_gateway import get_gateway
from app.llm_gateway.errors import LLMError
from app.storage import (
    CardStorage,
    CanonStorage,
    DraftStorage,
    MemoryPackStorage,
    CreativeMemoryStorage,
    PlanStore,
    SessionHistoryStorage,
)
from app.agents import ArchivistAgent, WriterAgent, EditorAgent
from app.agents.planner import generate_plan
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.embeddings import create_embeddings_backend
from app.context_engine.trace_collector import trace_collector
from app.orchestrator.storage_adapter import UnifiedStorageAdapter
from app.schemas.draft import SceneBrief
from app.utils.language import normalize_language
from app.utils.logger import get_logger
from app.utils.text import normalize_prose_paragraphs
from app.services.chapter_binding_service import chapter_binding_service
from app.orchestrator._types import SessionStatus
from app.orchestrator._context_mixin import ContextMixin
from app.orchestrator._analysis_mixin import AnalysisMixin
from app.orchestrator.architecture import route_contract
from app.orchestrator.orchestrator_helpers import (
    build_context_debug,
    extract_scene_brief_names,
    extract_top_sources,
    normalize_chapter_id,
    trim_context_package,
)

logger = get_logger(__name__)


class Orchestrator(ContextMixin, AnalysisMixin):
    """
    编排器 - 协调多智能体写作工作流

    Orchestrates a multi-agent writing workflow including scene brief generation,
    research loops, draft writing, and editorial revision. Manages session state,
    context budgets, and streaming output.

    Attributes:
        card_storage (CardStorage): 角色/世界观卡片存储 / Character and world card storage.
        canon_storage (CanonStorage): 事实表和时间线存储 / Canon facts and timeline events storage.
        draft_storage (DraftStorage): 章节草稿和摘要存储 / Draft and summary storage.
        gateway (LLMGateway): LLM 调用网关 / Unified LLM gateway.
        archivist (ArchivistAgent): 档案员智能体 / Agent for scene brief and canon updates.
        writer (WriterAgent): 撰稿人智能体 / Agent for draft generation.
        editor (EditorAgent): 编辑智能体 / Agent for revision.
        select_engine (ContextSelectEngine): 上下文选择引擎 / Context selection engine.
        progress_callback (Optional[Callable]): 进度更新回调 / Callback for progress updates.
        current_status (SessionStatus): 当前会话状态 / Current session status.
        max_iterations (int): 最大修订轮次 / Maximum revision iterations.
        max_question_rounds (int): 最大提问轮次 / Maximum pre-writing question rounds.
        max_research_rounds (int): 最大研究轮次 / Maximum research loop rounds.
    """

    def __init__(
        self, data_dir: Optional[str] = None, progress_callback: Optional[Callable] = None, language: str = "zh"
    ):
        """
        初始化编排器 / Initialize the Orchestrator.

        Note: Must use consistent path resolution logic with Settings.data_dir
        to avoid data directory misalignment where drafts are written but
        not visible to the frontend status interface.

        Args:
            data_dir: 数据目录路径 / Path to data directory (defaults to Settings.data_dir).
            progress_callback: 进度更新回调函数 / Async callback for progress events.
            language: 写作语言 / Writing language ("zh" or "en").
        """
        if data_dir is None:
            from app.config import settings

            data_dir = settings.data_dir
        self.card_storage = CardStorage(data_dir)
        self.canon_storage = CanonStorage(data_dir)
        self.draft_storage = DraftStorage(data_dir)
        self.memory_pack_storage = MemoryPackStorage(data_dir)
        self.creative_memory_storage = CreativeMemoryStorage(data_dir)
        self.plan_store = PlanStore(data_dir)
        self.session_history = SessionHistoryStorage(data_dir)

        self.gateway = get_gateway()

        normalized_language = normalize_language(language, default="zh")
        self.language = normalized_language
        self.archivist = ArchivistAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage,
            language=normalized_language,
        )
        self.writer = WriterAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage,
            language=normalized_language,
        )
        self.editor = EditorAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage,
            language=normalized_language,
        )

        self.storage_adapter = UnifiedStorageAdapter(self.card_storage, self.canon_storage, self.draft_storage)
        # Phase 4: 注入嵌入后端（默认 config.retrieval.embeddings.enabled=true → 语义+词法融合）。
        # 初始化失败（缺库/缺模型）时工厂返回 None，检索自动降级为纯词法，绝不阻断写作主流程。
        try:
            from app.config import config as _app_config

            embeddings_backend = create_embeddings_backend(_app_config)
        except Exception as exc:
            logger.warning("Embeddings backend unavailable; semantic retrieval disabled: %s", exc)
            embeddings_backend = None
        self.select_engine = ContextSelectEngine(embeddings_service=embeddings_backend)
        # Phase 7 降级可见：启动即明示语义检索配置状态（依赖缺失会在首次检索时降级，见 select_engine）。
        if embeddings_backend is not None:
            logger.info("语义检索：已配置启用（嵌入后端就绪；缺 fastembed/模型时首次检索降级为纯词法）。")
        else:
            logger.info("语义检索：纯词法（embeddings.enabled=false 或嵌入后端不可用）。")

        self.progress_callback = progress_callback
        self.current_status = SessionStatus.IDLE
        self.current_project_id: Optional[str] = None
        self.current_chapter: Optional[str] = None
        self.iteration_count = 0
        self.question_round = 0
        self._stream_tasks: Dict[str, asyncio.Task] = {}  # 按章节隔离的流式任务
        self._last_stream_results: Dict[str, Dict[str, Any]] = {}
        self._cancelled: bool = False  # 通用取消标志，用于在所有阶段响应用户取消 / General cancel flag

        # Load session config from config.yaml with sensible defaults
        # 从 config.yaml 加载会话配置
        from app.config import config as app_cfg

        session_cfg = app_cfg.get("session", {})
        self.max_iterations = int(session_cfg.get("max_iterations", 5))
        self.max_question_rounds = int(session_cfg.get("max_question_rounds", 2))
        self.max_research_rounds = int(session_cfg.get("max_research_rounds", 5))

    def set_language(self, language: str) -> None:
        normalized = normalize_language(language, default=self.language)
        if normalized not in {"zh", "en"}:
            return
        self.language = normalized
        try:
            self.archivist.language = normalized
            self.writer.language = normalized
            self.editor.language = normalized
        except Exception:
            return

    def _p(self, zh: str, en: str) -> str:
        return en if self.language == "en" else zh

    async def start_session(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int = 3000,
        character_names: Optional[list] = None,
        skip_questions: bool = False,
    ) -> Dict[str, Any]:
        """
        开始一个新的写作会话 / Start a new writing session.

        Workflow:
        1. 档案员生成场景简要 (Scene Brief) / Archivist generates scene brief
        2. 提出预写问题 (Pre-writing Questions) / Generate pre-writing questions if needed
        3. 准备写作上下文 / Prepare writer context with memory packs
        4. 撰稿人生成初稿 (Draft) / Writer generates initial draft
        5. 编辑修订和反问 / Editor revises or questions are answered
        6. 返回等待用户反馈 / Wait for user feedback

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID (e.g., V1C001) / Chapter identifier.
            chapter_title: 章节标题 / Chapter title.
            chapter_goal: 章节目标 / Chapter writing goal/instruction.
            target_word_count: 目标字数 / Target word count (default 3000).
            character_names: 核心角色列表 / Optional character names to focus on.

        Returns:
            会话开始结果 / Session start result containing:
            - success: 是否成功 / Whether initialization succeeded.
            - status: 会话状态 / Current session status.
            - questions: 预写问题列表 / Pre-writing questions if any.
            - scene_brief: 场景简要 / Generated scene brief.
            - context_debug: 上下文调试信息 / Context debugging info.
            - draft_v1: 初稿 / Initial draft (when ready).
            - proposals: 设定建议 / Setting proposals from draft.
        """
        self.current_project_id = project_id
        self.current_chapter = chapter
        self.iteration_count = 0
        self.question_round = 0
        self._last_stream_results = {}
        self._cancelled = False  # 重置取消标志 / Reset cancel flag on new session

        try:
            # ============================================================================
            # 步骤 1: 档案员生成场景简要 / Step 1: Archivist generates scene brief
            # ============================================================================
            # 场景简要包含：当前情节上下文、相关角色、关键设定事实
            # Scene brief contains: plot context, relevant characters, key canonical facts
            try:
                await trace_collector.start_agent_trace("archivist", f"{project_id}:{chapter}")
            except Exception as exc:
                logger.warning("Trace start failed: %s", exc)

            await self._update_status(SessionStatus.GENERATING_BRIEF, "Archivist is preparing the scene brief...")

            if self._cancelled:
                return await self._handle_cancelled()

            archivist_result = await self.archivist.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "chapter_title": chapter_title,
                    "chapter_goal": chapter_goal,
                    "characters": character_names or [],
                },
            )

            if self._cancelled:
                return await self._handle_cancelled()

            if not archivist_result.get("success"):
                try:
                    await trace_collector.end_agent_trace("archivist", status="failed")
                except Exception as exc:
                    logger.warning("Trace end failed: %s", exc)
                return await self._handle_error("Scene brief generation failed")

            scene_brief = archivist_result["scene_brief"]
            try:
                await trace_collector.end_agent_trace("archivist", status="completed")
            except Exception as exc:
                logger.warning("Trace end failed: %s", exc)

            if self._cancelled:
                return await self._handle_cancelled()

            context_bundle = await self._prepare_writer_context(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                character_names=character_names,
            )
            writer_context = context_bundle["writer_context"]
            critical_items = context_bundle["critical_items"]
            dynamic_items = context_bundle["dynamic_items"]
            context_debug = self._build_context_debug(context_bundle.get("working_memory_payload"))

            if self._cancelled:
                return await self._handle_cancelled()

            questions = context_bundle.get("questions") or None
            if not questions:
                questions = await self.writer.generate_questions(
                    context_package=writer_context.get("context_package"),
                    scene_brief=scene_brief,
                    chapter_goal=chapter_goal,
                )
            if questions and not skip_questions and self.question_round < self.max_question_rounds:
                self.question_round += 1
                await self._update_status(SessionStatus.WAITING_USER_INPUT, "Waiting for user input...")
                return {
                    "success": True,
                    "status": SessionStatus.WAITING_USER_INPUT,
                    "questions": questions,
                    "scene_brief": scene_brief,
                    "question_round": self.question_round,
                    "context_debug": context_debug,
                }

            try:
                summary_text = str(getattr(scene_brief, "summary", scene_brief))[:100]
                await trace_collector.record_handoff(
                    "archivist",
                    "writer",
                    f"Scene brief prepared: {summary_text}...",
                )
                await trace_collector.start_agent_trace("writer", f"{project_id}:{chapter}")
                await trace_collector.record_context_select(
                    "writer",
                    selected_count=len(critical_items) + len(dynamic_items),
                    total_candidates=100,
                    token_usage=sum(len(str(i.content)) for i in critical_items + dynamic_items),
                )
            except Exception as exc:
                logger.warning("Trace writer setup failed: %s", exc)

            result = await self._run_writing_flow(
                project_id=project_id,
                chapter=chapter,
                writer_context=writer_context,
                target_word_count=target_word_count,
                working_memory_payload=context_bundle.get("working_memory_payload"),
            )
            if result.get("success"):
                result["scene_brief"] = scene_brief
                if context_debug:
                    result["context_debug"] = context_debug
            return result

        except Exception as exc:
            return await self._handle_error(f"Session error: {exc}", exc=exc)

    async def answer_questions(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int,
        answers: List[Dict[str, str]],
        character_names: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        用户回答预写问题后继续会话 / Continue session after user answers pre-writing questions.

        处理用户提供的答案，可能产生后续问题或进入正式创作。
        Process answers, potentially triggering follow-up questions or starting draft.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            chapter_title: 章节标题 / Chapter title.
            chapter_goal: 章节目标 / Chapter goal.
            target_word_count: 目标字数 / Target word count.
            answers: 用户答题列表 / User answers (list of {question, answer} pairs).
            character_names: 核心角色列表 / Optional character names.

        Returns:
            后续会话结果 / Session continuation result.
        """
        self.current_project_id = project_id
        self.current_chapter = chapter

        if self._cancelled:
            return await self._handle_cancelled()

        scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        if not scene_brief:
            archivist_result = await self.archivist.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "chapter_title": chapter_title,
                    "chapter_goal": chapter_goal,
                    "characters": character_names or [],
                },
            )
            if not archivist_result.get("success"):
                return await self._handle_error("Scene brief generation failed")
            scene_brief = archivist_result["scene_brief"]

        if self._cancelled:
            return await self._handle_cancelled()

        context_bundle = await self._prepare_writer_context(
            project_id=project_id,
            chapter=chapter,
            chapter_goal=chapter_goal,
            scene_brief=scene_brief,
            character_names=character_names,
            user_answers=answers,
            # Key fix: After pre-writing Q&A, don't trigger another research loop.
            # Reuse the existing memory pack (if present) and proceed to writing.
            force_refresh_memory_pack=False,
            memory_pack_source="writer_answer",
        )
        await self._persist_answer_memory(project_id, chapter, answers)
        writer_context = context_bundle["writer_context"]
        writer_context["user_answers"] = answers
        context_debug = self._build_context_debug(context_bundle.get("working_memory_payload"))

        followup_questions = context_bundle.get("questions") or []
        answered_keys = set()
        for item in answers or []:
            if not isinstance(item, dict):
                continue
            q_type = str(item.get("type") or "").strip()
            q_text = str(item.get("question") or item.get("text") or "").strip()
            q_key = str(item.get("key") or item.get("question_key") or "").strip()
            if q_key:
                answered_keys.add(("key", q_key))
            if q_type and q_text:
                answered_keys.add((q_type, q_text))
        if answered_keys and followup_questions:
            followup_questions = [
                q
                for q in followup_questions
                if ("key", str(q.get("key") or "").strip()) not in answered_keys
                and (str(q.get("type") or "").strip(), str(q.get("text") or "").strip()) not in answered_keys
            ]

        if followup_questions and answers and self.question_round < self.max_question_rounds:
            self.question_round += 1
            await self._update_status(SessionStatus.WAITING_USER_INPUT, "Waiting for user input...")
            return {
                "success": True,
                "status": SessionStatus.WAITING_USER_INPUT,
                "questions": followup_questions,
                "scene_brief": scene_brief,
                "question_round": self.question_round,
                "context_debug": context_debug,
            }

        result = await self._run_writing_flow(
            project_id=project_id,
            chapter=chapter,
            writer_context=writer_context,
            target_word_count=target_word_count,
            working_memory_payload=context_bundle.get("working_memory_payload"),
        )
        if result.get("success"):
            result["scene_brief"] = scene_brief
            if context_debug:
                result["context_debug"] = context_debug
        return result

    async def process_feedback(
        self,
        project_id: str,
        chapter: str,
        feedback: str,
        action: str = "revise",
        rejected_entities: list = None,
    ) -> Dict[str, Any]:
        """
        处理用户反馈（修订或确认） / Process user feedback on draft.

        Handles two main action types:
        1. 'confirm' - 用户确认当前草稿，进入最终分析 / User confirms draft, proceed to analysis
        2. 'revise' - 用户提出修改意见，触发修订流程 / User provides revision feedback

        对于短草稿（≤500字）直接触发撰稿人重写；对于长草稿则触发编辑修订。

        For short drafts (≤500 chars), triggers writer rewrite;
        for longer drafts, triggers editor revision.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            feedback: 用户反馈内容 / User feedback text.
            action: 操作类型 / Action type: 'revise' or 'confirm'.
            rejected_entities: 被否决的设定 / Optional rejected entity names.

        Returns:
            反馈处理结果 / Feedback processing result.

        Raises:
            Returns error dict if iterations limit exceeded.
        """
        if action == "confirm":
            return await self._finalize_chapter(project_id, chapter)

        self.iteration_count += 1
        if self.iteration_count >= self.max_iterations:
            return {
                "success": False,
                "error": "Maximum iterations reached",
                "message": "Maximum revision iterations reached.",
            }

        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            latest_version = versions[-1] if versions else "v1"
            latest_draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
            draft_length = len(latest_draft.content) if latest_draft and latest_draft.content else 0

            if draft_length <= 500:
                await self._update_status(SessionStatus.WRITING_DRAFT, "Writer is refining based on feedback...")
                scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
                if not scene_brief:
                    return await self._handle_error("Scene brief not found for rewrite")

                context_bundle = await self._prepare_writer_context(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_goal=scene_brief.goal,
                    scene_brief=scene_brief,
                    character_names=None,
                )
                writer_context = context_bundle["writer_context"]
                writer_context["user_feedback"] = feedback

                writer_result = await self.writer.execute(
                    project_id=project_id,
                    chapter=chapter,
                    context=writer_context,
                )
                if not writer_result.get("success"):
                    return await self._handle_error("Rewrite failed")

                draft = writer_result["draft"]
                await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")
                proposals = await self._detect_proposals(project_id, draft)

                return {
                    "success": True,
                    "status": SessionStatus.WAITING_FEEDBACK,
                    "draft": draft,
                    "version": draft.version,
                    "iteration": self.iteration_count,
                    "proposals": proposals,
                }

            await self._update_status(SessionStatus.EDITING, "Revising based on feedback...")

            memory_pack_payload = await self.ensure_memory_pack(
                project_id=project_id,
                chapter=chapter,
                chapter_goal="",
                scene_brief=None,
                user_feedback=feedback,
                force_refresh=False,
                source="editor",
            )

            editor_result = await self.editor.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": latest_version,
                    "user_feedback": feedback,
                    "rejected_entities": rejected_entities or [],
                    "memory_pack": memory_pack_payload,
                },
            )

            if not editor_result.get("success"):
                return await self._handle_error("Revision failed")

            await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")

            proposals = await self._detect_proposals(project_id, editor_result["draft"])

            return {
                "success": True,
                "status": SessionStatus.WAITING_FEEDBACK,
                "draft": editor_result["draft"],
                "version": editor_result.get("version", latest_version),
                "iteration": self.iteration_count,
                "proposals": proposals,
            }

        except Exception as exc:
            return await self._handle_error(f"Feedback processing error: {exc}", exc=exc)

    async def _finalize_chapter(self, project_id: str, chapter: str) -> Dict[str, Any]:
        """Finalize chapter and save final draft."""
        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            if not versions:
                return await self._handle_error("No draft found to finalize")

            latest_version = versions[-1]
            draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
            if not draft:
                return await self._handle_error("No draft content found to finalize")

            await self.draft_storage.save_final_draft(project_id=project_id, chapter=chapter, content=draft.content)

            await self._analyze_content(project_id, chapter, draft.content)

            await self._update_status(SessionStatus.COMPLETED, "Chapter completed.")

            return {
                "success": True,
                "status": SessionStatus.COMPLETED,
                "message": "Chapter finalized successfully",
                "final_draft": draft,
            }

        except Exception as exc:
            return await self._handle_error(f"Finalization error: {exc}", exc=exc)

    async def _run_writing_flow(
        self,
        project_id: str,
        chapter: str,
        writer_context: Dict[str, Any],
        target_word_count: int,
        working_memory_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行写作流：启动流式输出并等待反馈 / Run writer flow and wait for user feedback.

        步骤 / Steps:
        1. 更新状态为 WRITING_DRAFT / Update status to WRITING_DRAFT
        2. 取消任何现有的流式任务 / Cancel any existing stream task
        3. 创建新的流式任务并等待完成 / Create new stream task and wait
        4. 检测设定建议 / Detect setting proposals from draft
        5. 返回草稿和设定建议供用户反馈 / Return draft and proposals for feedback

        注意：使用后置刷新策略避免每次修订都重建记忆包
        Note: Uses post-write refresh to avoid rebuilding memory packs on every revision

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            writer_context: 写作上下文 / Context for writer agent.
            target_word_count: 目标字数 / Target word count.
            working_memory_payload: 工作记忆载荷 / Working memory payload for tracking.

        Returns:
            写作流程结果 / Writing flow result with draft and proposals.
        """
        await self._update_status(SessionStatus.WRITING_DRAFT, "Writer is drafting...")

        writer_payload = dict(writer_context)
        writer_payload["target_word_count"] = target_word_count

        # 取消该章节的旧流式任务（如有），按章节隔离避免跨章节竞态
        # Cancel previous stream task for this chapter to prevent cross-chapter race
        chapter_key = str(chapter)
        old_task = self._stream_tasks.pop(chapter_key, None)
        if old_task:
            old_task.cancel()

        try:
            task = asyncio.create_task(
                self._stream_writer_output(
                    project_id,
                    chapter,
                    writer_payload,
                    working_memory_payload=working_memory_payload,
                )
            )
            self._stream_tasks[chapter_key] = task
            await task
            self._stream_tasks.pop(chapter_key, None)
        except asyncio.CancelledError:
            # 区分用户主动取消（_cancelled=True）和其他原因的 task 取消
            # Distinguish user-initiated cancel from other task cancellations
            if self._cancelled:
                return await self._handle_cancelled()
            return await self._handle_error("Stream cancelled")
        except Exception as exc:
            return await self._handle_error(f"Draft generation failed: {exc}", exc=exc)

        # 流任务正常完成，但若中途被取消（_stream_writer_output 静默 return），也需要退出
        if self._cancelled:
            return await self._handle_cancelled()

        versions = await self.draft_storage.list_draft_versions(project_id, chapter)
        latest_version = versions[-1] if versions else "v1"
        draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
        if not draft:
            fallback = self._last_stream_results.get(str(chapter)) or {}
            fallback_draft = fallback.get("draft")
            fallback_proposals = fallback.get("proposals") or []
            if isinstance(fallback_draft, dict) and str(fallback_draft.get("content") or "").strip():
                await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")
                return {
                    "success": True,
                    "status": SessionStatus.WAITING_FEEDBACK,
                    "draft_v1": fallback_draft,
                    "iteration": self.iteration_count,
                    "proposals": fallback_proposals,
                }
            return await self._handle_error("Draft generation failed")

        await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")
        draft_text = draft.content if hasattr(draft, "content") else str(draft)
        proposals = await self._detect_proposals(project_id, draft_text)

        if self._needs_memory_pack_refresh(working_memory_payload):
            try:
                await self.ensure_memory_pack(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_goal=writer_context.get("chapter_goal") or "",
                    scene_brief=writer_context.get("scene_brief"),
                    user_feedback="",
                    force_refresh=True,
                    source="writer_post",
                )
            except Exception as exc:
                logger.warning("Post-write memory pack refresh failed: %s", exc)

        return {
            "success": True,
            "status": SessionStatus.WAITING_FEEDBACK,
            "draft_v1": draft,
            "iteration": self.iteration_count,
            "proposals": proposals,
        }

    def _needs_memory_pack_refresh(self, payload: Optional[Dict[str, Any]]) -> bool:
        if not payload:
            return True
        evidence_pack = payload.get("evidence_pack") or {}
        items = evidence_pack.get("items") or []
        stats = evidence_pack.get("stats") or {}
        total = stats.get("total")
        if isinstance(total, int) and total > 0:
            return False
        if items:
            return False
        working_memory = payload.get("working_memory")
        if isinstance(working_memory, str) and working_memory.strip():
            return False
        return True

    async def _run_research_loop(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Optional[SceneBrief],
        user_answers: Optional[List[Dict[str, Any]]] = None,
        offline: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        执行多轮研究循环（带提前停止） / Run multi-round research loop (max rounds with early stop).

        研究循环工作流 / Research loop workflow:
        1. 提取初始缺口 / Extract initial knowledge gaps
        2. 生成研究计划 / Generate research plan
        3. 执行检索 / Execute retrieval
        4. 评估充分性 / Assess evidence sufficiency
        5. 停止条件：证据充分、达到最大轮次、或无法产生新查询

        Stop conditions:
        - 'sufficient' - 证据充分 / Evidence is sufficient
        - 'max_rounds' - 达到最大轮次 / Maximum rounds reached
        - 'no_queries' - 无法产生查询 / Cannot generate queries
        - 'offline_stop' - 离线模式停止 / Offline mode stop
        - 'empty_payload' - 检索无结果 / Retrieval returned empty

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            chapter_goal: 章节目标 / Chapter goal.
            scene_brief: 场景简要 / Scene brief (optional).
            user_answers: 用户答题 / User answers (optional).
            offline: 离线模式 / Offline mode flag.

        Returns:
            研究载荷 / Research payload with evidence and questions, or None if failed.
        """
        try:
            from app.services.working_memory_service import working_memory_service
        except Exception as exc:
            logger.warning("Failed to import working_memory_service: %s", exc)
            return None

        research_trace: List[Dict[str, Any]] = []
        extra_queries: List[str] = []
        working_payload: Optional[Dict[str, Any]] = None
        stop_reason = "unknown"

        await self._emit_progress(self._p("正在阅读前文...", "Reading prior text..."), stage="read_previous", round=0)
        await self._emit_progress(self._p("正在阅读相关事实摘要...", "Fetching facts..."), stage="read_facts", round=0)
        character_names = self._extract_scene_brief_names(scene_brief, limit=3)
        instruction_entities = await chapter_binding_service.extract_entities_from_text(project_id, chapter_goal)
        instruction_characters = instruction_entities.get("characters") or []
        loose_mentions = chapter_binding_service.extract_loose_mentions(chapter_goal, limit=6)

        mention_candidates: List[str] = []
        for name in instruction_characters + character_names + loose_mentions:
            if name and name not in mention_candidates:
                mention_candidates.append(name)

        # Pre-check which mentioned entities have existing cards. This list is used as
        # retrieval seeds (to improve recall), but UI should display the *actual*
        # cards hit from evidence_pack/card_snapshot later.
        card_hits: List[str] = []
        missing_cards: List[str] = []
        for name in mention_candidates[:12]:
            resolved = None
            try:
                resolved = await self.card_storage.get_character_card(project_id, name)
            except Exception:
                resolved = None
            if resolved:
                card_hits.append(name)
                continue
            try:
                resolved = await self.card_storage.get_world_card(project_id, name)
            except Exception:
                resolved = None
            if resolved:
                card_hits.append(name)
                continue
            missing_cards.append(name)

        try:
            initial_gaps = working_memory_service.build_gap_items(scene_brief, chapter_goal, language=self.language)
            if offline:
                plan_queries: List[str] = []
                for gap in initial_gaps or []:
                    for query in gap.get("queries") or []:
                        if query:
                            plan_queries.append(str(query).strip())
                extra_queries = list(dict.fromkeys([q for q in plan_queries if q]))[:4]
                if extra_queries:
                    await self._emit_progress(
                        self._p("研究计划已生成（离线）", "Research plan generated (offline)"),
                        stage="generate_plan",
                        round=1,
                        queries=extra_queries,
                        note="offline_from_gaps",
                    )
            else:
                plan = await self.writer.generate_research_plan(
                    chapter_goal=chapter_goal,
                    unresolved_gaps=initial_gaps,
                    evidence_stats={},
                    round_index=1,
                )
                extra_queries = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()]
                if extra_queries:
                    await self._emit_progress(
                        self._p("研究计划已生成", "Research plan generated"),
                        stage="generate_plan",
                        round=1,
                        queries=extra_queries,
                        note=str(plan.get("note") or ""),
                    )
        except Exception as exc:
            logger.warning("Initial research plan failed: %s", exc)

        for round_index in range(1, self.max_research_rounds + 1):
            await self._emit_progress(
                self._p(f"正在思考...（第{round_index}轮）", f"Preparing retrieval... (Round {round_index})"),
                stage="prepare_retrieval",
                round=round_index,
                note=self._p("整理缺口并准备检索", "Organizing gaps and preparing retrieval"),
            )

            merged_extra_queries = extra_queries
            retrieval_seeds = [q for q in (card_hits + missing_cards) if str(q or "").strip()]
            if retrieval_seeds:
                merged_extra_queries = list(
                    dict.fromkeys([q for q in (extra_queries + retrieval_seeds) if str(q or "").strip()])
                )[:8]

            payload = await working_memory_service.prepare(
                project_id=project_id,
                chapter=chapter,
                scene_brief=scene_brief,
                chapter_goal=chapter_goal,
                user_answers=user_answers,
                extra_queries=merged_extra_queries,
                force_minimum_questions=False,
                semantic_rerank=False if offline else None,
                round_index=round_index,
                language=self.language,
            )
            if not payload:
                stop_reason = "empty_payload"
                break

            if round_index == 1:
                snapshot = await self._build_card_snapshot(project_id, payload)
                hit_characters = [
                    str(item.get("name") or "").strip()
                    for item in (snapshot.get("characters") or [])
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                ]
                hit_world = [
                    str(item.get("name") or "").strip()
                    for item in (snapshot.get("world") or [])
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                ]
                hit_cards = list(dict.fromkeys((hit_characters + hit_world)))[:5]
                if hit_cards:
                    card_message = self._p(
                        "正在查询设定“" + "”“".join(hit_cards) + "”",
                        "Looking up cards: " + ", ".join(hit_cards),
                    )
                else:
                    card_message = self._p("正在查询相关设定...", "Looking up cards...")

                await self._emit_progress(
                    card_message,
                    stage="lookup_cards",
                    round=0,
                    queries=hit_cards,
                    payload={
                        "hit_characters": hit_characters[:10],
                        "hit_world": hit_world[:10],
                        "seed_entities": payload.get("seed_entities") or [],
                        "source": "card_snapshot",
                    },
                )

            retrieval_requests = payload.get("retrieval_requests") or []
            for req in retrieval_requests:
                req["round"] = round_index

            evidence_pack = payload.get("evidence_pack") or {}
            evidence_groups = evidence_pack.get("groups") or []
            stats = evidence_pack.get("stats") or {}
            queries = []
            hits = 0
            for req in retrieval_requests:
                for query in req.get("queries") or []:
                    if query:
                        queries.append(query)
                if not req.get("skipped"):
                    hits += int(req.get("count") or 0)
            queries = list(dict.fromkeys(queries))
            top_sources = self._extract_top_sources(evidence_groups, limit=3)
            await self._emit_progress(
                self._p(f"正在检索...（第{round_index}轮）", f"Executing retrieval... (Round {round_index})"),
                stage="execute_retrieval",
                round=round_index,
                queries=queries,
                hits=hits,
                top_sources=top_sources,
                note=self._p("已完成检索，正在整理证据", "Retrieval completed; organizing evidence"),
            )

            research_trace.append(
                {
                    "round": round_index,
                    "queries": stats.get("queries") or queries,
                    "types": stats.get("types") or {},
                    "count": stats.get("total", len(evidence_pack.get("items") or [])),
                    "hits": hits,
                    "top_sources": top_sources,
                    "extra_queries": extra_queries,
                }
            )

            working_payload = payload
            report = payload.get("sufficiency_report") or {}
            if report.get("sufficient") is True:
                stop_reason = "sufficient"
                await self._emit_progress(
                    self._p("证据判定：充分，准备结束研究", "Evidence check: sufficient; preparing to finish research"),
                    stage="self_check",
                    round=round_index,
                    stop_reason=stop_reason,
                    note=self._p("证据充分，提前结束研究", "Sufficient evidence; ending research early"),
                )
                break

            if round_index >= self.max_research_rounds:
                stop_reason = "max_rounds"
                await self._emit_progress(
                    self._p("证据仍不足，已到最大轮次", "Evidence still insufficient; reached max rounds"),
                    stage="self_check",
                    round=round_index,
                    stop_reason=stop_reason,
                    note=self._p(
                        "达到最大轮次，进入反问或待确认", "Max rounds reached; entering questions/confirmation"
                    ),
                )
                break

            await self._emit_progress(
                self._p("证据不足，继续检索", "Evidence insufficient; continuing retrieval"),
                stage="self_check",
                round=round_index,
                note=self._p("证据不足，进入下一轮", "Insufficient evidence; moving to next round"),
            )

            if offline:
                stop_reason = "offline_stop"
                await self._emit_progress(
                    self._p("离线模式：停止继续规划检索", "Offline mode: stop planning further retrieval"),
                    stage="self_check",
                    round=round_index,
                    stop_reason=stop_reason,
                    note=self._p("离线评测仅执行第1轮检索", "Offline evaluation runs only the first retrieval round"),
                )
                break

            plan = await self.writer.generate_research_plan(
                chapter_goal=chapter_goal,
                unresolved_gaps=payload.get("unresolved_gaps") or [],
                evidence_stats=stats,
                round_index=round_index + 1,
            )
            extra_queries = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()]
            if not extra_queries:
                stop_reason = "no_queries"
                await self._emit_progress(
                    self._p("研究计划为空，停止检索", "Research plan empty; stopping retrieval"),
                    stage="self_check",
                    round=round_index,
                    stop_reason=stop_reason,
                    note=self._p("缺口无法转化为有效检索", "Gaps cannot be converted into effective retrieval queries"),
                )
                break
            await self._emit_progress(
                self._p("研究计划已生成", "Research plan generated"),
                stage="generate_plan",
                round=round_index + 1,
                queries=extra_queries,
                note=str(plan.get("note") or ""),
            )

        if working_payload is None:
            return None

        report = working_payload.get("sufficiency_report") or {}
        needs_user_input = bool(report.get("needs_user_input"))
        if stop_reason != "max_rounds" or not needs_user_input:
            working_payload["questions"] = []

        if research_trace and stop_reason:
            stop_note = ""
            if stop_reason == "sufficient":
                stop_note = self._p("证据充分，提前结束研究", "Sufficient evidence; ending research early")
            elif stop_reason == "max_rounds":
                stop_note = self._p(
                    "达到最大轮次，进入反问或待确认", "Max rounds reached; entering questions/confirmation"
                )
            elif stop_reason == "no_queries":
                stop_note = self._p(
                    "无法生成有效检索，停止研究", "Cannot generate effective retrieval queries; stopping research"
                )
            else:
                stop_note = self._p("研究流程提前停止", "Research flow stopped early")
            research_trace[-1]["stop_reason"] = stop_reason
            research_trace[-1]["note"] = stop_note

        working_payload["research_trace"] = research_trace
        working_payload["research_stop_reason"] = stop_reason
        return working_payload

    async def _stream_writer_output(
        self,
        project_id: str,
        chapter: str,
        writer_payload: Dict[str, Any],
        working_memory_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        流式处理撰稿人输出并持久化最终草稿 / Stream writer output to client while persisting the final draft.

        处理步骤 / Processing steps:
        1. 发送流开始事件 / Send stream start event
        2. 逐token接收和转发撰稿人输出 / Receive and forward tokens from writer
        3. 收集完整文本 / Collect complete text
        4. 检测设定建议 / Detect proposals from content
        5. 保存为版本1草稿 / Save as v1 draft
        6. 发送流结束事件 / Send stream end event

        错误处理 / Error handling:
        - 空内容错误 / Empty content raises RuntimeError
        - 被取消的流任务正常退出 / Cancelled streams exit gracefully
        - 进度回调异常被捕获不阻断流程 / Callback exceptions don't block streaming

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            writer_payload: 撰稿人载荷 / Writer context payload.
            working_memory_payload: 工作记忆 / Optional working memory for tracking.

        Raises:
            RuntimeError: 如果最终文本为空 / If final text is empty.
        """
        await self._emit_progress(self._p("正在撰写...", "Writing..."), stage="writing", status="writing")
        if self.progress_callback:
            await self.progress_callback(
                {
                    "type": "stream_start",
                    "project_id": project_id,
                    "chapter": chapter,
                }
            )

        chunks: List[str] = []

        # Phase 5：草稿生成途中的真实流式 thinking。默认开（config.retrieval.stream_thinking=true，
        # 缺省视为 True，与 config.yaml 一致）。能力门控：仅推理模型返回 reasoning_content 时才触发，
        # 非推理模型 / 无 progress_callback 自动 no-op，行为与历史一致。
        on_thinking = None
        try:
            from app.config import config as _cfg

            if _cfg.get("retrieval", {}).get("stream_thinking", True) and self.progress_callback:

                async def on_thinking(delta: str) -> None:
                    try:
                        await self.progress_callback(
                            {
                                "type": "agent_thinking",
                                "project_id": project_id,
                                "chapter": chapter,
                                "content": str(delta)[:2000],
                            }
                        )
                    except Exception:
                        pass

        except Exception:
            on_thinking = None

        async for chunk in self.writer.execute_stream_draft(
            project_id=project_id,
            chapter=chapter,
            context=writer_payload,
            on_thinking=on_thinking,
        ):
            if not chunk:
                continue
            if self._cancelled:
                break
            chunks.append(chunk)
            if self.progress_callback:
                await self.progress_callback(
                    {
                        "type": "token",
                        "project_id": project_id,
                        "chapter": chapter,
                        "content": chunk,
                    }
                )

        # 用户取消时静默退出，不保存草稿
        # Exit silently on user cancel without saving draft
        if self._cancelled:
            return

        final_text = normalize_prose_paragraphs("".join(chunks), language=self.language).strip()
        if not final_text:
            raise RuntimeError("Empty draft result")

        pending_confirmations = []
        draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version="v1",
            content=final_text,
            word_count=len(final_text),
            pending_confirmations=pending_confirmations,
        )

        proposals = await self._detect_proposals(project_id, final_text)

        await self._persist_research_trace_memory(
            project_id=project_id,
            chapter=chapter,
            working_memory_payload=working_memory_payload,
        )

        if self.progress_callback:
            try:
                draft_payload = draft.model_dump(mode="json")
            except Exception:
                draft_payload = {
                    "chapter": getattr(draft, "chapter", chapter),
                    "version": getattr(draft, "version", "v1"),
                    "content": getattr(draft, "content", final_text),
                    "word_count": getattr(draft, "word_count", len(final_text)),
                }
            self._last_stream_results[str(chapter)] = {
                "draft": draft_payload,
                "proposals": proposals,
                "timestamp": int(time.time() * 1000),
            }
            await self.progress_callback(
                {
                    "type": "stream_end",
                    "project_id": project_id,
                    "chapter": chapter,
                    "draft": draft_payload,
                    "proposals": proposals,
                }
            )

    async def _update_status(self, status: SessionStatus, message: str) -> None:
        """Update session status and notify callback."""
        self.current_status = status

        if self.progress_callback:
            await self.progress_callback(
                {
                    "status": status.value,
                    "message": message,
                    "project_id": self.current_project_id,
                    "chapter": self.current_chapter,
                    "iteration": self.iteration_count,
                }
            )

    async def _handle_error(self, error_message: str, *, exc: Exception | None = None) -> Dict[str, Any]:
        """Handle error and update status."""
        # Enrich message with LLM provider details if available
        if exc is not None and isinstance(exc, LLMError):
            error_message = f"{error_message} [provider={exc.provider}, reason={exc.reason}]"

        self.current_status = SessionStatus.ERROR

        if self.progress_callback:
            await self.progress_callback(
                {
                    "status": SessionStatus.ERROR.value,
                    "message": error_message,
                    "project_id": self.current_project_id,
                    "chapter": self.current_chapter,
                }
            )

        return {"success": False, "status": SessionStatus.ERROR, "error": error_message}

    async def _handle_cancelled(self) -> Dict[str, Any]:
        """Handle user-initiated cancellation: reset state and broadcast cancel event."""
        logger.info("Session cancelled by user: project=%s chapter=%s", self.current_project_id, self.current_chapter)
        self.current_status = SessionStatus.IDLE
        chapter = self.current_chapter
        self.current_project_id = None
        self.current_chapter = None

        if self.progress_callback:
            await self.progress_callback(
                {
                    "type": "cancelled",
                    "status": SessionStatus.IDLE.value,
                    "message": "Session cancelled by user",
                    "project_id": self.current_project_id,
                    "chapter": chapter,
                }
            )

        return {"success": False, "status": SessionStatus.IDLE, "cancelled": True}

    def get_status(self) -> Dict[str, Any]:
        """Get current session status."""
        return {
            "status": self.current_status.value,
            "project_id": self.current_project_id,
            "chapter": self.current_chapter,
            "iteration": self.iteration_count,
        }

    async def decide_writing_action(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool = False,
        has_draft: bool = False,
        emit: bool = True,
    ) -> Dict[str, Any]:
        """Phase 5（vibe writing）：判定本轮 chat 意图（write/edit）并发透明 intent 事件。

        让前端只需一个输入框：撰写/编辑由此处自判（选中→编辑、无草稿→撰写、含糊→LLM 判定）。
        返回 ``{action, scope, reason, via}``，调用方据此路由到既有 Writer/Editor 能力。
        """
        from app.agents.intent import classify_writing_intent

        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        decision = await classify_writing_intent(
            message,
            has_selection=has_selection,
            has_draft=has_draft,
            gateway=self.gateway,
            provider=provider,
        )
        if emit and self.progress_callback:
            await self.progress_callback({"type": "intent", "project_id": project_id, "chapter": chapter, **decision})
        return decision

    async def create_plan(self, project_id: str, goal: str, context_hint: str = "") -> Optional[Dict[str, Any]]:
        """Phase 11：把复杂指令拆成串行 plan 并持久化；无可执行步骤则返回 None（调用方回退普通 write/edit）。"""
        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        try:
            existing_chapters = await self.draft_storage.list_chapters(project_id)
        except Exception:
            existing_chapters = []
        steps = await generate_plan(self.gateway, provider, goal, context_hint, chapters=existing_chapters)
        if not steps:
            return None
        plan = {
            "id": f"plan_{int(time.time() * 1000)}",
            "goal": str(goal or "").strip(),
            "steps": steps,
            "status": "planning",
            "created_at": int(time.time() * 1000),
        }
        await self.plan_store.write_plan(project_id, plan)
        await self._emit_progress(
            self._p("已生成执行计划", "Plan generated"),
            stage="plan_created",
            status="plan",
            plan_id=plan["id"],
            steps=len(steps),
        )
        return plan

    async def execute_plan(self, project_id: str, plan_id: str, step_runner=None) -> Dict[str, Any]:
        """Phase 11：串行执行 plan（红线 1：正文主线单线程）。

        每步持久化进度（断点续传）、发透明事件、响应用户打断（self._cancelled）。
        step_runner 可注入（默认 self._run_plan_step）以便单测编排逻辑。
        """
        plan = await self.plan_store.read_plan(project_id, plan_id)
        if not plan:
            return {"success": False, "error": "plan_not_found"}
        runner = step_runner or self._run_plan_step
        steps = plan.get("steps") or []
        plan["status"] = "running"
        await self.plan_store.write_plan(project_id, plan)

        completed = True
        for step in steps:
            if self._cancelled:
                completed = False
                break
            if step.get("status") == "done":
                continue  # 断点续传：跳过已完成步骤
            await self._emit_progress(
                self._p(
                    f"计划步骤 {step.get('id')}/{len(steps)}：{step.get('description', '')}",
                    f"Plan step {step.get('id')}/{len(steps)}: {step.get('description', '')}",
                ),
                stage="plan_step",
                status="plan",
                step_id=step.get("id"),
                action=step.get("action"),
            )
            try:
                result = await runner(project_id, step)
                step["status"] = "done"
                if result:
                    step["result"] = str(result)[:500]
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = str(exc)
                logger.warning("Plan step %s failed: %s", step.get("id"), exc)
                completed = False
                await self.plan_store.write_plan(project_id, plan)
                break  # 失败即停：不在坏状态上盲跑后续（可能依赖前序）步骤
            await self.plan_store.write_plan(project_id, plan)  # 每步持久化进度

        if self._cancelled:
            plan["status"] = "interrupted"
        elif completed:
            plan["status"] = "done"
        else:
            plan["status"] = "failed"
        await self.plan_store.write_plan(project_id, plan)
        return {"success": completed, "plan": plan}

    async def _run_plan_step(self, project_id: str, step: Dict[str, Any]) -> str:
        """把单个 plan 步骤分发到既有能力。

        - research：做真实 canon 检索并返回要点（不再空操作）。
        - edit/analyze：先校验章节存在（grounding 兜底，避免在捏造章节上执行）。
        - 注：plan 是「自主批处理」→ 直接落稿（start_session / process_feedback），而非交互式
          agent+diff 提议（run_writing_agent）——批量自主与单轮人审语境不同，刻意区分。
        """
        action = str(step.get("action") or "").strip()
        chapter = str(step.get("chapter") or "").strip()
        description = str(step.get("description") or "").strip()

        if action == "research":
            return f"research: {await self._plan_research_note(project_id, description)}"

        if action in ("edit", "analyze") and chapter:
            try:
                existing = await self.draft_storage.list_chapters(project_id)
            except Exception:
                existing = []
            if existing and chapter not in existing:
                raise ValueError(f"章节 {chapter} 不存在，无法 {action}")

        if action == "write" and chapter:
            res = await self.start_session(
                project_id, chapter, step.get("title") or chapter, description, skip_questions=True
            )
            return f"write {chapter}: {res.get('status')}"
        if action == "edit" and chapter:
            res = await self.process_feedback(project_id, chapter, description, action="revise")
            return f"edit {chapter}: {res.get('status')}"
        if action == "analyze" and chapter:
            res = await self.analyze_chapter(project_id, chapter)
            return f"analyze {chapter}: {res.get('success')}"
        return f"{action}: {description[:80]}"

    async def _plan_research_note(self, project_id: str, query: str) -> str:
        """plan 的 research 步骤：真实检索 canon，返回要点摘要（供透明展示，非空操作）。"""
        query = str(query or "").strip()
        if not query:
            return "（空查询）"
        try:
            items = (
                await self.select_engine.retrieval_select(
                    project_id=project_id,
                    query=query,
                    item_types=["fact", "character", "world"],
                    storage=self.storage_adapter,
                    top_k=5,
                )
                or []
            )
        except Exception as exc:
            logger.warning("plan research retrieval failed: %s", exc)
            return f"检索失败：{exc}"
        parts = []
        for it in items[:5]:
            content = str(getattr(it, "content", "") or "").strip()
            if content:
                parts.append(content[:120])
        return "查到：" + "；".join(parts) if parts else "未检索到相关已确立设定/事实"

    async def run_writing_agent(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool = False,
        has_draft: bool = False,
        thinking: bool = False,
        target_word_count: int = 3000,
    ) -> Dict[str, Any]:
        """阶段 C：agent 写作主循环（对齐 AI coding 的 Write/Edit）。

        agent 自主用 write_content / edit_lines（+ 检索工具）操作正文工作副本——不区分写/改，
        由 agent 看着当前正文自主选择。完成后 working_text 经 WS 模拟流式推送（stream_start/token/
        stream_end），前端据此走流式 diff 审阅采纳（finalizeDraftAsDiff）。

        Returns:
            {success, action, changed, ...}；provider 不支持工具 / agent 未动笔 → {fallback: True}，
            调用方据此回退既有预判路由（能力降级，红线 4）。
        """
        from app.agents.tools import WriterToolset
        from app.agents.writing_actions import WritingActionToolset
        from app.agents.agentic import run_agentic_chat
        from app.config import config as _cfg

        # 当前正文：diff 基线 + 工作副本
        current_text = ""
        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            if versions:
                latest_draft = await self.draft_storage.get_draft(project_id, chapter, versions[-1])
                current_text = str(getattr(latest_draft, "content", "") or "")
        except Exception:
            current_text = ""

        retrieval = WriterToolset(
            project_id,
            self.storage_adapter,
            self.select_engine,
            current_chapter=chapter,
        )
        writing_ts = WritingActionToolset(current_text, retrieval_toolset=retrieval)

        system = self._build_writing_agent_system(
            has_draft=bool(current_text.strip()), target_word_count=target_word_count
        )
        user = self._build_writing_agent_user(
            message, chapter, current_text, has_selection, target_word_count=target_word_count
        )

        tools_used = {"any": False}
        progress_cb = self.progress_callback

        async def _on_event(ev: Dict[str, Any]) -> None:
            etype = ev.get("type")
            if etype == "tool_call":
                tools_used["any"] = True
            if not progress_cb:
                return
            try:
                if etype == "thinking":
                    await progress_cb(
                        {
                            "type": "agent_thinking",
                            "project_id": project_id,
                            "chapter": chapter,
                            "content": str(ev.get("content") or "")[:2000],
                        }
                    )
                elif etype == "tool_call":
                    await progress_cb(
                        {
                            "type": "agent_tool_call",
                            "project_id": project_id,
                            "chapter": chapter,
                            "name": ev.get("name"),
                            "arguments": ev.get("arguments"),
                        }
                    )
                elif etype == "tool_result":
                    await progress_cb(
                        {
                            "type": "agent_tool_result",
                            "project_id": project_id,
                            "chapter": chapter,
                            "name": ev.get("name"),
                            "result": str(ev.get("result") or "")[:1000],
                        }
                    )
            except Exception:
                pass

        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        if not provider:
            return {"success": False, "fallback": True, "reason": "no_provider"}

        thinking_param = None
        if thinking:
            _builder = getattr(self.gateway, "thinking_param_for_agent", None)
            if callable(_builder):
                thinking_param = _builder(self.writer.get_agent_name(), thinking)
        # 写整章需要较长输出：按目标字数放宽 max_tokens（中文约 1.6~2 token/字，留足余量）。
        gen_max_tokens = max(4096, int(target_word_count * 2.0))
        max_iters = int(_cfg.get("retrieval", {}).get("agentic_max_iterations", 4)) + 2
        try:
            resp = await run_agentic_chat(
                self.gateway,
                provider,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                writing_ts,
                temperature=0.7,
                max_tokens=gen_max_tokens,
                max_iterations=max_iters,
                on_event=_on_event,
                thinking=thinking_param,
            )
        except Exception as exc:
            logger.warning("run_writing_agent failed; will fallback to routed path: %s", exc)
            return {"success": False, "fallback": True, "reason": f"agent_error:{exc}"}

        if not writing_ts.changed:
            # agent 没动笔：完全没调工具（很可能 provider 不支持工具）→ 回退预判路由；
            # 调了工具但只检索没写 → 作为纯回复返回（不改正文）。
            if not tools_used["any"]:
                return {"success": False, "fallback": True, "reason": "no_tool_calls"}
            return {"success": True, "action": "reply", "changed": False, "message": str(resp.get("content") or "")}

        final_text = writing_ts.working_text
        proposals = await self._stream_text_as_diff(project_id, chapter, final_text)
        return {
            "success": True,
            "action": "agentic_write",
            "changed": True,
            "proposals": proposals,
            "actions": writing_ts.actions,
            "summary": str(resp.get("content") or "").strip(),
        }

    def _build_writing_agent_system(self, *, has_draft: bool, target_word_count: int = 3000) -> str:
        """写作 agent 的 system prompt：把写/改统一为两个工具，先检索后落笔（对齐 AI coding）。"""
        lang = "中文" if self.language == "zh" else "英文"
        base = (
            "你是小说撰稿 agent，工作方式与 AI 编程助手一致：先用检索工具核对设定，再用写作工具落笔。"
            "『写新内容』与『改旧文』不是两件事，而是你的两个工具，由你看着当前正文自主选择：\n"
            "- write_content(content[, mode]): 写入整章/整段正文（mode=replace 覆盖 / append 续写），"
            "content 是你直接创作的小说正文本身。\n"
            "- edit_lines(old_text, new_text): 精确替换正文中唯一出现的一处片段，用于局部修改/润色/删减"
            "（old_text 须与正文逐字一致且唯一）。\n"
            "检索工具（lookup_card/query_canon/query_relations/read_chapter/search_prose）供你按需"
            "核对人物设定、关系、伏笔与已确立事实，避免前后矛盾。\n\n"
            "工作原则：\n"
            "1) 先检索后落笔：动笔前查全相关设定与事实，时间近的章节更相关。\n"
            "2) 选对工具：正文为空或需大段新内容 → write_content；只改局部 → edit_lines。\n"
            "3) content 只含小说正文，不夹带解释、标题或标记。\n"
            "4) 完成后用一句话说明你改了什么/为何，不要复述正文。\n"
        )
        if has_draft:
            base += (
                "本章已有正文（见用户消息）。这是『编辑』场景：优先用 edit_lines 做针对性的局部修改/润色"
                "（按需改写，不必长篇）；仅当用户明确要求重写整章时才 write_content(replace)。\n"
            )
        else:
            base += (
                f"本章暂无正文。这是『撰写整章』场景：必须用 write_content **一次写出完整的一整章**，"
                f"目标约 {target_word_count} 字（不少于 {int(target_word_count * 0.8)} 字），要有起承转合、"
                "场景与对白充分展开，写到本章自然收束——绝不能只写开头、提纲或片段就停。\n"
            )
        base += f"\n请用{lang}创作。"
        return base

    def _build_writing_agent_user(
        self, message: str, chapter: str, current_text: str, has_selection: bool, target_word_count: int = 3000
    ) -> str:
        """写作 agent 的 user 消息：注入当前正文（供 edit_lines 定位）+ 用户指令。"""
        parts = [f"本章 ID：{chapter}"]
        body = str(current_text or "")
        if body.strip():
            if len(body) > 6000:
                body = body[:3500] + "\n…（中段省略，如需修改中段请用检索或要求重写）…\n" + body[-2000:]
            parts.append(f"【当前正文】\n{body}")
        else:
            parts.append("【当前正文】（空）")
        if has_selection:
            parts.append("（用户在编辑器中有选中片段，优先聚焦该处修改。）")
        parts.append(f"\n用户指令：{str(message or '').strip()}")
        if not body.strip():
            parts.append(
                f"请先检索必要设定，再用 write_content 写出**完整一整章**（目标约 {target_word_count} 字，写足写完，勿只开头）。"
            )
        else:
            parts.append("请先检索必要设定，再用写作工具完成本轮。")
        return "\n".join(parts)

    async def _stream_text_as_diff(self, project_id: str, chapter: str, final_text: str) -> List[Dict[str, Any]]:
        """把既成正文经 WS 模拟流式推送（stream_start/token/stream_end），前端据此走流式 diff 审阅。

        与 _stream_writer_output 同构，但内容是 agent 工作副本（既成文本），故不重新流式生成、不预存草稿
        （提议态，采纳后由前端落地——红线 2：真相在文件、对话可弃）。返回设定建议（proposals）。
        """
        cb = self.progress_callback
        text = str(final_text or "")
        if cb:
            await cb({"type": "stream_start", "project_id": project_id, "chapter": chapter})
            for chunk in self._chunk_for_stream(text):
                if self._cancelled:
                    break
                await cb({"type": "token", "project_id": project_id, "chapter": chapter, "content": chunk})
        proposals: List[Dict[str, Any]] = []
        if text and not self._cancelled:
            try:
                proposals = await self._detect_proposals(project_id, text)
            except Exception:
                proposals = []
        if cb:
            await cb(
                {
                    "type": "stream_end",
                    "project_id": project_id,
                    "chapter": chapter,
                    "draft": {"chapter": chapter, "version": "v1", "content": text, "word_count": len(text)},
                    "proposals": proposals,
                }
            )
        return proposals

    @staticmethod
    def _chunk_for_stream(text: str, size: int = 24) -> List[str]:
        """把文本切成固定长度小块，用于 WS 模拟流式推送（前端 StreamingDiffView 逐块显示）。"""
        text = str(text or "")
        return [text[i : i + size] for i in range(0, len(text), size)]

    # ---------------------------------------------------------------- 对话记忆层 --
    # 持久化对话历史（Git-Native）+ compact 长对话压缩 + 顺带提炼作者偏好 → creative_memory。
    # 取代脆弱的前端 localStorage 单点：刷新/重启/清缓存/换机均不丢，且可 Git 追踪。

    async def append_conversation(self, project_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """追加一条对话消息到持久存储（sessions/conversation.jsonl）。"""
        return await self.session_history.append(project_id, message)

    async def load_conversation(self, project_id: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        """读取持久化的对话历史（limit>0 只取最近 N 条）。"""
        return await self.session_history.load(project_id, limit=limit)

    async def _summarize_conversation(self, text: str) -> str:
        """把早期对话压成简明要点：优先 LLM 摘要，失败降级规则压缩（smart_compress）。"""
        text = str(text or "").strip()
        if not text:
            return ""
        try:
            provider = self.gateway.get_provider_for_agent(self.archivist.get_agent_name())
            system = self._p(
                "你是对话摘要助手。把下面早期的人机创作对话压成简明要点，保留：作者的关键指令/偏好、"
                "已做的写作决策、进行到哪。中文，≤200 字，分条，不要逐句复述。",
                "You summarize an earlier human-AI writing conversation into concise bullets: keep the author's "
                "key instructions/preferences, decisions made, and progress. ≤200 words, bulleted.",
            )
            resp = await self.gateway.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": text[:6000]}],
                provider=provider,
                temperature=0.3,
            )
            out = str(resp.get("content") or "").strip()
            if out:
                return out
        except Exception as exc:
            logger.warning("conversation summary via LLM failed; falling back to rule-based: %s", exc)
        try:
            from app.context_engine.smart_compressor import smart_compress

            compressed, _ = smart_compress(text, target_ratio=0.35)
            return str(compressed or "").strip() or text[:600]
        except Exception:
            return text[:600]

    async def compact_conversation(
        self, project_id: str, *, keep_recent: int = 40, trigger_at: int = 120
    ) -> Dict[str, Any]:
        """长对话压缩 + 顺带从被压缩对话提炼作者偏好 → creative_memory（best-effort，不阻断）。

        summarizer 注入 SessionHistoryStorage.compact：把早期对话压成一条 summary；同时把其中用户消息
        当作"作者反馈"喂给 Archivist 提取偏好/进度/决策，Upsert 到创作记忆（与 canon 边界清晰）。
        """
        extracted: List[str] = []

        async def _summarizer(old_messages: List[Dict[str, Any]]) -> str:
            convo = "\n".join(
                f"{m.get('role', 'user')}: {str(m.get('content') or '').strip()}"
                for m in old_messages
                if str(m.get("content") or "").strip()
            )
            summary = await self._summarize_conversation(convo)
            try:
                user_text = "\n".join(
                    str(m.get("content") or "").strip()
                    for m in old_messages
                    if m.get("role") == "user" and str(m.get("content") or "").strip()
                )
                if user_text:
                    mems = await self.archivist.extract_creative_memory(final_draft="", user_feedback=user_text)
                    for mem in mems:
                        slug = await self.creative_memory_storage.write_memory(
                            project_id,
                            mem["slug"],
                            mem["description"],
                            mem.get("body", ""),
                            mem.get("type", "preference"),
                        )
                        extracted.append(slug)
            except Exception as exc:
                logger.warning("preference extraction during compact failed: %s", exc)
            return summary

        result = await self.session_history.compact(
            project_id, _summarizer, keep_recent=keep_recent, trigger_at=trigger_at
        )
        result["preferences_extracted"] = extracted
        return result

    async def run_chat_turn(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool = False,
        has_draft: bool = False,
        target_word_count: int = 3000,
        auto_execute_plan: bool = False,
        thinking: bool = False,
    ) -> Dict[str, Any]:
        """Phase 12 · 单 Writer 主循环的统一对话入口：意图自判 → 路由到既有能力。

        一个输入框、一个对话回合：作者发一句话，AI 自判 write/edit/continue/plan 并执行。
        5 阶段写作能力保留为被路由调用的 human-in-the-loop 检查点（不删——作者可随时打断/反问，
        与 G-3 教训一致）。这是『意图路由式主循环』，非 agentic-loop 重写 5 阶段。
        """
        decision = await self.decide_writing_action(
            project_id, chapter, message, has_selection=has_selection, has_draft=has_draft
        )
        action = str(decision.get("action") or "write")

        if action == "plan":
            plan = await self.create_plan(project_id, goal=message)
            if plan:
                result: Dict[str, Any] = {"success": True, "status": "plan_ready", "plan": plan}
                if auto_execute_plan:
                    result["execution"] = await self.execute_plan(project_id, plan["id"])
                return {
                    **result,
                    "action": "plan",
                    "decision": decision,
                    "route_contract": route_contract("plan", auto_execute_plan=auto_execute_plan),
                }
            action = "write"  # 拆不出可执行步骤 → 回退普通撰写

        # 阶段 C（对齐 AI coding，默认主路径）：写/改统一交给 agent 主循环自主用工具完成
        # （write_content / edit_lines + 检索），不再预判分流。
        if chapter:
            agent_result = await self.run_writing_agent(
                project_id,
                chapter,
                message,
                has_selection=has_selection,
                has_draft=has_draft,
                thinking=thinking,
                target_word_count=target_word_count,
            )
            if not agent_result.get("fallback"):
                result_action = str(agent_result.get("action") or action)
                return {
                    **agent_result,
                    "decision": decision,
                    "route_contract": route_contract(result_action),
                }
            logger.info("writing agent fell back: %s", agent_result.get("reason"))

        # 能力降级（红线 4）/ 无章节：返回 fallback 信号，由前端用成熟的 write/edit 流程兜底
        # （含 5 阶段反问、editor 精确 diff 等 human-in-the-loop 检查点，不在此单轮强行完成）。
        return {
            "success": True,
            "fallback": True,
            "action": action,
            "decision": decision,
            "route_contract": route_contract(action, fallback=True),
        }

    async def _emit_progress(self, message: str, **kwargs) -> None:
        if not self.progress_callback:
            return
        status = kwargs.pop("status", "research")
        payload = {
            "status": status,
            "message": message,
            "project_id": self.current_project_id,
            "chapter": self.current_chapter,
            "timestamp": int(time.time() * 1000),
        }
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        await self.progress_callback(payload)

    def _normalize_chapter_id(self, chapter_id: str) -> str:
        return normalize_chapter_id(chapter_id)

    def _trim_context_package(
        self,
        context_package: Dict[str, Any],
        max_tokens: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return trim_context_package(context_package, max_tokens)

    def _extract_scene_brief_names(self, scene_brief: Any, limit: int = 3) -> List[str]:
        return extract_scene_brief_names(scene_brief, limit=limit)

    def _extract_top_sources(self, evidence_groups: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
        return extract_top_sources(evidence_groups, limit=limit)

    def _build_context_debug(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return build_context_debug(payload)
