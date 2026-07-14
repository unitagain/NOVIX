"""Unified provider-aware token accounting and recoverable payload folding.

中文说明：本模块是 provider payload、writer assembly 与 session compact 的唯一
token accounting contract，并记录 provider usage 对本地 estimator 的校准证据。
"""

from __future__ import annotations

import copy
import json
import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from app.context_engine.token_counter import count_tokens_for_model
from app.context_engine.tool_artifact import tool_artifact_exists


_CALIBRATION_WINDOW = 256
_CALIBRATION_ERRORS: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=_CALIBRATION_WINDOW))
TOOL_RESULT_FOLD_PLACEHOLDER = "[工具结果已折叠；原始结果可由 artifact ref 恢复]"


def tool_result_fold_placeholder(artifact_ref: str) -> str:
    """Render a provider-visible placeholder that retains its recovery ref."""

    return f"{TOOL_RESULT_FOLD_PLACEHOLDER} {artifact_ref}"


def _calibration_key(provider: str, model: str) -> str:
    return f"{str(provider or '').lower()}:{str(model or '').lower()}"


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def token_estimator_calibration(provider: str = "", model: str = "") -> Dict[str, Any]:
    """Return process-local, privacy-safe estimator error evidence."""

    errors = list(_CALIBRATION_ERRORS.get(_calibration_key(provider, model), ()))
    return {
        "samples": len(errors),
        "p50_relative_error": _percentile(errors, 0.50),
        "p95_relative_error": _percentile(errors, 0.95),
        "max_relative_error": max(errors) if errors else None,
        "window": _CALIBRATION_WINDOW,
        "policy_adjusted": False,
    }


def record_token_estimator_observation(
    *,
    provider: str,
    model: str,
    estimated_tokens: int,
    actual_tokens: int,
) -> Dict[str, Any]:
    """Record one provider-reported prompt-token observation without storing content."""

    estimated = max(0, int(estimated_tokens or 0))
    actual = max(0, int(actual_tokens or 0))
    if actual > 0:
        error = abs(estimated - actual) / actual
        _CALIBRATION_ERRORS[_calibration_key(provider, model)].append(float(error))
    return token_estimator_calibration(provider, model)


@dataclass(frozen=True)
class TokenAccounting:
    tokens: int
    upper_bound_tokens: int
    tokenizer: str
    exact: bool
    error_bound: float
    provider: str
    model: str
    calibration_samples: int = 0
    observed_p95_relative_error: Optional[float] = None
    calibration_policy_adjusted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _account_text(text: str, *, provider: str = "", model: str = "") -> TokenAccounting:
    provider_name = str(provider or "").lower()
    tokens, tokenizer, tokenizer_exact = count_tokens_for_model(text, model)
    tokens = max(1, tokens) if text else 0
    provider_uses_tiktoken = provider_name in {"openai", "aistudio"}
    error_bound = 0.15 if tokenizer_exact and provider_uses_tiktoken else 0.35
    calibration = token_estimator_calibration(provider_name, model)
    return TokenAccounting(
        tokens=tokens,
        upper_bound_tokens=max(tokens, int(tokens * (1.0 + error_bound) + 0.999)) if tokens else 0,
        tokenizer=tokenizer,
        exact=False,
        error_bound=error_bound,
        provider=provider_name,
        model=str(model or ""),
        calibration_samples=int(calibration["samples"]),
        observed_p95_relative_error=calibration["p95_relative_error"],
        calibration_policy_adjusted=False,
    )


def count_text_tokens(content: Any, *, provider: str = "", model: str = "") -> TokenAccounting:
    """Count one text or structured context section with the shared contract."""

    text = content if isinstance(content, str) else json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _account_text(text, provider=provider, model=model)


def _provider_projection(value: Any) -> Any:
    """Remove internal lineage metadata before counting or provider egress."""

    if isinstance(value, dict):
        return {
            str(key): _provider_projection(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_provider_projection(item) for item in value]
    return value


def count_provider_payload(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    provider: str = "",
    model: str = "",
) -> TokenAccounting:
    """Count the serialized provider projection with an explicit estimator contract."""

    payload = json.dumps(
        {
            "messages": _provider_projection(messages or []),
            "tools": _provider_projection(tools or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _account_text(payload, provider=provider, model=model)


def fold_payload_to_budget(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]],
    provider: str,
    model: str,
    budget: int,
) -> Tuple[List[Dict[str, Any]], TokenAccounting, List[Dict[str, Any]]]:
    """Fold only explicitly recoverable old tool results; never truncate prose."""

    fitted = copy.deepcopy(messages or [])
    accounting = count_provider_payload(fitted, tools=tools, provider=provider, model=model)
    degradations: List[Dict[str, Any]] = []
    if budget <= 0 or accounting.upper_bound_tokens <= budget:
        return _provider_projection(fitted), accounting, degradations

    tool_positions: List[Tuple[int, Optional[int]]] = []
    for message_index, message in enumerate(fitted):
        if message.get("role") == "tool" and message.get("_recoverable") is True:
            source_ref = str(message.get("_source_ref") or "")
            if tool_artifact_exists(source_ref):
                tool_positions.append((message_index, None))
        elif message.get("role") == "user" and isinstance(message.get("content"), list):
            for block_index, block in enumerate(message["content"]):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("_recoverable") is True
                    and tool_artifact_exists(str(block.get("_source_ref") or ""))
                ):
                    tool_positions.append((message_index, block_index))

    for message_index, position_block_index in tool_positions[:-1]:
        if position_block_index is None:
            original = str(fitted[message_index].get("content") or "")
            source_ref = str(fitted[message_index].get("_source_ref") or "")
            fitted[message_index]["content"] = tool_result_fold_placeholder(source_ref)
        else:
            block = fitted[message_index]["content"][position_block_index]
            original = str(block.get("content") or "")
            source_ref = str(block.get("_source_ref") or "")
            block["content"] = tool_result_fold_placeholder(source_ref)
        if original:
            degradations.append(
                {
                    "type": "tool_result_folding",
                    "message_index": message_index,
                    "original_chars": len(original),
                    "source_ref": source_ref,
                    "recoverable": True,
                }
            )
        accounting = count_provider_payload(fitted, tools=tools, provider=provider, model=model)
        if accounting.upper_bound_tokens <= budget:
            break
    return _provider_projection(fitted), accounting, degradations
