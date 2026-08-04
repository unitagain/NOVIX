"""Gateway request trace, egress and stream lifecycle telemetry port."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from app.context_engine.trace_collector import TraceEventType, trace_collector
from app.context_engine.turn_scope import current_turn_scope
from app.observability.runtime_metrics import runtime_metrics
from app.security.egress_context import current_egress_policy
from app.security.egress_ledger import EgressLedger, egress_ledger
from app.utils.logger import get_logger


logger = get_logger(__name__)


class GatewayTelemetryPort:
    def __init__(self, ledger: EgressLedger = egress_ledger) -> None:
        self.ledger = ledger

    async def record_request_plan(
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
        scope = current_turn_scope()
        if scope is not None:
            record = scope.prepare_model_request(
                messages=messages,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                token_accounting=token_accounting,
                capabilities=capabilities,
                degradation=degradation,
                final_payload_fingerprint=final_payload_fingerprint,
            )
        else:
            record = {
                "planned": False,
                "provider": provider,
                "message_count": len(messages or []),
                "max_tokens": max_tokens,
                "tool_names": [],
                "input_tokens": int((token_accounting or {}).get("tokens") or 0),
                "input_upper_bound_tokens": int((token_accounting or {}).get("upper_bound_tokens") or 0),
                "tokenizer": (token_accounting or {}).get("tokenizer"),
                "token_count_exact": bool((token_accounting or {}).get("exact")),
                "token_error_bound": float((token_accounting or {}).get("error_bound") or 0.0),
                "capabilities": dict(capabilities or {}),
                "degradation": list(degradation or []),
                "final_payload_fingerprint": final_payload_fingerprint,
                "request_id": f"req_{uuid.uuid4().hex}",
                "request_fingerprint": hashlib.sha256(
                    json.dumps(
                        {
                            "messages": messages,
                            "provider": provider,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "tools": tools or [],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        trace_snapshot = json.loads(json.dumps(record, ensure_ascii=False, default=str))
        await trace_collector.record(TraceEventType.LLM_REQUEST, "llm_gateway", trace_snapshot)
        return record

    async def record_egress(
        self,
        request_trace: Dict[str, Any],
        *,
        provider_profile: Optional[str],
        provider_name: str,
        messages: List[Dict[str, Any]],
        data_classification: str,
        authorized: bool,
        status: str,
    ) -> None:
        try:
            scope = current_turn_scope()
            policy = current_egress_policy()
            await self.ledger.record(
                {
                    "project_id": scope.project_id if scope else None,
                    "corpus_id": policy.corpus_id if policy else None,
                    "data_classification": policy.data_classification if policy else data_classification,
                    "authorized": bool(authorized and (policy.authorized if policy else True)),
                    "redaction": policy.redaction if policy else "content_not_logged",
                    "provider_profile": provider_profile,
                    "provider": provider_name,
                    "request_id": request_trace.get("request_id"),
                    "request_fingerprint": request_trace.get("request_fingerprint"),
                    "payload_bytes": len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8")),
                    "status": status,
                }
            )
            await trace_collector.record(
                TraceEventType.EGRESS,
                "llm_gateway",
                {
                    "provider": provider_name,
                    "request_id": request_trace.get("request_id"),
                    "request_fingerprint": request_trace.get("request_fingerprint"),
                    "status": status,
                },
            )
        except Exception as exc:
            logger.warning("Egress telemetry failed: %s", exc)

    @staticmethod
    async def close_provider_stream(stream: Any) -> None:
        close_stream = getattr(stream, "aclose", None)
        if not callable(close_stream):
            return
        try:
            await close_stream()
        except Exception as exc:
            runtime_metrics.increment("gateway.stream_close_failure")
            logger.warning("Provider stream cleanup failed: %s", exc)
