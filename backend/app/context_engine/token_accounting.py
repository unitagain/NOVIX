"""Provider-aware final payload token accounting and deterministic folding."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.context_engine.token_counter import count_tokens_for_model


@dataclass(frozen=True)
class TokenAccounting:
    tokens: int
    upper_bound_tokens: int
    tokenizer: str
    exact: bool
    error_bound: float
    provider: str
    model: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def count_provider_payload(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    provider: str = "",
    model: str = "",
) -> TokenAccounting:
    """Count the exact serialized logical payload with an explicit estimator contract."""

    provider_name = str(provider or "").lower()
    payload = json.dumps(
        {"messages": messages or [], "tools": tools or []},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    tokens, tokenizer, tokenizer_exact = count_tokens_for_model(payload, model)
    tokens = max(1, tokens)
    provider_uses_tiktoken = provider_name in {"openai", "aistudio"}
    # Tokenizer output is exact for the serialized logical payload, but provider
    # chat framing/tool overhead is not observable locally, so the API count is estimated.
    exact = False
    error_bound = 0.15 if tokenizer_exact and provider_uses_tiktoken else 0.35
    return TokenAccounting(
        tokens=tokens,
        upper_bound_tokens=max(tokens, int(tokens * (1.0 + error_bound) + 0.999)),
        tokenizer=tokenizer,
        exact=exact,
        error_bound=error_bound,
        provider=provider_name,
        model=str(model or ""),
    )


def fold_payload_to_budget(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]],
    provider: str,
    model: str,
    budget: int,
) -> Tuple[List[Dict[str, Any]], TokenAccounting, List[Dict[str, Any]]]:
    """Fold only recoverable old tool results; never truncate instructions or prose."""

    fitted = copy.deepcopy(messages or [])
    accounting = count_provider_payload(fitted, tools=tools, provider=provider, model=model)
    degradations: List[Dict[str, Any]] = []
    if budget <= 0 or accounting.upper_bound_tokens <= budget:
        return fitted, accounting, degradations

    tool_positions: List[Tuple[int, Optional[int]]] = []
    for message_index, message in enumerate(fitted):
        if message.get("role") == "tool":
            tool_positions.append((message_index, None))
        elif message.get("role") == "user" and isinstance(message.get("content"), list):
            for block_index, block in enumerate(message["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_positions.append((message_index, block_index))

    for message_index, block_index in tool_positions[:-1]:
        if block_index is None:
            original = str(fitted[message_index].get("content") or "")
            fitted[message_index]["content"] = "[工具结果已折叠；原始结果可由 trace/source ref 恢复]"
        else:
            block = fitted[message_index]["content"][block_index]
            original = str(block.get("content") or "")
            block["content"] = "[工具结果已折叠；原始结果可由 trace/source ref 恢复]"
        if original:
            degradations.append(
                {"type": "tool_result_folding", "message_index": message_index, "original_chars": len(original)}
            )
        accounting = count_provider_payload(fitted, tools=tools, provider=provider, model=model)
        if accounting.upper_bound_tokens <= budget:
            break
    return fitted, accounting, degradations
