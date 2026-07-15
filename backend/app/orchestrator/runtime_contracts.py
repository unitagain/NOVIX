"""Typed data contracts and minimal collaborator seams for the main turn path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, MutableMapping, Optional, Protocol, TypedDict

from app.context_engine.turn_scope import TurnScope


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
    fallback: bool
    incomplete: bool
    cancelled: bool
    reason: str
    action: str
    changed: bool
    message: str
    summary: str
    assembly_fingerprint: str
    agent_run: Dict[str, Any]
    context_supply: Dict[str, Any]
    fallback_context: Dict[str, Any]
    proposals: List[Dict[str, Any]]
    actions: List[Dict[str, Any]]


class ChatTurnResult(TypedDict, total=False):
    success: bool
    status: object
    action: str
    decision: Dict[str, Any]
    route_contract: Dict[str, Any]
    plan: Dict[str, Any]
    execution: Dict[str, Any]
    cancelled: bool
    incomplete: bool
    fallback: bool
    fallback_executed: bool
    reason: str
    terminal_state: str
    fallback_context: Dict[str, Any]
    fallback_decision: Dict[str, Any]
    fallback_execution: Dict[str, Any]
    pending_action: Dict[str, Any]
    compatibility: Dict[str, Any]
    context_plan: Dict[str, Any]
    trace_ref: str
    runtime: Dict[str, Any]


ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]
ProposalDetector = Callable[[str, str], Awaitable[List[Dict[str, Any]]]]


class GatewayPort(Protocol):
    def get_provider_for_agent(self, name: str) -> Optional[str]: ...

    def thinking_param_for_agent(self, name: str, enabled: bool) -> object: ...


class WriterAgentPort(Protocol):
    def get_agent_name(self) -> str: ...


class DraftStoragePort(Protocol):
    async def list_draft_versions(self, project_id: str, chapter: str) -> List[str]: ...

    async def get_draft(self, project_id: str, chapter: str, version: str) -> object: ...

    def get_latest_draft_file(self, project_id: str, chapter: str) -> Optional[Path]: ...


class FallbackDraftStoragePort(Protocol):
    async def list_draft_versions(self, project_id: str, chapter: str) -> List[str]: ...

    def get_draft_revision(self, project_id: str, chapter: str, version: str) -> Dict[str, Any]: ...


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


class ApplicationPort(Protocol):
    plans: PlansPort
    commands: CommandsPort


class WritingServicePort(Protocol):
    async def run(self, project_id: str, chapter: str, message: str, **kwargs: Any) -> WritingResult: ...


class FallbackExecutionPort(Protocol):
    async def execute(
        self,
        *,
        project_id: str,
        chapter: str,
        message: str,
        action: str,
        fallback_context: Dict[str, Any],
        target_word_count: int,
        approval_action_id: str = "",
        approval_token: str = "",
    ) -> ChatTurnResult: ...


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


class FallbackOwnerPort(Protocol):
    application: ApplicationPort
    draft_storage: FallbackDraftStoragePort

    async def start_session(
        self,
        *,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int = 3000,
    ) -> Dict[str, Any]: ...

    async def process_feedback(
        self,
        *,
        project_id: str,
        chapter: str,
        feedback: str,
        action: str = "revise",
    ) -> Dict[str, Any]: ...


class ChatTurnOwnerPort(Protocol):
    session_history: SessionHistoryPort
    context_planning_service: ContextPlanningPort
    select_engine: RankingTracePort
    application: ApplicationPort
    writing_service: WritingServicePort
    fallback_execution_service: FallbackExecutionPort
    draft_storage: DraftStoragePort
    _active_turn_scopes: MutableMapping[str, TurnScope]

    async def decide_writing_action(
        self,
        project_id: str,
        chapter: str,
        message: str,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...
