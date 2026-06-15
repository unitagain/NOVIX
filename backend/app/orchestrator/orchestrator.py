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
from app.storage import CardStorage, CanonStorage, DraftStorage, MemoryPackStorage
from app.agents import ArchivistAgent, WriterAgent, EditorAgent
from app.context_engine.select_engine import ContextSelectEngine
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
from app.orchestrator.orchestrator_helpers import (
    build_context_debug,
    estimate_context_tokens,
    extract_scene_brief_names,
    extract_top_sources,
    merge_card_description,
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
        self.select_engine = ContextSelectEngine()

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
            if questions and self.question_round < self.max_question_rounds:
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

    async def run_research_only(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        character_names: Optional[list] = None,
        user_answers: Optional[List[Dict[str, Any]]] = None,
        offline: bool = False,
    ) -> Dict[str, Any]:
        """
        仅运行档案员和研究循环，不生成草稿 / Run archivist + research loop only (no draft generation).

        用于回归评测和上下文分析：
        - 进度事件和跟踪可见性
        - 检索覆盖率和充分性检查
        - 提问触发规则

        Used for regression evaluation of agent behaviors:
        - progress events and trace visibility
        - retrieval coverage and sufficiency checks
        - question triggering rules

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            chapter_title: 章节标题 / Chapter title.
            chapter_goal: 章节目标 / Chapter goal.
            character_names: 核心角色列表 / Optional character names.
            user_answers: 用户预写答题 / Optional pre-writing answers.
            offline: 离线模式 / Offline mode (single research round).

        Returns:
            研究完成结果 / Research completion result.
        """
        self.current_project_id = project_id
        self.current_chapter = chapter
        self.iteration_count = 0
        self.question_round = 0

        try:
            scene_brief = None
            try:
                scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
            except Exception as exc:
                logger.warning("Failed to load scene_brief for %s:%s: %s", project_id, chapter, exc)
                scene_brief = None

            if not scene_brief and not offline:
                await self._update_status(SessionStatus.GENERATING_BRIEF, "Archivist is preparing the scene brief...")
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

            if not scene_brief:
                return await self._handle_error("Scene brief not available for offline research")

            working_memory_payload = await self._run_research_loop(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                user_answers=user_answers,
                offline=offline,
            )
            if working_memory_payload:
                await self._save_memory_pack(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_goal=chapter_goal,
                    scene_brief=scene_brief,
                    working_memory_payload=working_memory_payload,
                    source="research_only",
                )
            context_debug = self._build_context_debug(working_memory_payload)
            questions = (working_memory_payload or {}).get("questions") or []

            return {
                "success": True,
                "status": "research_completed",
                "scene_brief": scene_brief,
                "questions": questions,
                "context_debug": context_debug,
            }
        except Exception as exc:
            return await self._handle_error(f"Research only session error: {exc}", exc=exc)

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
        async for chunk in self.writer.execute_stream_draft(
            project_id=project_id,
            chapter=chapter,
            context=writer_payload,
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

    def _estimate_context_tokens(self, context_package: Dict[str, Any]) -> int:
        return estimate_context_tokens(context_package)

    def _trim_context_package(
        self,
        context_package: Dict[str, Any],
        max_tokens: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return trim_context_package(context_package, max_tokens)

    def _merge_card_description(self, description: str, rationale: str) -> str:
        return merge_card_description(description, rationale)

    def _extract_scene_brief_names(self, scene_brief: Any, limit: int = 3) -> List[str]:
        return extract_scene_brief_names(scene_brief, limit=limit)

    def _extract_top_sources(self, evidence_groups: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
        return extract_top_sources(evidence_groups, limit=limit)

    def _build_context_debug(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return build_context_debug(payload)
