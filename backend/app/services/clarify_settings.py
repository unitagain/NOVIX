"""Writer clarification-tool policy resolution.

This module owns configuration only.  It never calls a model and never gates a
turn.  The Writer always receives the normalized ``ask_clarification`` tool;
the resolved policy is injected into the Writer context as a preference for
when the model should consider using that tool.
"""

from __future__ import annotations

from typing import Any, Dict

from app.config import config

_VALID_AUTO_TRIGGERS = {"auto", "always", "off"}
# This is a protocol boundary, not a user preference.  The Writer may choose
# one, two, or three questions; settings only influence how readily it checks
# for a real gap.
MAX_CLARIFICATION_QUESTIONS = 3


def normalize_auto_trigger(value: Any, *, default: str = "auto") -> str:
    """Normalize the setting without turning it into deterministic routing."""

    fallback = str(default or "auto").strip().lower()
    if fallback not in _VALID_AUTO_TRIGGERS:
        fallback = "auto"
    if isinstance(value, bool):
        return "always" if value else "off"
    normalized = str(value or "").strip().lower()
    aliases = {"agentic": "auto", "dynamic": "auto", "true": "always", "false": "off"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _VALID_AUTO_TRIGGERS else fallback


def _global_defaults() -> Dict[str, Any]:
    writer = config.get("writer", {}) or {}
    if not isinstance(writer, dict):
        writer = {}
    policy = writer.get("clarification") or {}
    # Accept the old top-level key while projects migrate; it is only a
    # policy alias and never starts another model call or routing branch.
    legacy = config.get("clarify", {}) or {}
    if not isinstance(legacy, dict):
        legacy = {}
    if not isinstance(policy, dict):
        policy = {}
    trigger = policy.get("auto_trigger", policy.get("mode"))
    if trigger in (None, ""):
        trigger = legacy.get("auto_trigger", legacy.get("mode", "auto"))
    normalized_trigger = normalize_auto_trigger(trigger)
    return {
        "auto_trigger": normalized_trigger,
        # ``mode`` remains an API/UI compatibility alias; it no longer starts
        # a separate workflow.
        "mode": normalized_trigger,
        # Keep the value in the response for older clients, but do not expose
        # it as a setting: question count is selected by the Writer per turn.
        "max_questions": MAX_CLARIFICATION_QUESTIONS,
    }


def resolve_clarify_settings(project_meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """Merge global and project policy for the Writer clarification tool."""

    settings = _global_defaults()
    meta = project_meta if isinstance(project_meta, dict) else {}
    writer = meta.get("writer") if isinstance(meta.get("writer"), dict) else {}
    writer_policy = writer.get("clarification") if isinstance(writer, dict) else None
    legacy_policy = meta.get("clarify")
    # Apply the legacy project key first, then let the namespaced Writer
    # policy win when both are present.  This keeps migrations lossless while
    # preserving one effective policy owner.
    for override in (legacy_policy, writer_policy):
        if not isinstance(override, dict):
            continue
        trigger = override.get("auto_trigger", override.get("mode"))
        if trigger not in (None, ""):
            settings["auto_trigger"] = normalize_auto_trigger(trigger, default=settings["auto_trigger"])
            settings["mode"] = settings["auto_trigger"]
    # ``max_questions`` used to be read from project config.  It is now a
    # fixed 1-3 protocol limit; accepting that key as a preference would make
    # the model's per-turn choice non-autonomous.  Leave the compatibility
    # response at the protocol maximum instead.
    settings["max_questions"] = MAX_CLARIFICATION_QUESTIONS
    return settings


__all__ = ["MAX_CLARIFICATION_QUESTIONS", "normalize_auto_trigger", "resolve_clarify_settings"]
