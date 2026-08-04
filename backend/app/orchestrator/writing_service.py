"""Agentic writer execution service."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.agents.agentic import run_agentic_chat
from app.agents.tools import WriterToolset
from app.agents.writing_actions import WritingActionToolset, normalize_clarification_questions
from app.config import config
from app.context_engine.tool_artifact import ToolExecutionStatus
from app.context_engine.turn_scope import current_turn_scope
from app.error_contract import record_degradation, safe_error_code
from app.llm_gateway.thinking import reasoning_param_enabled
from app.orchestrator.context_assembly_service import ContextAssemblyService
from app.orchestrator.runtime_contracts import (
    AgentStreamEvent,
    DraftStoragePort,
    GatewayPort,
    ProgressCallback,
    ProposalDetector,
    WriterAgentPort,
    WritingResult,
)
from app.schemas.draft import ChapterSummary
from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 撰写/编辑工具的参数就是整章正文（write_content.content / edit_lines.new_text）。
# 正文已经过 provisional token 原生流式推给前端编辑器；tool_call 事件不应再重复搬运全文
# （既浪费带宽，也会让「工具调用」轨迹泄漏正文）。此处把这类参数收敛为长度摘要。
_WRITING_ACTION_TOOLS = {"write_content", "edit_lines"}
_CLARIFICATION_TOOL = "ask_clarification"


def _safe_tool_call_arguments(name: Any, arguments: Any) -> Any:
    tool_name = str(name or "")
    if tool_name == _CLARIFICATION_TOOL:
        parsed = arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                parsed = {}
        raw_questions = parsed.get("questions") if isinstance(parsed, dict) else []
        count = len(raw_questions) if isinstance(raw_questions, list) else 0
        return {"question_count": min(3, count)}
    if tool_name not in _WRITING_ACTION_TOOLS:
        return arguments
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {"_summary": f"{len(arguments)} chars"}
    if not isinstance(parsed, dict):
        return {"_summary": "writing_action"}
    summary: Dict[str, Any] = {}
    if "mode" in parsed:
        summary["mode"] = parsed.get("mode")
    for field in ("content", "new_text", "old_text"):
        if field in parsed:
            summary[f"{field}_chars"] = len(str(parsed.get(field) or ""))
    return summary or {"_summary": "writing_action"}


class WritingService:
    """Own tool execution, provider calls, and proposal streaming for writer turns."""

    def __init__(
        self,
        *,
        gateway: GatewayPort,
        writer: WriterAgentPort,
        draft_storage: DraftStoragePort,
        storage_adapter: object,
        select_engine: object,
        context_assembly: ContextAssemblyService,
        progress_callback: Optional[ProgressCallback] = None,
        detect_proposals: Optional[ProposalDetector] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ):
        self.gateway = gateway
        self.writer = writer
        self.draft_storage = draft_storage
        self.storage_adapter = storage_adapter
        self.select_engine = select_engine
        self.context_assembly = context_assembly
        self.progress_callback = progress_callback
        self.detect_proposals = detect_proposals
        self.is_cancelled = is_cancelled or (lambda: False)

    async def run(
        self,
        project_id: str,
        chapter: str,
        message: str,
        *,
        has_selection: bool = False,
        thinking: bool = False,
        reasoning_level: str = "off",
        target_word_count: int = 3000,
    ) -> WritingResult:
        current_text, current_path = await self._load_working_text(project_id, chapter)
        try:
            existing_chapters = list(await self.draft_storage.list_chapters(project_id))
        except Exception:
            existing_chapters = []
        outline_settings, outline_push = await self._resolve_outline(project_id)
        relations_push = await self._resolve_relations_push(project_id)
        clarification_settings = await self._resolve_clarification_settings(project_id)
        clarification_policy = self._clarification_policy_text(clarification_settings)
        retrieval = WriterToolset(
            project_id,
            self.storage_adapter,
            self.select_engine,
            current_chapter=chapter,
            outline_enabled=bool(outline_settings.get("enabled", True)),
        )
        writing_tools = WritingActionToolset(
            current_text,
            retrieval_toolset=retrieval,
            active_chapter=chapter,
            existing_chapters=existing_chapters,
            require_chapter_target=True,
        )
        scope = current_turn_scope()
        request = self.context_assembly.assemble_writer_request(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            context_plan=scope.active_plan if scope is not None else None,
            existing_chapters=existing_chapters,
            outline_push=outline_push,
            relations_push=relations_push,
            outline_enabled=bool(outline_settings.get("enabled", True)),
            clarification_policy=clarification_policy,
        )
        if scope is not None and scope.source_closure_required and current_text and current_path is not None:
            try:
                if current_path.is_file():
                    persisted = current_path.read_text(encoding="utf-8")
                    if persisted == current_text:
                        scope.register_source_file(
                            current_path,
                            source_id="draft.current",
                            asset_type="draft",
                            selection_reason="edit_baseline",
                            required=True,
                        )
            except (OSError, UnicodeError, RuntimeError, ValueError):
                pass
        if scope is not None and scope.turn_trace is not None:
            scope.turn_trace.assembly_fingerprints.append(request.fingerprint)
            scope.turn_trace.source_usage.extend(
                [
                    {"type": "user_message", "revision": hashlib.sha256(message.encode("utf-8")).hexdigest()},
                    {"type": "chapter", "id": chapter},
                ]
            )
            if current_text:
                scope.turn_trace.source_usage.append(
                    {"type": "draft", "id": chapter, "revision": hashlib.sha256(current_text.encode("utf-8")).hexdigest()}
                )
        try:
            from app.observability.usage_diagnostics import record_edit_assembly, record_source_usage

            for source_type in request.supply_report.available:
                record_source_usage(str(source_type), "available")
            for source_type in request.supply_report.pushed:
                record_source_usage(str(source_type), "selected")
            for item in request.supply_report.omitted:
                record_source_usage(str(item.get("type") or "other"), "omitted")
            if current_text:
                record_edit_assembly(
                    draft_tokens=request.supply_report.draft_tokens,
                    pushed_tokens=request.supply_report.draft_pushed_tokens,
                    projected=bool(request.supply_report.omitted),
                )
        except Exception as exc:
            record_degradation("writer_source_usage_diagnostics", exc)
        tools_used_any = False
        tool_names: set[str] = set()
        stream_state = {"started": False, "provisional": False}
        reasoning_visible = False

        def current_chapter() -> str:
            return str(writing_tools.target_chapter or chapter or "")

        def current_chapter_target() -> Optional[Dict[str, Any]]:
            return writing_tools.chapter_target()

        supply_report_payload = request.supply_report.to_dict()

        def incomplete_result(reason: str, agent_run: Optional[Dict[str, Any]] = None) -> WritingResult:
            return {
                "success": False,
                "incomplete": True,
                "terminal_state": "incomplete",
                "reason": reason,
                "agent_run": dict(agent_run or {}),
                "context_supply": dict(supply_report_payload),
            }

        def failed_result(reason: str, agent_run: Optional[Dict[str, Any]] = None) -> WritingResult:
            return {
                "success": False,
                "terminal_state": "failed",
                "reason": reason,
                "agent_run": dict(agent_run or {}),
                "context_supply": dict(supply_report_payload),
            }

        async def on_event(event: AgentStreamEvent) -> None:
            nonlocal tools_used_any
            if event.get("type") == "tool_call":
                tools_used_any = True
                tool_names.add(str(event.get("name") or ""))
                if str(event.get("name") or "") == "finish_turn":
                    return
            if event.get("type") == "tool_result" and str(event.get("name") or "") == "finish_turn":
                return
            if scope is not None and scope.turn_trace is not None and event.get("type") == "tool_result":
                scope.turn_trace.source_usage.append(
                    {
                        "type": "jit_tool",
                        "tool": str(event.get("name") or ""),
                        "arguments_fingerprint": hashlib.sha256(
                            json.dumps(event.get("arguments"), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest(),
                        "result_fingerprint": hashlib.sha256(str(event.get("result") or "").encode("utf-8")).hexdigest(),
                    }
                )
                try:
                    from app.observability.usage_diagnostics import record_source_usage, tool_bucket

                    record_source_usage(tool_bucket(str(event.get("name") or "")), "used")
                except Exception as exc:
                    record_degradation("writer_jit_usage_diagnostics", exc)
            if (
                event.get("type") == "provisional_content"
                and event.get("source") == "tool_argument"
                and self.progress_callback
            ):
                if not stream_state["started"]:
                    stream_state["started"] = True
                    await self._safe_progress(
                        {
                            "type": "stream_start",
                            "project_id": project_id,
                            "chapter": current_chapter(),
                            "chapter_target": current_chapter_target(),
                            "provisional": True,
                        }
                    )
                content = str(event.get("content") or "")
                if content:
                    stream_state["provisional"] = True
                    await self._safe_progress(
                        {
                            "type": "token",
                            "project_id": project_id,
                            "chapter": current_chapter(),
                            "content": content,
                            "provisional": True,
                            "source": str(event.get("source") or "assistant"),
                        }
                    )
            await self._emit_agent_event(project_id, current_chapter(), event)

        async def close_provisional(status: str, reason: str = "") -> None:
            # 不变量：只要发过 provisional stream_start，就必须补发一个终止流事件，
            # 否则前端 serverStreamActive/streamingState 卡在 true → 一直「生成中」。
            # 因此以 started（是否已发 stream_start）为准，而非 provisional（是否有非空 token）。
            if stream_state["started"] and self.progress_callback:
                await self._safe_progress(
                    {
                        "type": "stream_abort" if status != "completed" else "stream_complete",
                        "project_id": project_id,
                        "chapter": current_chapter(),
                        "provisional": True,
                        "reason": str(reason or ""),
                    }
                )

        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        if not provider:
            return failed_result("no_provider")

        thinking_param = None
        if thinking or reasoning_level != "auto":
            builder = getattr(self.gateway, "thinking_param_for_agent", None)
            if callable(builder):
                thinking_param = builder(
                    self.writer.get_agent_name(),
                    thinking,
                    reasoning_level=reasoning_level,
                )
        reasoning_visible = reasoning_param_enabled(thinking_param)
        try:
            response = await run_agentic_chat(
                self.gateway,
                provider,
                request.messages,
                writing_tools,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                max_iterations=request.max_iterations,
                on_event=on_event,
                thinking=thinking_param,
                emit_reasoning=reasoning_visible,
            )
        except Exception as exc:
            if str(exc) == "turn_cancelled":
                await close_provisional("cancelled", "turn_cancelled")
                return {"success": False, "cancelled": True, "reason": "turn_cancelled"}
            logger.warning("writing service failed: %s", exc)
            await close_provisional("failed", safe_error_code(exc))
            return failed_result(safe_error_code(exc))

        agent_run = response.to_dict()
        response_payload = dict(getattr(response, "response", {}) or {})
        chapter_target = current_chapter_target()
        target_chapter = current_chapter()
        supply_report = dict(supply_report_payload)
        retrieved_types = sorted(
            {
                {
                    "lookup_card": "card",
                    "query_canon": "canon",
                    "query_relations": "canon",
                    "read_chapter": "prose",
                    "search_prose": "prose",
                }.get(name, "")
                for name in tool_names
                if name
            }
            - {""}
        )
        supply_report["retrieved"] = retrieved_types
        supply_report["used"] = sorted(set(request.supply_report.used) | set(retrieved_types))
        if response.cancelled:
            await close_provisional("cancelled", response.finish_reason or "turn_cancelled")
            return {
                "success": False,
                "cancelled": True,
                "reason": response.finish_reason or "turn_cancelled",
                "agent_run": agent_run,
                "context_supply": supply_report,
            }
        if response.incomplete:
            reason = response.finish_reason or "max_iterations"
            clarification = response_payload.get("clarification")
            raw_questions = response_payload.get("questions")
            if not raw_questions and isinstance(clarification, dict):
                raw_questions = clarification.get("questions")
            questions = normalize_clarification_questions(raw_questions)
            if questions and (isinstance(clarification, dict) or response_payload.get("terminal_state") == "requires_input"):
                await close_provisional("requires_input", reason)
                pause_run = response.to_dict(include_response=True)
                clarification_payload = dict(clarification) if isinstance(clarification, dict) else {}
                clarification_payload.update(
                    {
                        "decision": "ask",
                        "questions": questions,
                        "question_count": len(questions),
                    }
                )
                return {
                    "success": False,
                    "terminal_state": "requires_input",
                    "incomplete": True,
                    "reason": "clarification_requested",
                    "action": "agentic_write",
                    "changed": False,
                    "partial": False,
                    "questions": questions[:3],
                    "clarification": clarification_payload,
                    "clarify_decision": "ask",
                    "actions": list(writing_tools.actions),
                    "assembly_fingerprint": request.fingerprint,
                    "agent_run": pause_run,
                    "context_supply": supply_report,
                }
            turn_effect = writing_tools.terminal_payload()
            if writing_tools.changed:
                proposals = await self._stream_text_as_diff(
                    project_id,
                    target_chapter,
                    writing_tools.working_text,
                    emit_tokens=not stream_state["provisional"],
                    stream_started=stream_state["started"],
                    turn_effect=turn_effect,
                    chapter_target=chapter_target,
                )
                return {
                    "success": True,
                    "terminal_state": "incomplete",
                    "incomplete": True,
                    "action": "agentic_write",
                    "changed": True,
                    "partial": True,
                    "reason": reason,
                    "content": writing_tools.working_text,
                    "proposals": proposals,
                    "actions": writing_tools.actions,
                    "summary": "已达到单轮工具调用上限，已交付当前修改供审阅。",
                    "assembly_fingerprint": request.fingerprint,
                    "agent_run": agent_run,
                    "context_supply": supply_report,
                    "turn_effect": turn_effect,
                    "chapter_target": chapter_target,
                }
            await close_provisional("incomplete", reason)
            return incomplete_result(reason, agent_run)
        if not response.success:
            reason = str((response.error or {}).get("code") or response.finish_reason or "agent_run_failed")
            await close_provisional("failed", reason)
            if reason in {"turn_deadline_exceeded", "timeout"}:
                return incomplete_result(reason, agent_run)
            return failed_result(reason, agent_run)

        turn_effect = writing_tools.terminal_payload()
        if not writing_tools.changed:
            if not tools_used_any:
                await close_provisional("incomplete", "no_tool_calls")
                return incomplete_result("no_tool_calls", agent_run)
            await close_provisional("completed")
            return {
                "success": True,
                "action": "reply",
                "changed": False,
                "message": str(turn_effect.get("message") or response.get("content") or ""),
                "assembly_fingerprint": request.fingerprint,
                "agent_run": agent_run,
                "context_supply": supply_report,
                "turn_effect": turn_effect,
                "chapter_target": chapter_target,
            }

        auto_commit: Optional[Dict[str, Any]] = None
        if isinstance(chapter_target, dict) and chapter_target.get("create"):
            try:
                auto_commit = await self._commit_created_chapter(
                    project_id,
                    target_chapter,
                    writing_tools.working_text,
                    str(chapter_target.get("title") or ""),
                )
            except Exception as exc:
                logger.warning("Created chapter commit failed: %s", safe_error_code(exc), exc_info=True)
                await close_provisional("failed", "chapter_commit_failed")
                failed = failed_result("chapter_commit_failed", agent_run)
                failed.update(
                    {
                        "content": writing_tools.working_text,
                        "chapter_target": chapter_target,
                        "turn_effect": turn_effect,
                        "auto_commit": {
                            "committed": False,
                            "chapter": target_chapter,
                            "reason": safe_error_code(exc),
                        },
                    }
                )
                return failed

        proposals = await self._stream_text_as_diff(
            project_id,
            target_chapter,
            writing_tools.working_text,
            emit_tokens=not stream_state["provisional"],
            stream_started=stream_state["started"],
            turn_effect=turn_effect,
            chapter_target=chapter_target,
            auto_commit=auto_commit,
        )
        return {
            "success": True,
            "action": "agentic_write",
            "changed": True,
            "content": writing_tools.working_text,
            "proposals": proposals,
            "actions": writing_tools.actions,
            "summary": str(turn_effect.get("message") or response.get("content") or "").strip(),
            "assembly_fingerprint": request.fingerprint,
            "agent_run": agent_run,
            "context_supply": supply_report,
            "turn_effect": turn_effect,
            "chapter_target": chapter_target,
            "auto_commit": auto_commit,
        }

    async def _commit_created_chapter(
        self,
        project_id: str,
        chapter: str,
        content: str,
        title: str,
    ) -> Dict[str, Any]:
        """Persist a successfully completed new chapter before announcing stream completion."""

        target = str(chapter or "").strip()
        text = str(content or "")
        if not target or not text.strip():
            raise ValueError("created_chapter_missing_content")
        await self.draft_storage.save_current_draft(
            project_id=project_id,
            chapter=target,
            content=text,
            word_count=len(text),
            create_prev_backup=False,
        )
        summary = await self.draft_storage.get_chapter_summary(project_id, target)
        clean_title = str(title or "").strip() or target
        if summary is None:
            summary = ChapterSummary(
                chapter=target,
                volume_id=ChapterIDValidator.extract_volume_id(target) or "V1",
                title=clean_title,
                word_count=len(text),
            )
        else:
            summary.title = clean_title
            summary.word_count = len(text)
        await self.draft_storage.save_chapter_summary(project_id, summary)
        return {
            "committed": True,
            "chapter": target,
            "title": clean_title,
            "word_count": len(text),
        }

    async def _resolve_outline(self, project_id: str) -> tuple[Dict[str, Any], str]:
        """解析大纲设置；require_consult 开启且启用时返回要推入 writer 稳定前缀的大纲文本。

        大纲永不进入事实提取——这里只把它作为「规划意图」推入上下文供 AI 查阅。
        """
        from app.services.outline_settings import resolve_outline_settings

        try:
            card = getattr(self.storage_adapter, "card", None)
            meta: Dict[str, Any] = {}
            if card is not None:
                project_file = card.get_project_path(project_id) / "project.yaml"
                if project_file.is_file():
                    meta = await card.read_yaml(project_file) or {}
            settings = resolve_outline_settings(meta)
        except Exception as exc:
            record_degradation("writer_outline_settings", exc)
            return {"enabled": True, "require_consult": False, "max_push_tokens": 2000}, ""
        push_text = ""
        if settings.get("enabled") and settings.get("require_consult"):
            try:
                outline = getattr(self.storage_adapter, "outline", None)
                if outline is not None:
                    data = await outline.get_outline(project_id)
                    content = str(data.get("content") or "").strip()
                    if content:
                        # 稳定前缀有 token 上限：超限截断为「摘要 + 恢复入口」，不硬失败。
                        char_budget = max(200, int(settings.get("max_push_tokens", 2000)) * 2)
                        if len(content) > char_budget:
                            content = content[:char_budget].rstrip() + "\n…（大纲较长，其余可用 read_outline 工具查阅）"
                        push_text = content
            except Exception as exc:
                record_degradation("writer_outline_push", exc)
        return settings, push_text

    async def _resolve_relations_push(self, project_id: str) -> str:
        """把卡片层作者设定的人物关系与称呼渲染为 writer 稳定前缀块（U4 供给主路径）。

        为什么默认注入而不是等模型调用 ``query_relations``：关系边是**作者设定**、规模有界
        （存储层硬上限 500 条、每个标签 ≤20 字），却决定每一句对白该怎么称呼对方——
        属于「确定性必选项」，与风格卡同类。放在 system 稳定前缀还对 prompt caching 友好。
        超出条数上限时显式标注剩余条数并指向 ``query_relations``，不静默截断。
        """
        settings = dict(config.get("retrieval", {}).get("relations", {}) or {})
        if settings.get("enabled") is False:
            return ""
        try:
            get_edges = getattr(self.storage_adapter, "get_card_relation_edges", None)
            if get_edges is None:
                return ""
            edges = [edge for edge in (await get_edges(project_id) or []) if isinstance(edge, dict)]
        except Exception as exc:
            record_degradation("writer_card_relations_push", exc)
            return ""
        if not edges:
            return ""
        from app.context_engine.relation_graph import Relation

        limit = max(1, int(settings.get("max_push_edges") or 80))
        lines = [f"- {Relation.from_card_edge(edge).text()}" for edge in edges[:limit]]
        remaining = len(edges) - len(lines)
        if remaining > 0:
            lines.append(f"（另有 {remaining} 条设定关系未在此列出，需要时用 query_relations 按人物查询）")
        return "\n".join(lines)

    async def _resolve_clarification_settings(self, project_id: str) -> Dict[str, Any]:
        """Load the Writer tool policy without invoking another model or workflow."""

        from app.services.clarify_settings import resolve_clarify_settings

        try:
            card = getattr(self.storage_adapter, "card", None)
            meta: Dict[str, Any] = {}
            if card is not None:
                project_file = card.get_project_path(project_id) / "project.yaml"
                if project_file.is_file():
                    meta = await card.read_yaml(project_file) or {}
            return resolve_clarify_settings(meta)
        except Exception as exc:
            record_degradation("writer_clarification_settings", exc)
            return {"auto_trigger": "auto", "mode": "auto"}

    @staticmethod
    def _clarification_policy_text(settings: Dict[str, Any]) -> str:
        """Describe policy to the Writer; this is guidance, not deterministic routing."""

        trigger = str(settings.get("auto_trigger") or settings.get("mode") or "auto").strip().lower()
        if trigger == "always":
            posture = "完成必要检索后，主动检查是否存在会显著改变本轮结果的未决作者选择；有则调用工具，没有则继续写作。"
        elif trigger == "off":
            posture = "除非用户明确要求你先确认，否则不要主动调用该工具；工具仍可按你的判断使用。"
        else:
            posture = "仅当现有上下文无法可靠确定、且缺口会显著改变结果时调用工具；信息足够时直接写作。"
        return (
            f"{posture}\n"
            "这是同一个可选 Writer 工具，不是独立流程；每个 Writer turn 最多调用一次。"
            "问题由你根据本轮已注入和已检索的上下文自行拟定，每次自行选择 1-3 个；"
            "数量不是设置项，也不要凑满上限；不要使用固定题库或泛化问题。"
        )

    async def _load_working_text(self, project_id: str, chapter: str) -> tuple[str, Optional[Path]]:
        """加载用户当前所见正文（final.md 与 draft_*.md 取最新，见 DraftStorage.get_working_text）。"""
        try:
            return await self.draft_storage.get_working_text(project_id, chapter)
        except Exception:
            return "", None

    async def _emit_agent_event(self, project_id: str, chapter: str, event: AgentStreamEvent) -> None:
        if not self.progress_callback:
            return
        event_type = event.get("type")
        payload: Optional[Dict[str, Any]] = None
        if event_type == "thinking":
            payload = {
                "type": "agent_thinking",
                "project_id": project_id,
                "chapter": chapter,
                "turn_id": str(getattr(current_turn_scope(), "turn_id", "") or ""),
                "content": str(event.get("content") or "")[:2000],
            }
        elif event_type == "assistant_text":
            payload = {
                "type": "agent_message",
                "project_id": project_id,
                "chapter": chapter,
                "turn_id": str(getattr(current_turn_scope(), "turn_id", "") or ""),
                "content": str(event.get("content") or "")[:2000],
            }
        elif event_type == "tool_call":
            payload = {
                "type": "agent_tool_call",
                "project_id": project_id,
                "chapter": chapter,
                "turn_id": str(getattr(current_turn_scope(), "turn_id", "") or ""),
                "tool_call_id": event.get("tool_call_id"),
                "name": event.get("name"),
                "arguments": _safe_tool_call_arguments(event.get("name"), event.get("arguments")),
            }
        elif event_type == "tool_result":
            # 执行状态直接取自 ToolExecutionResult.to_dict()（agentic 循环已透传），
            # 不在此处重算。error 只取结构化 code：明文 message 可能含正文片段，
            # 不得进入 WS payload（对齐「正文/prompt/output 不进 telemetry」不变量）。
            execution = event.get("tool_result")
            execution = execution if isinstance(execution, dict) else {}
            error = execution.get("error")
            payload = {
                "type": "agent_tool_result",
                "project_id": project_id,
                "chapter": chapter,
                "turn_id": str(getattr(current_turn_scope(), "turn_id", "") or ""),
                "tool_call_id": event.get("tool_call_id"),
                "name": event.get("name"),
                "result": str(event.get("result") or "")[:1000],
                "status": str(execution.get("status") or ToolExecutionStatus.SUCCEEDED.value),
                "error_code": (error.get("code") if isinstance(error, dict) else None),
                "elapsed_ms": int(execution.get("elapsed_ms") or 0),
                "recoverable": bool(execution.get("recoverable")),
            }
        if payload is not None:
            await self.progress_callback(payload)

    async def _stream_text_as_diff(
        self,
        project_id: str,
        chapter: str,
        final_text: str,
        *,
        emit_tokens: bool = True,
        stream_started: bool = False,
        turn_effect: Optional[Dict[str, Any]] = None,
        chapter_target: Optional[Dict[str, Any]] = None,
        auto_commit: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        text = str(final_text or "")
        if self.progress_callback:
            if not stream_started:
                await self._safe_progress(
                    {
                        "type": "stream_start",
                        "project_id": project_id,
                        "chapter": chapter,
                        "chapter_target": dict(chapter_target or {}),
                    }
                )
            if emit_tokens:
                for chunk in self._chunk(text):
                    if self.is_cancelled():
                        break
                    await self._safe_progress(
                        {"type": "token", "project_id": project_id, "chapter": chapter, "content": chunk}
                    )
        proposals: List[Dict[str, Any]] = []
        if text and not self.is_cancelled() and self.detect_proposals is not None:
            try:
                proposals = await self.detect_proposals(project_id, text)
            except Exception:
                proposals = []
        if self.progress_callback:
            await self._safe_progress(
                {
                    "type": "stream_end",
                    "project_id": project_id,
                    "chapter": chapter,
                    "draft": {"chapter": chapter, "version": "v1", "content": text, "word_count": len(text)},
                    "proposals": proposals,
                    "turn_effect": dict(turn_effect or {}),
                    "chapter_target": dict(chapter_target or {}),
                    "auto_commit": dict(auto_commit or {}),
                    "provisional_replaced": not emit_tokens,
                }
            )
        return proposals

    async def _safe_progress(self, payload: Dict[str, Any]) -> None:
        if not self.progress_callback:
            return
        try:
            await self.progress_callback(payload)
        except Exception as exc:
            record_degradation("writing_progress_callback", exc)

    @staticmethod
    def _chunk(text: str, size: int = 24) -> List[str]:
        return [text[index : index + size] for index in range(0, len(text), size)]
