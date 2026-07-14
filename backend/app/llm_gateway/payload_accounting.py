"""Final provider payload accounting and lossless overflow policy."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.context_engine.token_accounting import fold_payload_to_budget
from app.context_engine.token_counter import get_model_context_window
from app.context_engine.turn_scope import current_turn_scope
from app.llm_gateway.providers.base import BaseLLMProvider


class PayloadAccountingPort:
    def prepare(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        target_provider: BaseLLMProvider,
        max_tokens: Optional[int],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], str]:
        provider_name = str(target_provider.get_provider_name() or "")
        model = str(getattr(target_provider, "model", "") or "")
        context_limit = get_model_context_window(model)
        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            verification = scope.source_registry.verify_payload(messages, tools) if scope.source_registry else {}
            if verification.get("valid") is not True:
                missing = (verification.get("missing") or [{}])[0]
                raise RuntimeError(
                    f"context_unregistered_payload:{missing.get('kind') or 'source'}:{missing.get('index', '')}"
                )
        if scope is not None and scope.active_plan is not None:
            planned_budget = getattr(scope.active_plan, "budget", {}) or {}
            context_limit = int(planned_budget.get("context_limit_tokens") or context_limit)
            input_budget = int(planned_budget.get("input_tokens") or 0)
        else:
            reserve = int(max_tokens or getattr(target_provider, "max_tokens", 0) or 0)
            input_budget = max(1, context_limit - reserve)
        fitted, accounting, degradation = fold_payload_to_budget(
            messages,
            tools=tools,
            provider=provider_name,
            model=model,
            budget=input_budget,
        )
        if accounting.upper_bound_tokens > input_budget:
            raise ValueError(f"context_budget_exceeded:{accounting.upper_bound_tokens}>{input_budget}")
        if scope is not None and scope.source_closure_required:
            scope.register_provider_payload(
                fitted,
                tools,
                source_prefix="payload_accounting.final",
                selection_reason="validated_provider_payload_projection",
                artifact_ref="payload_accounting:final",
            )
        payload = {
            "messages": fitted,
            "tools": tools or [],
            "provider": provider_name,
            "model": model,
            "max_tokens": max_tokens,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return fitted, accounting.to_dict(), degradation, fingerprint
