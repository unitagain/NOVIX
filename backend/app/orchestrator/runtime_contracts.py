"""Typed data contracts and minimal collaborator seams for the main turn path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, MutableMapping, Optional, Protocol, TypedDict

from app.context_engine.turn_scope import TurnScope
from app.schemas.draft import ChapterSummary


class AgentStreamEvent(TypedDict, total=False):
    type: str
    content: str
    source: str
    name: str
    arguments: object
    result: object
    provisional: bool


class WritingResult(TypedDict, total=False):
    success: bool
    terminal_state: str
    incomplete: bool
    cancelled: bool
    reason: str
    action: str
    changed: bool
    partial: bool
    content: str
    message: str
    summary: str
    assembly_fingerprint: str
    agent_run: Dict[str, Any]
    context_supply: Dict[str, Any]
    proposals: List[Dict[str, Any]]
    actions: List[Dict[str, Any]]
    writing_memory: Dict[str, Any]
    turn_effect: Dict[str, Any]
    # 新建章节与自动提交只在对应路径产生；未发生时显式为 None，消费方按 isinstance(dict) 判定。
    chapter_target: Optional[Dict[str, Any]]
    auto_commit: Optional[Dict[str, Any]]
    clarification: Dict[str, Any]
    clarify_decision: str
    questions: List[Dict[str, Any]]


class ChatTurnResult(TypedDict, total=False):
    success: bool
    status: object
    action: str
    changed: bool
    partial: bool
    content: str
    decision: Dict[str, Any]
    route_contract: Dict[str, Any]
    plan: Dict[str, Any]
    execution: Dict[str, Any]
    cancelled: bool
    incomplete: bool
    reason: str
    terminal_state: str
    context_plan: Dict[str, Any]
    trace_ref: str
    runtime: Dict[str, Any]
    writing_memory: Dict[str, Any]
    turn_effect: Dict[str, Any]
    chapter_target: Optional[Dict[str, Any]]
    auto_commit: Optional[Dict[str, Any]]
    clarification: Dict[str, Any]
    clarify_decision: str
    questions: List[Dict[str, Any]]
    clarify_mode: str


ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]
ProposalDetector = Callable[[str, str], Awaitable[List[Dict[str, Any]]]]


class GatewayPort(Protocol):
    def get_provider_for_agent(self, name: str) -> Optional[str]: ...

    def thinking_param_for_agent(
        self, name: str, enabled: bool = False, *, reasoning_level: str = "auto"
    ) -> object: ...


class WriterAgentPort(Protocol):
    def get_agent_name(self) -> str: ...


class DraftStoragePort(Protocol):
    async def get_working_text(self, project_id: str, chapter: str) -> tuple[str, Optional[Path]]: ...

    async def list_chapters(self, project_id: str) -> List[str]: ...

    async def save_current_draft(self, project_id: str, chapter: str, content: str, **kwargs: Any) -> object: ...

    async def get_chapter_summary(self, project_id: str, chapter: str) -> Optional[ChapterSummary]: ...

    async def save_chapter_summary(self, project_id: str, summary: ChapterSummary) -> None: ...


class ContextPlanPort(Protocol):
    budget: Mapping[str, object]
    snapshot: object
    policy: Mapping[str, object]
    project_root: str
    sources: object

    def verify_sources(self) -> Dict[str, Any]: ...

    def validate_request(self, **kwargs: Any) -> Dict[str, Any]: ...


class SessionHistoryPort(Protocol):
    async def current_context_epoch(self, project_id: str) -> int: ...


class RankingTracePort(Protocol):
    def reset_ranking_trace(self) -> None: ...


class ContextPlanningPort(Protocol):
    def prepare_context_plan(self, **kwargs: Any) -> object: ...

    async def attach_chat_context_plan(
        self,
        result: Mapping[str, object],
        **kwargs: Any,
    ) -> ChatTurnResult: ...


class PlansPort(Protocol):
    async def create_plan(self, project_id: str, goal: str, **kwargs: Any) -> Optional[Dict[str, Any]]: ...

    async def execute_plan(self, project_id: str, plan_id: str) -> Dict[str, Any]: ...


class CommandsPort(Protocol):
    async def run(self, **kwargs: Any) -> Dict[str, Any]: ...


class AnalysisPort(Protocol):
    """Turn-effect write-back seam consumed by the chat turn path."""

    async def apply_turn_effect(
        self,
        project_id: str,
        chapter: str,
        turn_effect: Dict[str, Any],
    ) -> Dict[str, Any]: ...


class ApplicationPort(Protocol):
    plans: PlansPort
    commands: CommandsPort
    analysis: AnalysisPort


class WritingServicePort(Protocol):
    async def run(self, project_id: str, chapter: str, message: str, **kwargs: Any) -> WritingResult: ...


class PendingActionPort(Protocol):
    async def list_actions(self, project_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]: ...

    async def create_action(
        self,
        project_id: str,
        *,
        operation: str,
        target: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> Dict[str, Any]: ...

    async def consume_action(
        self,
        project_id: str,
        *,
        action_id: str,
        token: str,
        operation: str,
        target: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...


class ChatTurnOwnerPort(Protocol):
    session_history: SessionHistoryPort
    context_planning_service: ContextPlanningPort
    select_engine: RankingTracePort
    application: ApplicationPort
    writing_service: WritingServicePort
    draft_storage: DraftStoragePort
    gateway: GatewayPort
    writer: WriterAgentPort
    _active_turn_scopes: MutableMapping[str, TurnScope]

    async def decide_writing_action(
        self,
        project_id: str,
        chapter: str,
        message: str,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...
