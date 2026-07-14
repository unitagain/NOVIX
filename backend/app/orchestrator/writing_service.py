"""Agentic writer execution service."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.agents.agentic import run_agentic_chat
from app.agents.fallback_policy import build_fallback_context
from app.agents.tools import WriterToolset
from app.agents.writing_actions import WritingActionToolset
from app.context_engine.turn_scope import current_turn_scope
from app.error_contract import record_degradation, safe_error_code
from app.orchestrator.context_assembly_service import ContextAssemblyService
from app.utils.logger import get_logger

logger = get_logger(__name__)

ProgressCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


class WritingService:
    """Own tool execution, provider calls, and proposal streaming for writer turns."""

    def __init__(
        self,
        *,
        gateway: Any,
        writer: Any,
        draft_storage: Any,
        storage_adapter: Any,
        select_engine: Any,
        context_assembly: ContextAssemblyService,
        progress_callback: ProgressCallback = None,
        detect_proposals: Optional[Callable[[str, str], Awaitable[List[Dict[str, Any]]]]] = None,
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
        target_word_count: int = 3000,
    ) -> Dict[str, Any]:
        current_text = await self._load_current_text(project_id, chapter)
        retrieval = WriterToolset(
            project_id,
            self.storage_adapter,
            self.select_engine,
            current_chapter=chapter,
        )
        writing_tools = WritingActionToolset(current_text, retrieval_toolset=retrieval)
        scope = current_turn_scope()
        request = self.context_assembly.assemble_writer_request(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            context_plan=scope.active_plan if scope is not None else None,
        )
        if scope is not None and scope.source_closure_required and current_text:
            try:
                draft_path = self.draft_storage.get_latest_draft_file(project_id, chapter)
                if draft_path is not None and draft_path.is_file():
                    persisted = draft_path.read_text(encoding="utf-8")
                    if persisted == current_text:
                        scope.register_source_file(
                            draft_path,
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

            for source_type in request.supply_report.get("available") or []:
                record_source_usage(str(source_type), "available")
            for source_type in request.supply_report.get("pushed") or []:
                record_source_usage(str(source_type), "selected")
            for item in request.supply_report.get("omitted") or []:
                record_source_usage(str(item.get("type") or "other"), "omitted")
            if current_text:
                record_edit_assembly(
                    draft_tokens=int(request.supply_report.get("draft_tokens") or 0),
                    pushed_tokens=int(request.supply_report.get("draft_pushed_tokens") or 0),
                    projected=bool(request.supply_report.get("omitted")),
                )
        except Exception as exc:
            record_degradation("writer_source_usage_diagnostics", exc)
        tools_used = {"any": False, "names": set()}
        stream_state = {"started": False, "provisional": False}

        def fallback_result(reason: str, agent_run: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {
                "success": False,
                "fallback": True,
                "reason": reason,
                "agent_run": dict(agent_run or {}),
                "context_supply": dict(request.supply_report),
                "fallback_context": build_fallback_context(
                    reason=reason,
                    agent_run=agent_run,
                    context_supply=request.supply_report,
                ),
            }

        async def on_event(event: Dict[str, Any]) -> None:
            if event.get("type") == "tool_call":
                tools_used["any"] = True
                tools_used["names"].add(str(event.get("name") or ""))
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
                        {"type": "stream_start", "project_id": project_id, "chapter": chapter, "provisional": True}
                    )
                content = str(event.get("content") or "")
                if content:
                    stream_state["provisional"] = True
                    await self._safe_progress(
                        {
                            "type": "token",
                            "project_id": project_id,
                            "chapter": chapter,
                            "content": content,
                            "provisional": True,
                            "source": str(event.get("source") or "assistant"),
                        }
                    )
            await self._emit_agent_event(project_id, chapter, event)

        async def close_provisional(status: str, reason: str = "") -> None:
            if stream_state["provisional"] and self.progress_callback:
                await self._safe_progress(
                    {
                        "type": "stream_abort" if status != "completed" else "stream_complete",
                        "project_id": project_id,
                        "chapter": chapter,
                        "provisional": True,
                        "reason": str(reason or ""),
                    }
                )

        try:
            provider = self.gateway.get_provider_for_agent(self.writer.get_agent_name())
        except Exception:
            provider = None
        if not provider:
            return fallback_result("no_provider")

        thinking_param = None
        if thinking:
            builder = getattr(self.gateway, "thinking_param_for_agent", None)
            if callable(builder):
                thinking_param = builder(self.writer.get_agent_name(), thinking)
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
            )
        except Exception as exc:
            if str(exc) == "turn_cancelled":
                await close_provisional("cancelled", "turn_cancelled")
                return {"success": False, "cancelled": True, "reason": "turn_cancelled"}
            logger.warning("writing service failed; caller may use fallback: %s", exc)
            await close_provisional("failed", safe_error_code(exc))
            return fallback_result(safe_error_code(exc))

        agent_run = response.to_dict()
        supply_report = dict(request.supply_report)
        retrieved_types = sorted(
            {
                {
                    "lookup_card": "card",
                    "query_canon": "canon",
                    "query_relations": "canon",
                    "read_chapter": "prose",
                    "search_prose": "prose",
                }.get(name, "")
                for name in tools_used["names"]
                if name
            }
            - {""}
        )
        supply_report["retrieved"] = retrieved_types
        supply_report["used"] = sorted(set(supply_report.get("used") or []) | set(retrieved_types))
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
            await close_provisional("incomplete", reason)
            return {
                "success": False,
                "incomplete": True,
                "reason": reason,
                "agent_run": agent_run,
                "context_supply": supply_report,
                "fallback_context": build_fallback_context(
                    reason=reason,
                    agent_run=agent_run,
                    context_supply=supply_report,
                ),
            }
        if not response.success:
            reason = str((response.error or {}).get("code") or response.finish_reason or "agent_run_failed")
            await close_provisional("failed", reason)
            if reason in {"turn_deadline_exceeded", "timeout"}:
                return {"success": False, "reason": reason, "agent_run": agent_run}
            return fallback_result(reason, agent_run)

        if not writing_tools.changed:
            if not tools_used["any"]:
                await close_provisional("fallback", "no_tool_calls")
                return fallback_result("no_tool_calls", agent_run)
            await close_provisional("completed")
            return {
                "success": True,
                "action": "reply",
                "changed": False,
                "message": str(response.get("content") or ""),
                "assembly_fingerprint": request.fingerprint,
                "agent_run": agent_run,
                "context_supply": supply_report,
            }

        proposals = await self._stream_text_as_diff(
            project_id,
            chapter,
            writing_tools.working_text,
            emit_tokens=not stream_state["provisional"],
            stream_started=stream_state["started"],
        )
        return {
            "success": True,
            "action": "agentic_write",
            "changed": True,
            "proposals": proposals,
            "actions": writing_tools.actions,
            "summary": str(response.get("content") or "").strip(),
            "assembly_fingerprint": request.fingerprint,
            "agent_run": agent_run,
            "context_supply": supply_report,
        }

    async def _load_current_text(self, project_id: str, chapter: str) -> str:
        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            if versions:
                latest = await self.draft_storage.get_draft(project_id, chapter, versions[-1])
                return str(getattr(latest, "content", "") or "")
        except Exception:
            return ""
        return ""

    async def _emit_agent_event(self, project_id: str, chapter: str, event: Dict[str, Any]) -> None:
        if not self.progress_callback:
            return
        event_type = event.get("type")
        payload: Optional[Dict[str, Any]] = None
        if event_type == "thinking":
            payload = {
                "type": "agent_thinking",
                "project_id": project_id,
                "chapter": chapter,
                "content": str(event.get("content") or "")[:2000],
            }
        elif event_type == "tool_call":
            payload = {
                "type": "agent_tool_call",
                "project_id": project_id,
                "chapter": chapter,
                "name": event.get("name"),
                "arguments": event.get("arguments"),
            }
        elif event_type == "tool_result":
            payload = {
                "type": "agent_tool_result",
                "project_id": project_id,
                "chapter": chapter,
                "name": event.get("name"),
                "result": str(event.get("result") or "")[:1000],
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
    ) -> List[Dict[str, Any]]:
        text = str(final_text or "")
        if self.progress_callback:
            if not stream_started:
                await self._safe_progress({"type": "stream_start", "project_id": project_id, "chapter": chapter})
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
