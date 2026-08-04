# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Orchestrator 是单 Writer 写作、显式分析、计划与会话能力的应用门面。
"""

import asyncio
import json
import time
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Optional

from app.llm_gateway import get_gateway
from app.error_contract import error_envelope, safe_error_code
from app.storage import (
    CardStorage,
    CanonStorage,
    DraftStorage,
    MemoryPackStorage,
    CreativeMemoryStorage,
    PlanStore,
    SessionHistoryStorage,
)
from app.agents import ArchivistAgent, WriterAgent
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.embeddings import create_embeddings_backend
from app.context_engine.reranker import create_reranker_backend
from app.context_engine.turn_scope import bind_turn_scope, current_turn_scope, new_turn_scope
from app.orchestrator.storage_adapter import UnifiedStorageAdapter
from app.utils.language import normalize_language
from app.utils.logger import get_logger
from app.orchestrator.contracts import SessionStatus
from app.orchestrator._analysis_mixin import AnalysisMixin
from app.orchestrator.architecture import route_contract
from app.orchestrator.context_planning_service import ContextPlanningService
from app.orchestrator.context_assembly_service import ContextAssemblyService
from app.orchestrator.writing_service import WritingService
from app.orchestrator.turn_runtime import TurnState
from app.orchestrator.chat_turn_service import ChatTurnService
from app.orchestrator.post_turn_service import PostTurnService
from app.orchestrator.plan_execution_service import PlanExecutionService
from app.orchestrator.worker_task_service import WorkerTaskService
from app.orchestrator.application_ports import (
    AnalysisPort,
    CommandPort,
    ConversationPort,
    OrchestratorApplicationPorts,
    StreamTaskRegistry,
    VolumeSummaryService,
)

logger = get_logger(__name__)


class Orchestrator(AnalysisMixin):
    """
    单 Writer 写作运行时与显式辅助能力的应用门面。

    Attributes:
        card_storage (CardStorage): 角色/世界观卡片存储 / Character and world card storage.
        canon_storage (CanonStorage): 事实表和时间线存储 / Canon facts and timeline events storage.
        draft_storage (DraftStorage): 章节草稿和摘要存储 / Draft and summary storage.
        gateway (LLMGateway): LLM 调用网关 / Unified LLM gateway.
        archivist (ArchivistAgent): 仅供显式分析、摘要等辅助能力内部使用。
        writer (WriterAgent): 默认写作与编辑的唯一 Agent。
        select_engine (ContextSelectEngine): 上下文选择引擎 / Context selection engine.
        progress_callback (Optional[Callable]): 进度更新回调 / Callback for progress updates.
        current_status (SessionStatus): 当前会话状态 / Current session status.
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
        self.storage_adapter = UnifiedStorageAdapter(self.card_storage, self.canon_storage, self.draft_storage)
        # Phase 4: 注入嵌入后端（默认 config.retrieval.embeddings.enabled=true → 语义+词法融合）。
        # 初始化失败（缺库/缺模型）时工厂返回 None，检索自动降级为纯词法，绝不阻断写作主流程。
        try:
            from app.config import config as _app_config

            embeddings_backend = create_embeddings_backend(_app_config)
            reranker_backend = create_reranker_backend(_app_config)
        except Exception as exc:
            logger.warning("Retrieval model backend unavailable; affected capability disabled: %s", exc)
            embeddings_backend = None
            reranker_backend = None
        self.select_engine = ContextSelectEngine(
            embeddings_service=embeddings_backend,
            reranker_service=reranker_backend,
        )
        # Phase 7 降级可见：启动即明示语义检索配置状态（依赖缺失会在首次检索时降级，见 select_engine）。
        if embeddings_backend is not None:
            logger.info("语义检索：已配置启用（嵌入后端就绪；缺 fastembed/模型时首次检索降级为纯词法）。")
        else:
            logger.info("语义检索：纯词法（embeddings.enabled=false 或嵌入后端不可用）。")

        self.progress_callback = progress_callback
        self.current_status = SessionStatus.IDLE
        self.current_project_id: Optional[str] = None
        self.current_chapter: Optional[str] = None
        self.stream_tasks = StreamTaskRegistry()
        self._cancelled: bool = False  # 通用取消标志，用于在所有阶段响应用户取消 / General cancel flag
        self._active_turn_scopes: Dict[str, Any] = {}

        self.worker_task_service = WorkerTaskService()
        self.context_planning_service = ContextPlanningService(
            select_engine=self.select_engine,
            draft_storage=self.draft_storage,
            gateway=self.gateway,
        )
        self.context_assembly_service = ContextAssemblyService(language=self.language)
        self.writing_service = WritingService(
            gateway=self.gateway,
            writer=self.writer,
            draft_storage=self.draft_storage,
            storage_adapter=self.storage_adapter,
            select_engine=self.select_engine,
            context_assembly=self.context_assembly_service,
            progress_callback=self.progress_callback,
            detect_proposals=self._detect_proposals,
            is_cancelled=lambda: self._cancelled,
        )
        self.chat_turn_service = ChatTurnService(self)
        self.post_turn_service = PostTurnService(
            session_history=self.session_history,
            archivist=self.archivist,
            creative_memory_storage=self.creative_memory_storage,
            summarize_conversation=self._summarize_conversation,
            verify_compact=self._verify_compact_artifact,
        )
        self.plan_execution_service = PlanExecutionService(
            gateway=self.gateway,
            writer=self.writer,
            draft_storage=self.draft_storage,
            plan_store=self.plan_store,
            select_engine=self.select_engine,
            storage_adapter=self.storage_adapter,
            worker_service=self.worker_task_service,
            emit_progress=self._emit_progress,
            translate=self._p,
            is_cancelled=lambda: self._cancelled,
            write_step=self._plan_write_step,
            edit_step=self._plan_edit_step,
            analyze_step=self._plan_analyze_step,
        )
        self.volume_summary_service = VolumeSummaryService(
            draft_storage=self.draft_storage,
            archivist=self.archivist,
        )
        self.application = OrchestratorApplicationPorts(
            conversation=ConversationPort(self.session_history, self.post_turn_service),
            commands=CommandPort(self._run_command),
            analysis=AnalysisPort(self),
            volumes=self.volume_summary_service,
            plans=self.plan_execution_service,
        )

    def set_language(self, language: str) -> None:
        normalized = normalize_language(language, default=self.language)
        if normalized not in {"zh", "en"}:
            return
        self.language = normalized
        try:
            self.archivist.language = normalized
            self.writer.language = normalized
            self.context_assembly_service.set_language(normalized)
        except Exception:
            return

    def set_progress_callback(self, callback: Optional[Callable]) -> None:
        self.progress_callback = callback
        self.writing_service.progress_callback = callback

    def _p(self, zh: str, en: str) -> str:
        return en if self.language == "en" else zh

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
                    "iteration": 0,
                }
            )

    async def _handle_error(self, error_message: str, *, exc: Exception | None = None) -> Dict[str, Any]:
        """Handle error and update status."""
        resolved = exc or RuntimeError("orchestrator_error")
        envelope = error_envelope(resolved)
        logger.error(
            "Orchestrator operation failed: code=%s internal_message=%s",
            safe_error_code(resolved),
            error_message,
            exc_info=exc is not None,
        )

        self.current_status = SessionStatus.ERROR

        if self.progress_callback:
            await self.progress_callback(
                {
                    "status": SessionStatus.ERROR.value,
                    "message": envelope.safe_detail,
                    "error": envelope.to_dict(),
                    "project_id": self.current_project_id,
                    "chapter": self.current_chapter,
                }
            )

        return {"success": False, "status": SessionStatus.ERROR, "error": envelope.to_dict()}

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

    async def _plan_write_step(self, project_id: str, step: Dict[str, Any]) -> str:
        chapter = str(step.get("chapter") or "").strip()
        description = str(step.get("description") or "").strip()
        return await self._execute_plan_writing_step(project_id, chapter, description, "write")

    async def _plan_edit_step(self, project_id: str, step: Dict[str, Any]) -> str:
        chapter = str(step.get("chapter") or "").strip()
        description = str(step.get("description") or "").strip()
        return await self._execute_plan_writing_step(project_id, chapter, description, "edit")

    async def _execute_plan_writing_step(
        self,
        project_id: str,
        chapter: str,
        description: str,
        action: str,
    ) -> str:
        """Execute an approved plan step through the single Writer path and persist it."""
        if not chapter:
            return f"{action}: missing_chapter"
        result = await self.writing_service.run(project_id, chapter, description)
        if not result.get("success") or not result.get("changed"):
            state = result.get("terminal_state") or result.get("reason") or "incomplete"
            return f"{action} {chapter}: {state}"
        await self.draft_storage.save_current_draft(project_id, chapter, str(result.get("content") or ""))
        turn_effect = result.get("turn_effect")
        if isinstance(turn_effect, dict):
            await self.apply_turn_effect(project_id, chapter, turn_effect)
        return f"{action} {chapter}: completed"

    async def _plan_analyze_step(self, project_id: str, step: Dict[str, Any]) -> str:
        chapter = str(step.get("chapter") or "").strip()
        res = await self.analyze_chapter(project_id, chapter)
        return f"analyze {chapter}: {res.get('success')}"

    # ---------------------------------------------------------------- 对话记忆层 --
    # 持久化对话历史（Git-Native）+ compact 长对话压缩 + 顺带提炼作者偏好 → creative_memory。
    # 取代脆弱的前端 localStorage 单点：刷新/重启/清缓存/换机均不丢，且可 Git 追踪。

    async def _summarize_conversation(self, text: str) -> Dict[str, Any]:
        """Build CompactArtifactV2 sections; safely degrade to a recoverable summary."""
        text = str(text or "").strip()
        if not text:
            return {}
        try:
            provider = self.gateway.get_provider_for_agent(self.archivist.get_agent_name())
            system = self._p(
                "你是创作会话状态压缩器。只输出 JSON 对象，字段固定为 decisions、constraints、entity_state、"
                "open_loops（字符串数组）和 recent_summary（字符串）。只保留输入明确支持的内容；不推断新事实，"
                "不把助手建议当作作者决定，不遗漏仍生效的硬约束和未决事项。recent_summary 不超过 300 字。",
                "You compress writing-session state. Return one JSON object with decisions, constraints, entity_state, "
                "open_loops (string arrays), and recent_summary (string). Include only source-supported claims; do not "
                "turn assistant suggestions into user decisions. Preserve active constraints and unresolved work.",
            )
            messages = [{"role": "system", "content": system}, {"role": "user", "content": text[:6000]}]
            scope = current_turn_scope()
            if scope is not None and scope.source_closure_required:
                scope.register_provider_payload(
                    messages,
                    source_prefix="orchestrator.compact_summary",
                    selection_reason="conversation_compact_assembly",
                    artifact_ref="Orchestrator._summarize_conversation",
                )
            resp = await self.gateway.chat(
                messages,
                provider=provider,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            out = str(resp.get("content") or "").strip()
            if out:
                from app.utils.llm_output import parse_json_payload

                parsed, error = parse_json_payload(out, expected_type=dict)
                if parsed and not error:
                    parsed["_provenance"] = {
                        "provider": str(resp.get("provider") or provider or ""),
                        "model": str(resp.get("model") or ""),
                        "prompt_fingerprint": str(resp.get("request_fingerprint") or ""),
                    }
                    return parsed
        except Exception as exc:
            logger.warning("conversation summary via LLM failed; falling back to rule-based: %s", exc)
        try:
            from app.context_engine.smart_compressor import smart_compress

            compressed, _ = smart_compress(text, target_ratio=0.35)
            return {"recent_summary": str(compressed or "").strip() or text[:600]}
        except Exception:
            return {"recent_summary": text[:600]}

    async def _verify_compact_artifact(self, artifact: Any, source_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Use an independent reviewer profile to reject lossy or unsupported compact state."""
        from app.utils.llm_output import parse_json_payload

        provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        source = "\n".join(
            f"{item.get('role', 'user')}: {str(item.get('content') or '').strip()}"
            for item in source_messages
            if str(item.get("content") or "").strip()
        )
        payload = {
            "source_conversation": source[:60000],
            "compact_artifact": artifact.to_dict(),
            "criteria": {
                "unsupported_claims": "artifact claims absent from source",
                "severe_omissions": "missing active hard constraints, decisions, entity state, or open loops",
                "contradictions": "artifact conflicts with source",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是独立的会话压缩审计器。只输出 JSON：unsupported_claims、severe_omissions、"
                    "contradictions（字符串数组）及 valid（布尔值）。只有三个数组均为空时 valid 才为 true。"
                    "不要评价文风，不要补充来源中不存在的信息。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            scope.register_provider_payload(
                messages,
                source_prefix="orchestrator.compact_verify",
                selection_reason="compact_verifier_assembly",
                artifact_ref="Orchestrator._verify_compact_artifact",
            )
        response = await self.gateway.chat(
            messages,
            provider=provider,
            temperature=0.0,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        parsed, error = parse_json_payload(str(response.get("content") or ""), expected_type=dict)
        if error or not parsed:
            return {"available": True, "valid": False, "reason": "invalid_verifier_response", "error": error}
        unsupported = [str(item) for item in parsed.get("unsupported_claims") or [] if str(item).strip()]
        omissions = [str(item) for item in parsed.get("severe_omissions") or [] if str(item).strip()]
        contradictions = [str(item) for item in parsed.get("contradictions") or [] if str(item).strip()]
        valid = not unsupported and not omissions and not contradictions and parsed.get("valid") is True
        return {
            "available": True,
            "valid": valid,
            "unsupported_claims": unsupported,
            "severe_omissions": omissions,
            "contradictions": contradictions,
            "provider": response.get("provider") or provider,
            "model": response.get("model"),
            "request_fingerprint": response.get("request_fingerprint"),
        }

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
        reasoning_level: str = "auto",
        selection_text: str = "",
    ) -> Dict[str, Any]:
        """Delegate the main route to ChatTurnService."""

        return await self.chat_turn_service.run(
            project_id,
            chapter,
            message,
            has_selection=has_selection,
            has_draft=has_draft,
            target_word_count=target_word_count,
            auto_execute_plan=auto_execute_plan,
            thinking=thinking,
            reasoning_level=reasoning_level,
            selection_text=selection_text,
        )

    def _cancel_active_turns(self) -> int:
        """Cancel every active turn owned by this project orchestrator."""

        scopes = list(self._active_turn_scopes.values())
        for scope in scopes:
            scope.cancel()
        self._cancelled = True
        return len(scopes)

    def cancel_session(self) -> Dict[str, int]:
        """Cancel all turn scopes and active stream tasks, then reset session state."""
        turns = self._cancel_active_turns()
        streams = self.stream_tasks.cancel_all()
        self.current_status = SessionStatus.IDLE
        self.current_project_id = None
        self.current_chapter = None
        return {"turns": turns, "streams": streams}

    async def _run_command(
        self,
        *,
        project_id: str,
        chapter: str,
        intent: str,
        route_path: str,
        operation: Callable[[], Any],
        target_word_count: int = 3000,
    ) -> Any:
        """Execute an explicit application operation under the shared turn control plane."""

        existing = current_turn_scope()
        owns_scope = existing is None
        scope = existing or new_turn_scope(project_id=project_id, chapter_id=chapter)
        if owns_scope:
            scope.context_epoch = await self.session_history.current_context_epoch(project_id)
        if owns_scope:
            self._active_turn_scopes[scope.turn_id] = scope
        try:
            with bind_turn_scope(scope) if owns_scope else nullcontext(scope):
                if scope.runtime.state == TurnState.CREATED:
                    scope.runtime.transition(TurnState.ROUTING)
                if scope.runtime.state == TurnState.ROUTING:
                    scope.runtime.transition(TurnState.CONTEXT_PLANNING, metadata={"intent": intent})
                self.context_planning_service.prepare_context_plan(
                    scope=scope,
                    project_id=project_id,
                    chapter=chapter,
                    intent=intent,
                    route_path=route_path,
                    target_word_count=target_word_count,
                )
                result = operation()
                if route_path == "plan_workflow" and scope.runtime.state == TurnState.CONTEXT_PLANNING:
                    scope.runtime.transition(TurnState.PLAN_RUNNING)
                elif route_path == "agentic_writer" and scope.runtime.state == TurnState.CONTEXT_PLANNING:
                    scope.runtime.transition(TurnState.WRITER_RUNNING)
                elif scope.runtime.state == TurnState.CONTEXT_PLANNING:
                    scope.runtime.transition(TurnState.WORKER_RUNNING, metadata={"route": route_path})
                if asyncio.iscoroutine(result):
                    result = await result
                if owns_scope and isinstance(result, dict):
                    result.setdefault("route_contract", route_contract(intent))
                    result = await self.context_planning_service.attach_chat_context_plan(
                        result,
                        project_id=project_id,
                        chapter=chapter,
                        intent=intent,
                        target_word_count=target_word_count,
                    )
                if owns_scope:
                    scope.runtime.complete()
                    if isinstance(result, dict):
                        result["runtime"] = scope.runtime.to_dict()
                return result
        except asyncio.CancelledError:
            scope.runtime.cancel("task_cancelled")
            raise
        except Exception as exc:
            scope.runtime.fail(exc)
            raise
        finally:
            if owns_scope:
                self._active_turn_scopes.pop(scope.turn_id, None)

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
