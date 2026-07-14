# -*- coding: utf-8 -*-
"""Frozen context execution contract for one runtime turn."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.context_engine.source_snapshot import SourceDescriptor, capture_source_snapshot, verify_source_snapshot
from app.context_engine.token_counter import get_model_context_window
from app.context_engine.tool_registry import tool_loadout_for_route


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _tool_names(tools: Optional[List[Dict[str, Any]]]) -> List[str]:
    names: List[str] = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = (function or {}).get("name") if isinstance(function, dict) else None
        name = name or (tool.get("name") if isinstance(tool, dict) else None)
        if name:
            names.append(str(name))
    return names


@dataclass(frozen=True)
class ContextPlanV2:
    """Deep-frozen executable context contract for one runtime route."""

    plan_id: str
    turn_id: str
    project_id: str
    chapter_id: str
    intent: str
    route_path: str
    context_epoch: str
    snapshot: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    provider: Mapping[str, Any] = field(default_factory=dict)
    budget: Mapping[str, int] = field(default_factory=dict)
    sources: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    policy: Mapping[str, Any] = field(default_factory=dict)
    ranking: Mapping[str, Any] = field(default_factory=dict)
    trust: Mapping[str, Any] = field(default_factory=dict)
    tool_loadout: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    degradation: Tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    version_refs: Tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    fingerprints: Mapping[str, str] = field(default_factory=dict)
    project_root: str = field(default="", repr=False, compare=False)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        for name in (
            "snapshot",
            "provider",
            "budget",
            "sources",
            "policy",
            "ranking",
            "trust",
            "tool_loadout",
            "degradation",
            "version_refs",
            "fingerprints",
        ):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    @property
    def allowed_tool_names(self) -> set[str]:
        return {str(item.get("name")) for item in self.tool_loadout if item.get("name")}

    def validate_request(
        self,
        *,
        messages: List[Dict[str, Any]],
        provider: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        token_accounting: Optional[Dict[str, Any]] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        degradation: Optional[List[Dict[str, Any]]] = None,
        final_payload_fingerprint: str = "",
    ) -> Dict[str, Any]:
        """Validate a final provider payload without mutating the frozen plan."""

        accounting = dict(token_accounting or {})
        estimated = int(accounting.get("upper_bound_tokens") or accounting.get("tokens") or 0)
        input_budget = int(self.budget.get("input_tokens") or 0)
        if input_budget > 0 and estimated > input_budget:
            raise ValueError(f"context_budget_exceeded:{estimated}>{input_budget}")

        requested_tools = _tool_names(tools)
        disallowed = sorted(set(requested_tools) - self.allowed_tool_names)
        if disallowed:
            raise PermissionError(f"context_plan_disallowed_tools:{','.join(disallowed)}")

        output_reserve = int(self.budget.get("output_reserve_tokens") or 0)
        if max_tokens and output_reserve > 0 and int(max_tokens) > output_reserve:
            raise ValueError(f"output_budget_exceeded:{int(max_tokens)}>{output_reserve}")

        planned_profile = str(self.provider.get("profile_id") or "")
        if planned_profile and provider and planned_profile != str(provider):
            raise ValueError(f"context_provider_mismatch:{provider}!={planned_profile}")

        payload = {
            "plan_id": self.plan_id,
            "planned": True,
            "provider": provider,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "message_count": len(messages or []),
            "input_tokens": int(accounting.get("tokens") or 0),
            "input_upper_bound_tokens": estimated,
            "tokenizer": accounting.get("tokenizer"),
            "token_count_exact": bool(accounting.get("exact")),
            "token_error_bound": float(accounting.get("error_bound") or 0.0),
            "tool_names": requested_tools,
            "prompt_fingerprint": _stable_hash(messages),
            "tools_fingerprint": _stable_hash(tools or []),
            "final_payload_fingerprint": final_payload_fingerprint,
            "capabilities": dict(capabilities or {}),
            "degradation": list(degradation or []),
            "model_config_fingerprint": _stable_hash(
                {"provider": provider, "temperature": temperature, "max_tokens": max_tokens}
            ),
        }
        payload["request_fingerprint"] = _stable_hash(payload)
        return payload

    def verify_sources(self) -> Dict[str, Any]:
        if not self.project_root:
            return {
                "valid": True,
                "checked": 0,
                "degraded": True,
                "reason": "source_verification_unavailable_for_legacy_plan",
                "failures": [],
            }
        if not self.snapshot:
            return {"valid": True, "checked": 0, "failures": []}
        return verify_source_snapshot(Path(self.project_root), (_thaw(item) for item in self.snapshot))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "plan_id": self.plan_id,
            "turn_id": self.turn_id,
            "project_id": self.project_id,
            "chapter_id": self.chapter_id,
            "intent": self.intent,
            "route_path": self.route_path,
            "context_epoch": self.context_epoch,
            "snapshot": _thaw(self.snapshot),
            "provider": _thaw(self.provider),
            "budget": _thaw(self.budget),
            "sources": _thaw(self.sources),
            "policy": _thaw(self.policy),
            "ranking": _thaw(self.ranking),
            "trust": _thaw(self.trust),
            "tool_loadout": _thaw(self.tool_loadout),
            "degradation": _thaw(self.degradation),
            "version_refs": _thaw(self.version_refs),
            "fingerprints": _thaw(self.fingerprints),
            "created_at": self.created_at,
        }


def snapshot_source_revisions(project_root: Path, *, chapter_id: str = "") -> List[Dict[str, Any]]:
    """Compatibility wrapper for content-addressed source snapshots."""

    return capture_source_snapshot(project_root, chapter_id=chapter_id)


def build_context_plan_v2(
    *,
    turn_id: str,
    project_id: str,
    chapter_id: str,
    intent: str,
    route_path: str,
    project_root: Path,
    provider_profile: Optional[Dict[str, Any]] = None,
    target_word_count: int = 3000,
    auto_execute_plan: bool = False,
    fallback_reason: str = "",
    context_epoch: int = 0,
) -> ContextPlanV2:
    profile = dict(provider_profile or {})
    context_limit = int(
        profile.get("context_window")
        or profile.get("context_length")
        or get_model_context_window(str(profile.get("model") or ""))
    )
    output_reserve = max(4096 if route_path == "agentic_writer" else 512, int(target_word_count or 0) * 2)
    output_reserve = min(output_reserve, max(512, context_limit // 2))
    loadout = tool_loadout_for_route(route_path, auto_execute_plan=auto_execute_plan)
    snapshot: List[Dict[str, Any]] = []
    degradation: List[Dict[str, str]] = []
    if fallback_reason:
        degradation.append({"type": "route", "status": "fallback", "reason": fallback_reason})
    sources = _sources_for_route(
        route_path,
        has_draft=bool(chapter_id),
        chapter=chapter_id,
        provider_profile=profile,
        tool_loadout=loadout,
    )
    source_registry_fingerprint = _stable_hash(sources)
    version_refs: List[Dict[str, Any]] = [
        {
            "asset_type": "planned_source_registry",
            "revision": source_registry_fingerprint,
            "context_epoch": max(0, int(context_epoch or 0)),
        },
    ]
    plan_payload = {
        "turn_id": turn_id,
        "context_epoch": context_epoch,
        "route": route_path,
        "snapshot": snapshot,
        "budget": {
            "context_limit_tokens": context_limit,
            "input_tokens": max(1024, context_limit - output_reserve),
            "output_reserve_tokens": output_reserve,
            "tool_schema_tokens": sum(int(item.get("context_cost") or 0) for item in loadout),
        },
        "tools": loadout,
        "policy": {
            "selection": "fresh_context_first_full_canon_or_jit_by_project_size",
            "retrieval": "production_context_select_engine",
            "as_of": chapter_id,
            "compression": "tool_result_folding_then_session_compact",
            "overflow": "fold_recoverable_tool_results_then_reject",
            "source_closure_required": True,
            "source_registry_owner": "app.context_engine.source_snapshot.SourceRegistry",
        },
        "version_refs": version_refs,
    }
    return ContextPlanV2(
        plan_id=f"ctx_{uuid.uuid4().hex}",
        turn_id=turn_id,
        project_id=project_id,
        chapter_id=chapter_id,
        intent=intent,
        route_path=route_path,
        context_epoch=str(max(0, int(context_epoch or 0))),
        snapshot=snapshot,
        provider={
            "profile_id": profile.get("id") or profile.get("profile_id"),
            "provider": profile.get("provider"),
            "model": profile.get("model"),
            "context_limit": context_limit,
            "tokenizer": {
                "strategy": "provider_model_local_then_explicit_estimator",
                "exact_required_when_available": True,
            },
        },
        budget=plan_payload["budget"],
        sources=sources,
        policy=plan_payload["policy"],
        trust={"default": "trusted", "external_egress": "provider_request_only"},
        tool_loadout=loadout,
        degradation=degradation,
        version_refs=version_refs,
        fingerprints={
            "planned_sources": source_registry_fingerprint,
            "plan": _stable_hash(plan_payload),
        },
        project_root=str(Path(project_root).resolve()),
    )


def _sources_for_route(
    route_path: str,
    *,
    has_draft: bool,
    chapter: str,
    provider_profile: Optional[Dict[str, Any]] = None,
    tool_loadout: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    sources: List[SourceDescriptor] = [
        SourceDescriptor.planned(
            source_id="input.user_message",
            asset_type="user_message",
            selection_reason="author_instruction",
            required=True,
        ),
        SourceDescriptor.planned(
            source_id="prompt.runtime",
            asset_type="prompt",
            selection_reason="route_prompt",
            required=True,
        ),
        SourceDescriptor.planned(
            source_id="payload.provider_message",
            asset_type="provider_message",
            selection_reason="provider_payload_fragment",
            required=True,
        ),
        SourceDescriptor.planned(
            source_id="config.project",
            asset_type="project_config",
            selection_reason="runtime_configuration",
        ),
        SourceDescriptor.planned(
            source_id="project.cards",
            asset_type="cards",
            selection_reason="project_setting_context",
        ),
        SourceDescriptor.planned(
            source_id="project.summaries",
            asset_type="summaries",
            selection_reason="long_range_continuity",
        ),
        SourceDescriptor.planned(
            source_id="project.scene_brief",
            asset_type="scene_brief",
            selection_reason="chapter_execution_context",
        ),
        SourceDescriptor.planned(
            source_id="project.volume_order",
            asset_type="volume_order",
            selection_reason="chapter_order_grounding",
        ),
        SourceDescriptor.planned(
            source_id="model.assignment",
            asset_type="model_assignment",
            selection_reason="agent_provider_assignment",
            content=dict(provider_profile or {}),
            artifact_ref="llm_config:agent_assignment",
            required=True,
        ),
    ]
    if chapter:
        sources.append(
            SourceDescriptor.planned(
                source_id="target.chapter",
                asset_type="chapter",
                selection_reason="target_chapter",
                content=chapter,
                required=True,
            )
        )
    if has_draft:
        sources.append(
            SourceDescriptor.planned(
                source_id="draft.current",
                asset_type="draft",
                selection_reason="edit_baseline",
                required=True,
            )
        )

    if route_path == "agentic_writer":
        sources.extend(
            [
                SourceDescriptor.planned(
                    source_id="procedural.writer_tools",
                    asset_type="procedural_knowledge",
                    selection_reason="route_tool_loadout",
                    content=list(tool_loadout or []),
                    artifact_ref="tool_registry:agentic_writer",
                    required=True,
                ),
                SourceDescriptor.planned(
                    source_id="payload.tool_schema",
                    asset_type="tool_schema",
                    selection_reason="provider_tool_contract",
                    required=True,
                ),
                SourceDescriptor.planned(source_id="jit.cards", asset_type="cards", selection_reason="tool_jit"),
                SourceDescriptor.planned(source_id="jit.canon", asset_type="canon", selection_reason="tool_jit"),
                SourceDescriptor.planned(
                    source_id="jit.relations", asset_type="relations", selection_reason="tool_jit"
                ),
                SourceDescriptor.planned(source_id="jit.prose", asset_type="prose", selection_reason="tool_jit"),
                SourceDescriptor.planned(source_id="jit.memory", asset_type="memory", selection_reason="tool_jit"),
                SourceDescriptor.planned(
                    source_id="jit.tool_result", asset_type="tool_result", selection_reason="agentic_replay"
                ),
                SourceDescriptor.planned(
                    source_id="jit.provider_response",
                    asset_type="provider_response",
                    selection_reason="agentic_replay",
                ),
            ]
        )
    elif route_path == "plan_workflow":
        sources.extend(
            [
                SourceDescriptor.planned(
                    source_id="plan.current", asset_type="plan_store", selection_reason="plan_progress", required=True
                ),
                SourceDescriptor.planned(source_id="plan.canon", asset_type="canon", selection_reason="research"),
                SourceDescriptor.planned(
                    source_id="plan.summaries", asset_type="summaries", selection_reason="planning_context"
                ),
                SourceDescriptor.planned(
                    source_id="plan.volume_order", asset_type="volume_order", selection_reason="chapter_grounding"
                ),
            ]
        )
    elif route_path == "fallback_workflow":
        sources.extend(
            [
                SourceDescriptor.planned(
                    source_id="fallback.scene_brief",
                    asset_type="scene_brief",
                    selection_reason="writing_context",
                    required=True,
                ),
                SourceDescriptor.planned(
                    source_id="fallback.summaries", asset_type="summaries", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.cards", asset_type="cards", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.canon", asset_type="canon", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.memory", asset_type="memory", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.volume_order", asset_type="volume_order", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.chapter_goal", asset_type="chapter_goal", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.prose", asset_type="prose", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.evidence", asset_type="evidence", selection_reason="writing_context"
                ),
                SourceDescriptor.planned(
                    source_id="fallback.assembled",
                    asset_type="assembled_context",
                    selection_reason="agent_message_projection",
                ),
            ]
        )
    return [source.to_dict() for source in sources]
