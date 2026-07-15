# -*- coding: utf-8 -*-
"""T0 representative scenarios owned by the deterministic Golden Replay suite.

The scenarios use synthetic inputs and production components.  They intentionally
avoid prose-quality judging and never expose prompt, query, or manuscript text in
their result payloads.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.agents.agentic import run_agentic_chat
from app.agents.fallback_policy import build_fallback_context
from app.agents.runtime_result import AgentRunStatus
from app.agents.writing_actions import WritingActionToolset
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.tool_artifact import ToolArtifactStore, ToolExecutionStatus
from app.eval.retrieval_eval import evaluate_retrieval_recall
from app.llm_gateway.capabilities import CapabilityNegotiator
from app.llm_gateway.providers.base import BaseLLMProvider
from app.orchestrator.architecture import route_contract
from app.schemas.canon import Fact
from app.storage.creative_memory import CreativeMemoryStorage
from app.utils.permissions import decide_permission


@dataclass(frozen=True)
class ScenarioManifest:
    """Content-free decision contract for one representative backend scenario."""

    id: str
    layer: str
    capability: str
    required_sources: Tuple[str, ...]
    acceptable_sources: Tuple[str, ...]
    edit_target: str
    allowed_terminal_states: Tuple[str, ...]
    permission_boundary: str
    fallback_allowed: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SCENARIO_MANIFESTS: Tuple[ScenarioManifest, ...] = (
    ScenarioManifest(
        id="cross-volume-fact",
        layer="deterministic",
        capability="context_retrieval",
        required_sources=("canon.fact.prior_volume",),
        acceptable_sources=("canon.fact.current_volume", "chapter.summary"),
        edit_target="none",
        allowed_terminal_states=("completed",),
        permission_boundary="read_only",
        fallback_allowed=False,
    ),
    ScenarioManifest(
        id="style-constraint-source",
        layer="deterministic",
        capability="memory_retrieval",
        required_sources=("memory.preference",),
        acceptable_sources=("style.card", "user.message"),
        edit_target="none",
        allowed_terminal_states=("completed",),
        permission_boundary="read_only",
        fallback_allowed=False,
    ),
    ScenarioManifest(
        id="edit-head",
        layer="deterministic",
        capability="precise_edit",
        required_sources=("draft",),
        acceptable_sources=("selection", "user.message"),
        edit_target="head",
        allowed_terminal_states=("completed",),
        permission_boundary="write_requires_approval",
        fallback_allowed=False,
    ),
    ScenarioManifest(
        id="edit-middle",
        layer="deterministic",
        capability="precise_edit",
        required_sources=("draft",),
        acceptable_sources=("selection", "user.message"),
        edit_target="middle",
        allowed_terminal_states=("completed",),
        permission_boundary="write_requires_approval",
        fallback_allowed=False,
    ),
    ScenarioManifest(
        id="edit-tail",
        layer="deterministic",
        capability="precise_edit",
        required_sources=("draft",),
        acceptable_sources=("selection", "user.message"),
        edit_target="tail",
        allowed_terminal_states=("completed",),
        permission_boundary="write_requires_approval",
        fallback_allowed=False,
    ),
    ScenarioManifest(
        id="tool-failure-recovery",
        layer="deterministic",
        capability="agent_runtime",
        required_sources=("tool.result.metadata",),
        acceptable_sources=("tool.artifact.ref",),
        edit_target="none",
        allowed_terminal_states=("completed", "failed"),
        permission_boundary="tool_policy",
        fallback_allowed=True,
    ),
    ScenarioManifest(
        id="iteration-limit",
        layer="deterministic",
        capability="agent_runtime",
        required_sources=("agent.runtime.state",),
        acceptable_sources=("tool.result.metadata",),
        edit_target="none",
        allowed_terminal_states=("incomplete",),
        permission_boundary="tool_policy",
        fallback_allowed=True,
    ),
    ScenarioManifest(
        id="fallback-classification",
        layer="deterministic",
        capability="fallback_contract",
        required_sources=("fallback.context",),
        acceptable_sources=("context.supply.report", "tool.artifact.ref"),
        edit_target="middle",
        allowed_terminal_states=("incomplete",),
        permission_boundary="write_requires_approval",
        fallback_allowed=True,
    ),
    ScenarioManifest(
        id="provider-capability-degradation",
        layer="provider_optional",
        capability="provider_capability",
        required_sources=("provider.capability.profile",),
        acceptable_sources=("provider.usage",),
        edit_target="none",
        allowed_terminal_states=("degraded",),
        permission_boundary="no_network",
        fallback_allowed=True,
    ),
    ScenarioManifest(
        id="permission-boundary",
        layer="deterministic",
        capability="permission_contract",
        required_sources=("permission.policy",),
        acceptable_sources=("trust.context", "parent.restriction"),
        edit_target="none",
        allowed_terminal_states=("requires_approval",),
        permission_boundary="allow_ask_deny",
        fallback_allowed=False,
    ),
)


EVAL_ASSET_INVENTORY: Tuple[Dict[str, Any], ...] = (
    {
        "id": "golden_replay",
        "owner": "app.eval.golden_replay",
        "layer": "deterministic",
        "coverage": "covered",
        "evidence": ("component", "route", "trace", "representative_scenario"),
    },
    {
        "id": "retrieval_eval",
        "owner": "app.eval.retrieval_eval",
        "layer": "deterministic",
        "coverage": "covered",
        "evidence": ("recall", "ranking_trace", "latency_diagnostic"),
    },
    {
        "id": "trace_replay",
        "owner": "app.eval.trace_replay",
        "layer": "deterministic",
        "coverage": "covered",
        "evidence": ("terminal_route", "fallback", "token", "latency", "tool_failure"),
    },
    {
        "id": "usage_diagnostics",
        "owner": "app.observability.usage_diagnostics",
        "layer": "local_observation",
        "coverage": "partial",
        "evidence": ("terminal_state", "fallback", "source_usage", "provider_usage"),
    },
    {
        "id": "longform_provider_eval",
        "owner": "app.eval.longform_benchmark",
        "layer": "provider_optional",
        "coverage": "partial",
        "evidence": ("retrieval", "context_ab", "judge_diagnostic"),
    },
)


class _FactStorage:
    def __init__(self, facts: List[Fact]) -> None:
        self.facts = facts

    async def get_all_facts(self, project_id: str) -> List[Fact]:
        del project_id
        return list(self.facts)


class _ScriptedGateway:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        del messages, kwargs
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return dict(response)


class _ScenarioToolset:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {"name": "lookup", "description": "lookup", "parameters": {}}}]

    @staticmethod
    def is_result_recoverable(name: str) -> bool:
        del name
        return True

    async def execute(self, name: str, arguments: Any) -> str:
        del name, arguments
        if self.fail:
            raise RuntimeError("synthetic_tool_failure")
        return "synthetic-result"


class _CapabilityProvider(BaseLLMProvider):
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del messages, temperature, max_tokens, kwargs
        return {}

    def get_provider_name(self) -> str:
        return "gemini"


def _tool_response() -> Dict[str, Any]:
    return {
        "provider": "openai",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "lookup", "arguments": "{}"}],
        "finish_reason": "tool_calls",
    }


def _final_response() -> Dict[str, Any]:
    return {"provider": "openai", "content": "done", "tool_calls": None, "finish_reason": "stop"}


def _diagnostics(
    manifest: ScenarioManifest,
    *,
    observed_sources: Tuple[str, ...],
    terminal_state: str,
    edit_changed: bool | None = None,
    fallback_category: str = "none",
    degradation: Tuple[str, ...] = (),
    permission: str = "not_applicable",
) -> Dict[str, Any]:
    required = set(manifest.required_sources)
    observed = set(observed_sources)
    accepted = required | set(manifest.acceptable_sources)
    missing = sorted(required - observed)
    unexpected = sorted(observed - accepted)
    return {
        "critical_source_coverage": {
            "required_count": len(required),
            "observed_count": len(required & observed),
            "missing": missing,
            "ratio": (len(required & observed) / len(required)) if required else 1.0,
        },
        "source_overfetch": {"unexpected_types": unexpected, "count": len(unexpected)},
        "edit_target_outcome": {
            "target": manifest.edit_target,
            "changed": edit_changed,
            "status": "observed" if edit_changed is not None else "not_applicable",
        },
        "terminal_state": terminal_state,
        "fallback_category": fallback_category,
        "token": {"status": "insufficient_evidence"},
        "latency": {"status": "insufficient_evidence"},
        "degradation": list(degradation),
        "permission": permission,
    }


def _result(
    manifest: ScenarioManifest,
    *,
    passed: bool,
    evidence_status: str,
    diagnostics: Dict[str, Any],
    failure: str = "",
) -> Dict[str, Any]:
    return {
        "id": manifest.id,
        "passed": bool(passed),
        "evidence_status": evidence_status,
        "manifest": manifest.to_dict(),
        "diagnostics": diagnostics,
        "failure": "" if passed else failure,
    }


async def _cross_volume_fact(manifest: ScenarioManifest) -> Dict[str, Any]:
    facts = [
        Fact(id="V1-F1", statement="旧港的钟楼只在退潮时开启", source="V1C003", introduced_in="V1C003"),
        Fact(id="V2-F1", statement="北塔在正午封闭", source="V2C001", introduced_in="V2C001"),
    ]
    report = await evaluate_retrieval_recall(
        ContextSelectEngine(),
        _FactStorage(facts),
        [{"query": "旧港钟楼何时开启", "expect": ["V1-F1"], "current_chapter": "V2C003"}],
        top_k=2,
    )
    passed = report.get("recall") == 1.0
    return _result(
        manifest,
        passed=passed,
        evidence_status="covered",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("canon.fact.prior_volume",) if passed else (),
            terminal_state="completed",
            permission="allow",
        ),
        failure="prior-volume fact was not retrieved",
    )


async def _style_constraint_source(manifest: ScenarioManifest) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        storage = CreativeMemoryStorage(tmp)
        await storage.write_memory(
            "eval",
            "dialogue-style",
            "偏好短句对白",
            "对白保持简短并用动作承接。",
            "preference",
        )
        recalled = await storage.recall("eval", "对白风格", top_k=3)
    passed = any(item.get("slug") == "dialogue-style" for item in recalled)
    return _result(
        manifest,
        passed=passed,
        evidence_status="partial",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("memory.preference",) if passed else (),
            terminal_state="completed",
            permission="allow",
        ),
        failure="style preference source was not recalled",
    )


async def _edit_target(manifest: ScenarioManifest, old: str, new: str) -> Dict[str, Any]:
    original = "头部\n中部\n尾部"
    toolset = WritingActionToolset(original)
    await toolset.execute("edit_lines", {"old_text": old, "new_text": new})
    changed = toolset.changed and toolset.working_text == original.replace(old, new, 1) and len(toolset.actions) == 1
    return _result(
        manifest,
        passed=changed,
        evidence_status="covered",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("draft",),
            terminal_state="completed",
            edit_changed=changed,
            permission="ask",
        ),
        failure=f"{manifest.edit_target} edit target did not change exactly once",
    )


async def _tool_failure(manifest: ScenarioManifest) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        result = await run_agentic_chat(
            _ScriptedGateway([_tool_response(), _final_response()]),
            "openai",
            [{"role": "user", "content": "synthetic"}],
            _ScenarioToolset(fail=True),
            artifact_store=ToolArtifactStore(Path(tmp)),
        )
    failed_tool = bool(result.tool_results) and result.tool_results[0].status == ToolExecutionStatus.FAILED.value
    passed = result.status == AgentRunStatus.COMPLETED.value and failed_tool
    return _result(
        manifest,
        passed=passed,
        evidence_status="covered",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("tool.result.metadata",),
            terminal_state=result.status,
            degradation=("tool_failure",),
            permission="tool_policy_applied",
        ),
        failure="tool failure did not preserve a typed terminal result",
    )


async def _iteration_limit(manifest: ScenarioManifest) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        gateway = _ScriptedGateway([_tool_response()])
        result = await run_agentic_chat(
            gateway,
            "openai",
            [{"role": "user", "content": "synthetic"}],
            _ScenarioToolset(),
            max_iterations=2,
            artifact_store=ToolArtifactStore(Path(tmp)),
        )
    passed = (
        result.status == AgentRunStatus.INCOMPLETE.value
        and result.finish_reason == "max_iterations"
        and gateway.calls == 2
    )
    return _result(
        manifest,
        passed=passed,
        evidence_status="covered",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("agent.runtime.state", "tool.result.metadata"),
            terminal_state=result.status,
            fallback_category="iteration_limit",
            permission="tool_policy_applied",
        ),
        failure="iteration limit did not stop with incomplete terminal state",
    )


async def _fallback_classification(manifest: ScenarioManifest) -> Dict[str, Any]:
    context = build_fallback_context(
        reason="no_tool_calls",
        agent_run={"iterations": 1, "tool_results": [], "degradations": []},
        context_supply={"used": ["draft"]},
    )
    contract = route_contract("edit", fallback=True, auto_execute_plan=False)
    passed = context.get("category") == "no_tool_calls" and contract.get("path") == "fallback_workflow"
    return _result(
        manifest,
        passed=passed,
        evidence_status="partial",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("fallback.context", "context.supply.report"),
            terminal_state="incomplete",
            fallback_category=str(context.get("category") or "unknown"),
            permission="ask",
        ),
        failure="fallback reason or route classification regressed",
    )


async def _provider_degradation(manifest: ScenarioManifest) -> Dict[str, Any]:
    provider = _CapabilityProvider(api_key="synthetic", model="synthetic")
    negotiated = CapabilityNegotiator().negotiate(provider, {"response_format": {"type": "json_object"}})
    degradation = negotiated.get("degradation") or []
    passed = bool(degradation) and degradation[0].get("status") == "prompt_fallback"
    return _result(
        manifest,
        passed=passed,
        evidence_status="partial",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("provider.capability.profile",),
            terminal_state="degraded",
            fallback_category="capability",
            degradation=("json_mode:prompt_fallback",),
            permission="no_network",
        ),
        failure="unsupported provider capability did not emit explicit degradation",
    )


async def _permission_boundary(manifest: ScenarioManifest) -> Dict[str, Any]:
    read = decide_permission("query_canon")
    write = decide_permission("write_content", trust_context={"consumed_untrusted": True})
    delete = decide_permission("delete_project")
    levels = (read.level.value, write.level.value, delete.level.value)
    passed = levels == ("allow", "ask", "deny")
    return _result(
        manifest,
        passed=passed,
        evidence_status="covered",
        diagnostics=_diagnostics(
            manifest,
            observed_sources=("permission.policy", "trust.context"),
            terminal_state="requires_approval",
            permission="allow_ask_deny",
        ),
        failure="permission precedence did not preserve allow/ask/deny boundary",
    )


async def evaluate_representative_scenarios() -> List[Dict[str, Any]]:
    """Evaluate T0 scenarios as part of the existing Golden Replay runner."""

    manifests = {manifest.id: manifest for manifest in SCENARIO_MANIFESTS}
    return [
        await _cross_volume_fact(manifests["cross-volume-fact"]),
        await _style_constraint_source(manifests["style-constraint-source"]),
        await _edit_target(manifests["edit-head"], "头部", "新头部"),
        await _edit_target(manifests["edit-middle"], "中部", "新中部"),
        await _edit_target(manifests["edit-tail"], "尾部", "新尾部"),
        await _tool_failure(manifests["tool-failure-recovery"]),
        await _iteration_limit(manifests["iteration-limit"]),
        await _fallback_classification(manifests["fallback-classification"]),
        await _provider_degradation(manifests["provider-capability-degradation"]),
        await _permission_boundary(manifests["permission-boundary"]),
    ]
