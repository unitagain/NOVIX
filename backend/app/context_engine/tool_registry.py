# -*- coding: utf-8 -*-
"""P8 MCP-like tool registry.

The registry is intentionally metadata-first: it explains which tools are
available, how much context they cost, and what permission level applies. The
actual Writer tool schemas stay in their existing modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from app.utils.permissions import decide_permission


@dataclass(frozen=True)
class ToolSpec:
    """Tool metadata exposed in ContextPlan loadouts."""

    name: str
    description: str
    scope: str
    context_cost: int
    enabled_for: Sequence[str]
    read_only: bool = True
    external: bool = False
    permission: str = ""

    def to_loadout(self, *, consumed_untrusted: bool = False) -> Dict[str, Any]:
        decision = decide_permission(
            self.name,
            resource_scope={"scope": self.scope},
            trust_context={"consumed_untrusted": consumed_untrusted},
            base_permission=self.permission or None,
            read_only=self.read_only,
            external=self.external,
        )
        return {
            "name": self.name,
            "description": self.description,
            "permission": decision.level.value,
            "permission_decision": decision.to_dict(),
            "context_cost": self.context_cost,
            "scope": self.scope,
            "enabled_for": list(self.enabled_for),
            "read_only": self.read_only,
            "external": self.external,
        }


_TOOLS: Dict[str, ToolSpec] = {
    "lookup_card": ToolSpec(
        "lookup_card",
        "Read character or world card details.",
        "project",
        90,
        ("agentic_writer", "plan_workflow", "worker:retrieve", "worker:untrusted_extract"),
    ),
    "query_canon": ToolSpec(
        "query_canon",
        "Search confirmed and reviewable canon facts.",
        "project",
        110,
        ("agentic_writer", "plan_workflow", "worker:retrieve", "worker:untrusted_extract"),
    ),
    "query_relations": ToolSpec(
        "query_relations",
        "Read relation graph evidence.",
        "project",
        90,
        ("agentic_writer", "plan_workflow", "worker:retrieve", "worker:untrusted_extract"),
    ),
    "read_chapter": ToolSpec(
        "read_chapter",
        "Read an existing chapter or draft section.",
        "project",
        120,
        ("agentic_writer", "plan_workflow", "worker:retrieve"),
    ),
    "search_prose": ToolSpec(
        "search_prose",
        "Search prose snippets by lexical or semantic query.",
        "project",
        120,
        ("agentic_writer", "plan_workflow", "worker:retrieve"),
    ),
    "ask_clarification": ToolSpec(
        "ask_clarification",
        "Ask the author one to three Writer-authored clarification questions and pause the turn.",
        "interaction",
        100,
        ("agentic_writer",),
        read_only=True,
    ),
    "read_outline": ToolSpec(
        "read_outline",
        "Read the whole-story planning outline (author intent; never a source of established facts).",
        "outline",
        115,
        ("agentic_writer", "plan_workflow"),
    ),
    "edit_outline": ToolSpec(
        "edit_outline",
        "Edit the whole-story planning outline on author request (revision-bound, never a fact source).",
        "outline",
        130,
        ("agentic_writer",),
        read_only=False,
    ),
    "write_content": ToolSpec(
        "write_content",
        "Propose new prose for the active writing turn.",
        "draft",
        160,
        ("agentic_writer",),
        read_only=False,
        permission="ask",
    ),
    "create_chapter": ToolSpec(
        "create_chapter",
        "Propose a new chapter target for the active Writer turn.",
        "draft",
        110,
        ("agentic_writer",),
        read_only=False,
        permission="ask",
    ),
    "edit_lines": ToolSpec(
        "edit_lines",
        "Propose line-level edits for selected prose.",
        "draft",
        140,
        ("agentic_writer",),
        read_only=False,
        permission="ask",
    ),
    "finish_turn": ToolSpec(
        "finish_turn",
        "Finish the active Writer turn with a structured summary and fact candidates.",
        "turn",
        220,
        ("agentic_writer",),
    ),
    "write_chapter": ToolSpec(
        "write_chapter",
        "Execute an approved plan writing step.",
        "draft",
        160,
        ("plan_workflow",),
        read_only=False,
        permission="ask",
    ),
    "confirm_facts": ToolSpec(
        "confirm_facts",
        "Confirm canon candidates after user review.",
        "canon",
        80,
        ("governance",),
        read_only=False,
        permission="ask",
    ),
    "confirm_memory": ToolSpec(
        "confirm_memory",
        "Confirm memory candidates after user review.",
        "memory",
        80,
        ("governance",),
        read_only=False,
        permission="ask",
    ),
    "fanfiction_search": ToolSpec(
        "fanfiction_search",
        "Fetch or search external fanfiction reference content.",
        "external",
        180,
        ("external_reference",),
        read_only=True,
        external=True,
        permission="ask",
    ),
    "external_file_read": ToolSpec(
        "external_file_read",
        "Read user-selected external files.",
        "external",
        160,
        ("external_reference",),
        read_only=True,
        external=True,
        permission="ask",
    ),
    "third_party_tool": ToolSpec(
        "third_party_tool",
        "Call a third-party tool or API.",
        "external",
        200,
        ("external_reference",),
        read_only=False,
        external=True,
        permission="ask",
    ),
}

_ROUTE_TOOLS = {
    "agentic_writer": (
        "lookup_card",
        "query_canon",
        "query_relations",
        "read_chapter",
        "search_prose",
        "read_outline",
        "edit_outline",
        "ask_clarification",
        "create_chapter",
        "write_content",
        "edit_lines",
        "finish_turn",
    ),
    "plan_workflow": ("lookup_card", "query_canon", "query_relations", "read_chapter", "search_prose", "read_outline"),
    "worker:retrieve": ("lookup_card", "query_canon", "query_relations", "read_chapter", "search_prose"),
    "worker:untrusted_extract": ("lookup_card", "query_canon", "query_relations"),
    "external_reference": ("fanfiction_search", "external_file_read", "third_party_tool"),
}


def get_tool_spec(name: str) -> ToolSpec | None:
    """Return one registered tool spec."""

    return _TOOLS.get(str(name or ""))


def list_tool_specs() -> List[ToolSpec]:
    """Return registered tool specs in deterministic order."""

    return [_TOOLS[name] for name in sorted(_TOOLS)]


def _names_for_route(route_path: str, *, auto_execute_plan: bool = False) -> List[str]:
    names = list(_ROUTE_TOOLS.get(str(route_path or ""), ()))
    if route_path == "plan_workflow" and auto_execute_plan:
        names.append("write_chapter")
    return names


def tool_loadout(
    names: Iterable[str],
    *,
    consumed_untrusted: bool = False,
    read_only_only: bool = False,
) -> List[Dict[str, Any]]:
    """Build an explainable loadout from registered tool names."""

    rows: List[Dict[str, Any]] = []
    seen = set()
    for name in names:
        key = str(name or "")
        if not key or key in seen:
            continue
        seen.add(key)
        spec = get_tool_spec(key)
        if not spec:
            rows.append(
                {
                    "name": key,
                    "description": "Unregistered tool; conservative permission applies.",
                    "permission": decide_permission(
                        key,
                        trust_context={"consumed_untrusted": consumed_untrusted},
                    ).level.value,
                    "context_cost": 120,
                    "scope": "unknown",
                    "enabled_for": [],
                    "read_only": False,
                    "external": False,
                }
            )
            continue
        if read_only_only and not spec.read_only:
            continue
        rows.append(spec.to_loadout(consumed_untrusted=consumed_untrusted))
    return rows


def tool_loadout_for_route(
    route_path: str,
    *,
    auto_execute_plan: bool = False,
    trust_context: Dict[str, Any] | None = None,
    read_only_only: bool = False,
) -> List[Dict[str, Any]]:
    """Return the minimal registered loadout for a runtime route."""

    trust = dict(trust_context or {})
    consumed_untrusted = bool(trust.get("consumed_untrusted") or trust.get("trust_label") == "untrusted")
    names = _names_for_route(str(route_path or ""), auto_execute_plan=auto_execute_plan)
    return tool_loadout(names, consumed_untrusted=consumed_untrusted, read_only_only=read_only_only or consumed_untrusted)


def tool_loadout_summary(loadout: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize context cost and permission surface for trace/eval."""

    permissions: Dict[str, int] = {}
    for item in loadout:
        level = str(item.get("permission") or "ask")
        permissions[level] = permissions.get(level, 0) + 1
    return {
        "tool_count": len(loadout),
        "context_cost": sum(int(item.get("context_cost") or 0) for item in loadout),
        "permissions": permissions,
        "external_tools": [item["name"] for item in loadout if item.get("external")],
        "write_tools": [item["name"] for item in loadout if not item.get("read_only")],
    }
