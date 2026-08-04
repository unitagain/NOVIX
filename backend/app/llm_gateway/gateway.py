"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Unified LLM gateway with retry, provider routing, and usage tracking.
"""

import asyncio
import time
from typing import List, Dict, Any, Optional, Awaitable, Callable
import app.config as app_config
from app.utils.logger import get_logger
from app.services.llm_config_service import llm_config_service
from app.llm_gateway.errors import LLMError, normalize_provider_error, provider_retry_delay
from app.error_contract import DomainError, ErrorCategory, record_degradation
from app.llm_gateway.reliability import GatewayReliabilityController, PersistentIdempotencyRegistry, RequestDeadline
from app.observability.runtime_metrics import runtime_metrics
from app.security.egress_ledger import egress_ledger
from app.llm_gateway.capabilities import CapabilityNegotiator
from app.llm_gateway.payload_accounting import PayloadAccountingPort
from app.llm_gateway.provider_registry import ProviderRegistry
from app.llm_gateway.telemetry import GatewayTelemetryPort
from app.llm_gateway.contracts import ProviderUsage
from app.llm_gateway.providers import BaseLLMProvider

logger = get_logger(__name__)


class LLMGateway:
    """
    Unified LLM gateway with provider management
    统一的大模型网关，支持多提供商管理
    """

    def __init__(self):
        """Initialize gateway from profiles"""
        self.provider_registry = ProviderRegistry()
        self.capability_negotiator = CapabilityNegotiator()
        self.payload_accounting = PayloadAccountingPort()
        self.telemetry = GatewayTelemetryPort(egress_ledger)

        # Retry configuration from config.yaml / 从配置文件加载重试参数
        gw_cfg = app_config.config.get("gateway", {})
        self.max_retries = min(int(gw_cfg.get("max_retries", 5)), 10)  # 硬上限 10，防止无限重试
        self.retry_delays = gw_cfg.get("retry_delays", [1, 2, 4, 8, 16])
        if not isinstance(self.retry_delays, list):
            self.retry_delays = [1, 2, 4, 8, 16]
        self.max_retry_delay = float(gw_cfg.get("max_retry_delay", 60.0))
        self.request_timeout = max(1.0, float(gw_cfg.get("request_timeout_seconds", 120.0)))
        self.reliability = GatewayReliabilityController(
            max_concurrency=int(gw_cfg.get("max_concurrency_per_provider", 4)),
            min_interval_ms=int(gw_cfg.get("min_request_interval_ms", 0)),
            failure_threshold=int(gw_cfg.get("circuit_failure_threshold", 5)),
            cooldown_seconds=float(gw_cfg.get("circuit_cooldown_seconds", 30.0)),
        )

        # Cost tracking / 成本追踪
        self.total_tokens = 0
        self.total_requests = 0

    @property
    def providers(self) -> Dict[str, BaseLLMProvider]:
        return self.provider_registry.providers

    @providers.setter
    def providers(self, value: Dict[str, BaseLLMProvider]) -> None:
        if not hasattr(self, "provider_registry"):
            self.provider_registry = ProviderRegistry.from_providers(value)
        else:
            self.provider_registry.providers = dict(value)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,  # This is now the profile_id!
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry: bool = True,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        response_format: Optional[Dict[str, Any]] = None,
        thinking: Optional[Any] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        idempotency_key: Optional[str] = None,
        data_classification: str = "project_private",
        egress_authorized: bool = True,
    ) -> Dict[str, Any]:
        """
        Send chat request
        Args:
            provider: This is now the PROFILE ID, not just 'openai'
        """
        # If provider is None, fallback to default? Or raise error?
        # In new system, provider ID should be explicit or looked up via agent assignment

        # NOTE: existing code might pass 'openai' string.
        # We should handle backward compatibility or ensure caller passes profile ID.
        # Actually, caller usually passes result of get_provider_for_agent()

        self._ensure_reliability()
        requested_timeout = float(timeout_seconds or self.request_timeout)
        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                requested_timeout = task_execution.bounded_timeout(requested_timeout)
        except ImportError:
            pass
        deadline = RequestDeadline(requested_timeout)
        target_provider = self.provider_registry.resolve(provider)

        # 收集可选生成参数（仅传非 None 的），透传给 provider / collect optional gen params
        options = {
            key: value
            for key, value in (
                ("tools", tools),
                ("tool_choice", tool_choice),
                ("response_format", response_format),
                ("thinking", thinking),
                ("extra_body", extra_body),
            )
            if value is not None
        }

        negotiated = self.capability_negotiator.negotiate(target_provider, options)
        options = negotiated["options"]
        messages, accounting, overflow_degradation, final_payload_fingerprint = self.payload_accounting.prepare(
            messages=messages,
            tools=options.get("tools"),
            target_provider=target_provider,
            max_tokens=max_tokens,
        )
        request_trace = await self.telemetry.record_request_plan(
            messages=messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=options.get("tools"),
            token_accounting=accounting,
            capabilities=negotiated["capabilities"],
            degradation=[*negotiated["degradation"], *overflow_degradation],
            final_payload_fingerprint=final_payload_fingerprint,
        )
        request_trace["capabilities"] = negotiated["capabilities"]
        request_trace["degradation"] = [*negotiated["degradation"], *overflow_degradation]
        resolved_idempotency_key = idempotency_key
        if not resolved_idempotency_key:
            try:
                from app.jobs.durable_queue import current_task_execution

                task_execution = current_task_execution()
                if task_execution is not None:
                    resolved_idempotency_key = task_execution.next_llm_idempotency_key(
                        str(request_trace.get("request_fingerprint") or "")
                    )
            except Exception:
                resolved_idempotency_key = None
        cached = await self.reliability.cached_response(resolved_idempotency_key)
        if cached is not None:
            runtime_metrics.increment("gateway.idempotency_hit")
            cached["idempotency_replayed"] = True
            return cached
        if resolved_idempotency_key:
            persistent = await self.idempotency_registry.begin(
                resolved_idempotency_key, str(request_trace.get("request_fingerprint") or "")
            )
            if persistent.get("_existing") and persistent.get("status") in {"running", "completed"}:
                raise DomainError(
                    code=f"idempotency_result_not_replayable:{persistent.get('status')}",
                    category=ErrorCategory.CONFLICT,
                    retryable=False,
                )
        request_trace["idempotency_key"] = resolved_idempotency_key
        request_trace["timeout_seconds"] = deadline.total_seconds
        request_trace["_deadline"] = deadline
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="attempted",
        )
        if not egress_authorized:
            raise PermissionError("external_egress_not_authorized")
        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                await task_execution.mark_external_side_effect(str(request_trace.get("request_fingerprint") or ""))
        except ImportError:
            pass

        # Execute with retry
        try:
            if retry:
                response = await self._chat_with_retry(
                    target_provider, messages, temperature, max_tokens, options, request_trace=request_trace
                )
            else:
                response = await self._execute_chat(
                    target_provider, messages, temperature, max_tokens, options, request_trace=request_trace
                )
        except asyncio.CancelledError:
            await self.telemetry.record_egress(
                request_trace,
                provider_profile=provider,
                provider_name=target_provider.get_provider_name(),
                messages=messages,
                data_classification=data_classification,
                authorized=egress_authorized,
                status="cancelled_uncertain",
            )
            runtime_metrics.increment("gateway.cancelled")
            raise
        except Exception:
            if resolved_idempotency_key:
                await self.idempotency_registry.fail(
                    resolved_idempotency_key,
                    str(request_trace.get("request_fingerprint") or ""),
                    "provider_request_failed",
                )
            await self.telemetry.record_egress(
                request_trace,
                provider_profile=provider,
                provider_name=target_provider.get_provider_name(),
                messages=messages,
                data_classification=data_classification,
                authorized=egress_authorized,
                status="failed",
            )
            runtime_metrics.increment("gateway.provider_error")
            raise
        await self.reliability.cache_response(resolved_idempotency_key, response)
        if resolved_idempotency_key:
            await self.idempotency_registry.complete(
                resolved_idempotency_key, str(request_trace.get("request_fingerprint") or ""), response
            )
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="completed",
        )
        return response

    async def _chat_with_retry(
        self,
        provider: BaseLLMProvider,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        options: Optional[Dict[str, Any]] = None,
        request_trace: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute chat with intelligent retry based on error classification.
        基于错误分类的智能重试执行聊天。

        - Non-retryable errors (auth, invalid request) fail immediately
        - Retryable errors (timeout, connection, server) use exponential backoff
        """
        last_exception = None

        attempts = max(1, self.max_retries + 1)
        provider_key = f"{provider.get_provider_name()}:{getattr(provider, 'model', '')}"

        def _note_terminal_failure(exc: BaseException) -> None:
            # 熔断本身触发的 open/half_open_busy 不是新的 provider 失败，不再计数（避免自我延长熔断）。
            if str(exc) not in {"provider_circuit_open", "provider_circuit_half_open_busy"}:
                self.reliability.note_failure(provider_key)

        for attempt in range(attempts):
            try:
                return await self._execute_chat(
                    provider, messages, temperature, max_tokens, options, request_trace=request_trace, count_on_failure=False
                )
            except Exception as e:
                last_exception = e
                provider_name = provider.get_provider_name() if hasattr(provider, "get_provider_name") else "unknown"
                contract = normalize_provider_error(e, provider=provider_name)

                # Classify the error
                is_retryable, reason = contract.retryable, contract.category

                if not is_retryable:
                    # Non-retryable error - fail immediately (count one terminal failure)
                    _note_terminal_failure(e)
                    logger.error("LLM non-retryable error (reason=%s): %s", reason, e, exc_info=True)
                    raise LLMError(
                        str(e),
                        provider=provider_name,
                        reason=reason,
                        status_code=getattr(e, "status_code", None),
                        is_retryable=False,
                        original=e,
                        contract=contract,
                    ) from e

                # Retryable error - log and retry with backoff
                logger.warning(
                    "LLM retryable error (attempt=%d/%d, reason=%s): %s", attempt + 1, attempts, reason, e
                )
                runtime_metrics.increment("gateway.retry")
                try:
                    from app.context_engine.trace_collector import TraceEventType, trace_collector

                    await trace_collector.record(
                        TraceEventType.PROVIDER_RETRY,
                        "llm_gateway",
                        {"reason": reason, "attempt": attempt + 1, "request_id": (request_trace or {}).get("request_id")},
                    )
                except Exception as trace_exc:
                    record_degradation("gateway_retry_trace", trace_exc)

                if attempt < attempts - 1:
                    delay = provider_retry_delay(contract, attempt, self.retry_delays, self.max_retry_delay)
                    logger.info("Retrying in %.1f seconds...", delay)
                    deadline = (request_trace or {}).get("_deadline")
                    if isinstance(deadline, RequestDeadline):
                        await deadline.sleep(delay)
                    else:
                        await asyncio.sleep(delay)

        # All retries exhausted
        _note_terminal_failure(last_exception)
        logger.error("LLM request failed after %d attempts: %s", attempts, last_exception)
        runtime_metrics.increment("gateway.retry_exhausted")
        provider_name = provider.get_provider_name() if hasattr(provider, "get_provider_name") else "unknown"
        contract = normalize_provider_error(last_exception, provider=provider_name)
        is_retryable, reason = contract.retryable, contract.category
        raise LLMError(
            str(last_exception),
            provider=provider_name,
            reason=reason,
            status_code=getattr(last_exception, "status_code", None),
            is_retryable=True,
            original=last_exception,
        ) from last_exception

    async def _execute_chat(
        self,
        provider: BaseLLMProvider,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        options: Optional[Dict[str, Any]] = None,
        request_trace: Optional[Dict[str, Any]] = None,
        count_on_failure: bool = True,
    ) -> Dict[str, Any]:
        """Execute single chat request"""
        start_time = time.time()
        provider_key = f"{provider.get_provider_name()}:{getattr(provider, 'model', '')}"
        deadline = (request_trace or {}).get("_deadline")
        lease = await self.reliability.before_request(
            provider_key,
            deadline=deadline if isinstance(deadline, RequestDeadline) else None,
        )
        try:
            from app.observability.otel import telemetry

            with telemetry.span(
                "gen_ai.chat",
                attributes={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": provider.get_provider_name(),
                    "gen_ai.request.model": str(getattr(provider, "model", "") or ""),
                    "wenshape.request_id": str((request_trace or {}).get("request_id") or ""),
                },
            ) as provider_span:
                request = provider.chat(messages, temperature=temperature, max_tokens=max_tokens, **(options or {}))
                if isinstance(deadline, RequestDeadline):
                    response = await deadline.wait_for(request)
                else:
                    timeout = float((request_trace or {}).get("timeout_seconds") or self.request_timeout)
                    response = await asyncio.wait_for(request, timeout=timeout)
                usage = ProviderUsage.from_mapping(
                    response.get("usage"),
                    requests=1,
                    requested_max_tokens=int(max_tokens or getattr(provider, "max_tokens", 0) or 0),
                ).to_dict()
                response["usage"] = usage
                provider_span.set_attribute("gen_ai.response.model", str(response.get("model") or ""))
                provider_span.set_attribute("gen_ai.usage.input_tokens", int(usage.get("prompt_tokens") or 0))
                provider_span.set_attribute("gen_ai.usage.output_tokens", int(usage.get("completion_tokens") or 0))
        except asyncio.CancelledError:
            self.reliability.cancelled(lease)
            raise
        except Exception:
            # 重试上下文里逐次尝试不各自记熔断失败（否则一个请求就顶开熔断）；
            # 由调用方 _chat_with_retry 在请求终态时经 note_failure 计一次。
            if count_on_failure:
                self.reliability.failure(lease)
            else:
                self.reliability.retry_release(lease)
            runtime_metrics.increment("gateway.failure")
            raise
        self.reliability.success(lease)
        elapsed_time = time.time() - start_time

        self.total_requests += 1
        self.total_tokens += response.get("usage", {}).get("total_tokens", 0)

        response["provider"] = provider.get_provider_name()
        response["elapsed_time"] = elapsed_time
        response["request_id"] = (request_trace or {}).get("request_id")
        response["request_fingerprint"] = (request_trace or {}).get("request_fingerprint")
        response["capabilities"] = (request_trace or {}).get("capabilities") or {}
        response["degradation"] = (request_trace or {}).get("degradation") or []
        try:
            from app.context_engine.turn_scope import current_turn_scope

            scope = current_turn_scope()
            if scope is not None and scope.turn_trace is not None:
                scope.turn_trace.record_model_response(
                    str((request_trace or {}).get("request_id") or ""),
                    dict(response.get("usage") or {}),
                    provider=str(response.get("provider") or ""),
                    model=str(response.get("model") or ""),
                )
        except Exception as exc:
            logger.warning("Token reconciliation record failed: %s", exc)
        runtime_metrics.increment("gateway.success")
        runtime_metrics.observe("gateway.latency_ms", elapsed_time * 1000.0)
        runtime_metrics.increment("gateway.tokens", float((response.get("usage") or {}).get("total_tokens") or 0))
        try:
            from app.observability.usage_diagnostics import record_provider_usage

            record_provider_usage(dict(response.get("usage") or {}))
        except Exception as metric_exc:
            record_degradation("gateway_usage_diagnostics", metric_exc)
        try:
            usage = response.get("usage", {})
            logger.info(
                "LLM chat completed provider=%s model=%s elapsed_ms=%s prompt_tokens=%s completion_tokens=%s",
                response.get("provider"),
                response.get("model"),
                int(elapsed_time * 1000),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )
        except Exception as metric_exc:
            record_degradation("gateway_cache_metrics", metric_exc)
        try:
            from app.context_engine.trace_collector import TraceEventType, trace_collector

            usage = response.get("usage", {}) or {}
            await trace_collector.record(
                TraceEventType.LLM_RESPONSE,
                "llm_gateway",
                {
                    "provider": response.get("provider"),
                    "model": response.get("model"),
                    "usage": {
                        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                        "completion_tokens": int(usage.get("completion_tokens") or 0),
                        "total_tokens": int(usage.get("total_tokens") or 0),
                    },
                    "latency_ms": int(elapsed_time * 1000),
                    "message_count": len(messages or []),
                    "has_tools": bool((options or {}).get("tools")),
                    "response_format": (options or {}).get("response_format"),
                    "request_id": (request_trace or {}).get("request_id"),
                    "plan_id": (request_trace or {}).get("plan_id"),
                    "request_fingerprint": (request_trace or {}).get("request_fingerprint"),
                },
            )
        except Exception as exc:
            logger.warning("TRACE ERROR (LLM gateway): %s", exc)
        return response

    def get_stats(self) -> Dict[str, Any]:
        """Get gateway statistics"""
        return {
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "profiles_loaded": list(self.providers.keys()),
            "reliability": self.reliability.snapshot(),
        }

    def _ensure_reliability(self) -> None:
        if not hasattr(self, "request_timeout"):
            self.request_timeout = 120.0
        if not hasattr(self, "reliability"):
            self.reliability = GatewayReliabilityController()
        if not hasattr(self, "idempotency_registry"):
            self.idempotency_registry = PersistentIdempotencyRegistry()
        if not hasattr(self, "capability_negotiator"):
            self.capability_negotiator = CapabilityNegotiator()
        if not hasattr(self, "payload_accounting"):
            self.payload_accounting = PayloadAccountingPort()
        if not hasattr(self, "telemetry"):
            self.telemetry = GatewayTelemetryPort(egress_ledger)

    async def agentic_chat(
        self,
        messages: List[Dict[str, Any]],
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        thinking: Optional[Any] = None,
        timeout_seconds: Optional[float] = None,
        on_stream_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        data_classification: str = "project_private",
        egress_authorized: bool = True,
    ) -> Dict[str, Any]:
        """Execute one agent iteration with native tool-delta streaming when available."""
        self._ensure_reliability()
        target_provider = self.provider_registry.resolve(provider)
        if not target_provider.supports_agentic_stream():
            if on_stream_event is not None:
                await on_stream_event(
                    {
                        "type": "stream_degradation",
                        "reason": "provider_agentic_stream_unavailable",
                        "provider": target_provider.get_provider_name(),
                    }
                )
            return await self.chat(
                messages,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                thinking=thinking,
                timeout_seconds=timeout_seconds,
                data_classification=data_classification,
                egress_authorized=egress_authorized,
            )

        requested_timeout = float(timeout_seconds or self.request_timeout)
        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                requested_timeout = task_execution.bounded_timeout(requested_timeout)
        except ImportError:
            pass
        deadline = RequestDeadline(requested_timeout)
        options = {
            key: value
            for key, value in (("tools", tools), ("tool_choice", tool_choice), ("thinking", thinking))
            if value is not None
        }
        negotiated = self.capability_negotiator.negotiate(target_provider, options)
        options = negotiated["options"]
        tools = options.get("tools")
        tool_choice = options.get("tool_choice")
        thinking = options.get("thinking")
        prepared_messages, accounting, degradation, final_payload_fingerprint = self.payload_accounting.prepare(
            messages=messages,
            tools=tools,
            target_provider=target_provider,
            max_tokens=max_tokens,
        )
        request_trace = await self.telemetry.record_request_plan(
            messages=prepared_messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            token_accounting=accounting,
            capabilities={**negotiated["capabilities"], "agentic_stream": True},
            degradation=[*negotiated["degradation"], *degradation],
            final_payload_fingerprint=final_payload_fingerprint,
        )
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=prepared_messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="attempted",
        )
        if not egress_authorized:
            raise PermissionError("external_egress_not_authorized")
        request_trace["timeout_seconds"] = deadline.total_seconds
        request_trace["_deadline"] = deadline

        async def record_cancelled_egress() -> None:
            await self.telemetry.record_egress(
                request_trace,
                provider_profile=provider,
                provider_name=target_provider.get_provider_name(),
                messages=prepared_messages,
                data_classification=data_classification,
                authorized=egress_authorized,
                status="cancelled_uncertain",
            )
            runtime_metrics.increment("gateway.agentic_stream_cancelled")

        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                await task_execution.mark_external_side_effect(str(request_trace.get("request_fingerprint") or ""))
        except ImportError:
            pass
        except asyncio.CancelledError:
            await record_cancelled_egress()
            raise

        try:
            lease = await self.reliability.before_request(
                f"{target_provider.get_provider_name()}:{getattr(target_provider, 'model', '')}",
                deadline=deadline,
            )
        except asyncio.CancelledError:
            await record_cancelled_egress()
            raise
        content_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_buffers: Dict[int, Dict[str, str]] = {}
        usage: Optional[ProviderUsage] = None
        finish_reason = ""
        started_at = time.monotonic()
        first_event = True

        async def emit_stream_event(event: Dict[str, Any]) -> None:
            if on_stream_event is None:
                return
            try:
                await on_stream_event(dict(event))
            except Exception as exc:
                code = record_degradation("gateway_agentic_stream_callback", exc)
                request_trace.setdefault("degradation", []).append(
                    {
                        "type": "stream_callback_failure",
                        "code": code,
                        "event_type": str(event.get("type") or ""),
                    }
                )

        stream = target_provider.stream_chat_events(
            prepared_messages,
            temperature,
            max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            thinking=thinking,
        ).__aiter__()
        try:
            while True:
                try:
                    event = await deadline.wait_for(anext(stream), limit=min(30.0, deadline.remaining()))
                except StopAsyncIteration:
                    break
                if first_event:
                    first_event = False
                    try:
                        from app.observability.usage_diagnostics import record_ttft

                        record_ttft((time.monotonic() - started_at) * 1000.0)
                    except Exception as exc:
                        record_degradation("agentic_stream_ttft", exc)
                event_type = str(event.get("type") or "")
                if event_type == "response":
                    return dict(event.get("response") or {})
                if event_type == "content_delta":
                    content_parts.append(str(event.get("content") or ""))
                elif event_type == "thinking_delta":
                    thinking_parts.append(str(event.get("content") or ""))
                elif event_type == "tool_call_delta":
                    index = int(event.get("index") or 0)
                    buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    buffer["id"] += str(event.get("id") or "")
                    buffer["name"] += str(event.get("name") or "")
                    buffer["arguments"] += str(event.get("arguments") or "")
                elif event_type == "usage":
                    reported = ProviderUsage.from_mapping(event.get("usage"), requests=0)
                    usage = reported if usage is None else usage.merge(reported)
                elif event_type == "finish":
                    finish_reason = str(event.get("finish_reason") or "")
                await emit_stream_event(event)
            self.reliability.success(lease)
        except asyncio.CancelledError:
            self.reliability.cancelled(lease)
            await record_cancelled_egress()
            raise
        except Exception:
            self.reliability.failure(lease)
            await self.telemetry.record_egress(
                request_trace,
                provider_profile=provider,
                provider_name=target_provider.get_provider_name(),
                messages=prepared_messages,
                data_classification=data_classification,
                authorized=egress_authorized,
                status="failed",
            )
            runtime_metrics.increment("gateway.agentic_stream_failure")
            raise
        except BaseException:
            self.reliability.cancelled(lease)
            raise
        finally:
            await self.telemetry.close_provider_stream(stream)

        tool_calls = [
            {
                "id": value["id"] or f"stream-call-{index}",
                "type": "function",
                "name": value["name"],
                "arguments": value["arguments"] or "{}",
            }
            for index, value in sorted(tool_buffers.items())
            if value["name"]
        ]
        if usage is None:
            normalized_usage = ProviderUsage.from_mapping(
                None,
                requests=1,
                requested_max_tokens=int(max_tokens or getattr(target_provider, "max_tokens", 0) or 0),
            ).to_dict()
        else:
            normalized_usage = {
                **usage.to_dict(),
                "requests": 1,
                "requested_max_tokens": int(max_tokens or getattr(target_provider, "max_tokens", 0) or 0),
            }
        self.total_requests += 1
        self.total_tokens += int(normalized_usage.get("total_tokens") or 0)
        try:
            from app.observability.usage_diagnostics import record_provider_usage

            record_provider_usage(normalized_usage)
        except Exception as exc:
            record_degradation("agentic_stream_usage_diagnostics", exc)
        runtime_metrics.increment("gateway.agentic_stream_success")
        runtime_metrics.observe("gateway.agentic_stream_latency_ms", (time.monotonic() - started_at) * 1000.0)
        request_trace["usage"] = normalized_usage
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=prepared_messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="completed",
        )
        return {
            "provider": target_provider.get_provider_name(),
            "model": str(getattr(target_provider, "model", "") or ""),
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts) or None,
            "tool_calls": tool_calls or None,
            "usage": normalized_usage,
            "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
            "request_id": request_trace.get("request_id"),
            "request_fingerprint": request_trace.get("request_fingerprint"),
        }

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        *,
        on_thinking: Optional[Any] = None,
        timeout_seconds: Optional[float] = None,
        chunk_timeout_seconds: Optional[float] = None,
        data_classification: str = "project_private",
        egress_authorized: bool = True,
    ):
        """
        Stream chat response token by token, with retry before first chunk.
        流式输出聊天响应，首个 chunk 到达前支持重试。

        Args:
            messages: List of messages / 消息列表
            provider: Profile ID / 配置文件ID
            temperature: Override temperature / 覆盖温度
            max_tokens: Override max tokens / 覆盖最大token数
            on_thinking: 可选 async 回调，接收推理/思考增量（默认 None；正文仍走 yield）。
                Optional async callback for reasoning/thinking deltas (content still yields).

        Yields:
            String chunks as they arrive from the LLM
        """
        self._ensure_reliability()
        requested_timeout = float(timeout_seconds or self.request_timeout)
        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                requested_timeout = task_execution.bounded_timeout(requested_timeout)
        except ImportError:
            pass
        deadline = RequestDeadline(requested_timeout)
        chunk_timeout = max(1.0, float(chunk_timeout_seconds or min(30.0, deadline.total_seconds)))
        target_provider = self.provider_registry.resolve(provider)

        messages, accounting, overflow_degradation, final_payload_fingerprint = self.payload_accounting.prepare(
            messages=messages,
            tools=None,
            target_provider=target_provider,
            max_tokens=max_tokens,
        )
        request_trace = await self.telemetry.record_request_plan(
            messages=messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,
            token_accounting=accounting,
            capabilities={"streaming": True},
            degradation=overflow_degradation,
            final_payload_fingerprint=final_payload_fingerprint,
        )
        request_trace["timeout_seconds"] = deadline.total_seconds
        request_trace["chunk_timeout_seconds"] = chunk_timeout
        request_trace["_deadline"] = deadline
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="attempted",
        )
        if not egress_authorized:
            raise PermissionError("external_egress_not_authorized")
        try:
            from app.jobs.durable_queue import current_task_execution

            task_execution = current_task_execution()
            if task_execution is not None:
                await task_execution.mark_external_side_effect(str(request_trace.get("request_fingerprint") or ""))
        except ImportError:
            pass
        started_at = time.time()

        # Retry loop: retry only before the first chunk arrives.
        # Once data starts streaming, do not retry to avoid duplicate output.
        # 重试仅在首个 chunk 到达前；一旦开始接收数据则不再重试（避免重复输出）。
        last_exception = None
        attempts = max(1, self.max_retries + 1)
        for attempt in range(attempts):
            lease = await self.reliability.before_request(
                f"{target_provider.get_provider_name()}:{getattr(target_provider, 'model', '')}",
                deadline=deadline,
            )
            stream = None
            try:
                first_chunk = True
                from app.observability.otel import telemetry

                with telemetry.span(
                    "gen_ai.chat.stream",
                    attributes={
                        "gen_ai.operation.name": "chat",
                        "gen_ai.provider.name": target_provider.get_provider_name(),
                        "gen_ai.request.model": str(getattr(target_provider, "model", "") or ""),
                        "wenshape.request_id": str(request_trace.get("request_id") or ""),
                    },
                ):
                    stream = target_provider.stream_chat(
                        messages, temperature, max_tokens, on_thinking=on_thinking
                    ).__aiter__()
                    while True:
                        try:
                            chunk = await deadline.wait_for(anext(stream), limit=chunk_timeout)
                        except StopAsyncIteration:
                            break
                        first_chunk = False
                        yield chunk
                self.reliability.success(lease)
                runtime_metrics.increment("gateway.stream_success")
                stream_usage = ProviderUsage.from_mapping(
                    None,
                    requests=1,
                    requested_max_tokens=int(max_tokens or getattr(target_provider, "max_tokens", 0) or 0),
                ).to_dict()
                request_trace["usage"] = stream_usage
                try:
                    from app.context_engine.trace_collector import TraceEventType, trace_collector

                    await trace_collector.record(
                        TraceEventType.LLM_RESPONSE,
                        "llm_gateway",
                        {
                            "request_id": request_trace.get("request_id"),
                            "plan_id": request_trace.get("plan_id"),
                            "request_fingerprint": request_trace.get("request_fingerprint"),
                            "provider": target_provider.get_provider_name(),
                            "latency_ms": int((time.time() - started_at) * 1000),
                            "stream": True,
                            "usage": stream_usage,
                        },
                    )
                except Exception as exc:
                    logger.warning("TRACE ERROR (stream gateway): %s", exc)
                await self.telemetry.record_egress(
                    request_trace,
                    provider_profile=provider,
                    provider_name=target_provider.get_provider_name(),
                    messages=messages,
                    data_classification=data_classification,
                    authorized=egress_authorized,
                    status="completed",
                )
                return  # Stream completed successfully
            except asyncio.CancelledError:
                await self.telemetry.close_provider_stream(stream)
                stream = None
                self.reliability.cancelled(lease)
                await self.telemetry.record_egress(
                    request_trace,
                    provider_profile=provider,
                    provider_name=target_provider.get_provider_name(),
                    messages=messages,
                    data_classification=data_classification,
                    authorized=egress_authorized,
                    status="cancelled",
                )
                raise
            except Exception as e:
                await self.telemetry.close_provider_stream(stream)
                stream = None
                self.reliability.failure(lease)
                runtime_metrics.increment("gateway.stream_failure")
                if not first_chunk:
                    # Already yielded data — cannot retry without duplication
                    await self.telemetry.record_egress(
                        request_trace,
                        provider_profile=provider,
                        provider_name=target_provider.get_provider_name(),
                        messages=messages,
                        data_classification=data_classification,
                        authorized=egress_authorized,
                        status="failed_after_partial_stream",
                    )
                    raise
                last_exception = e
                provider_name = target_provider.get_provider_name()
                contract = normalize_provider_error(e, provider=provider_name)
                is_retryable, reason = contract.retryable, contract.category
                if not is_retryable:
                    provider_name = (
                        target_provider.get_provider_name()
                        if hasattr(target_provider, "get_provider_name")
                        else "unknown"
                    )
                    raise LLMError(
                        str(e),
                        provider=provider_name,
                        reason=reason,
                        status_code=getattr(e, "status_code", None),
                        is_retryable=False,
                        original=e,
                        contract=contract,
                    ) from e
                logger.warning(
                    "Stream retryable error before first chunk (attempt=%d/%d, reason=%s): %s",
                    attempt + 1,
                    attempts,
                    reason,
                    e,
                )
                if attempt < attempts - 1:
                    delay = provider_retry_delay(contract, attempt, self.retry_delays, self.max_retry_delay)
                    await deadline.sleep(delay)
            finally:
                await self.telemetry.close_provider_stream(stream)

        # All retries exhausted
        provider_name = (
            target_provider.get_provider_name() if hasattr(target_provider, "get_provider_name") else "unknown"
        )
        contract = normalize_provider_error(last_exception, provider=provider_name)
        is_retryable, reason = contract.retryable, contract.category
        await self.telemetry.record_egress(
            request_trace,
            provider_profile=provider,
            provider_name=target_provider.get_provider_name(),
            messages=messages,
            data_classification=data_classification,
            authorized=egress_authorized,
            status="failed",
        )
        raise LLMError(
            str(last_exception),
            provider=provider_name,
            reason=reason,
            status_code=getattr(last_exception, "status_code", None),
            is_retryable=True,
            original=last_exception,
            contract=contract,
        ) from last_exception

    def get_provider_for_agent(self, agent_name: str) -> str:
        """
        Get configured PROFILE ID for specific agent
        """
        assignments = llm_config_service.get_assignments()
        profile_id = assignments.get(agent_name)

        if not profile_id:
            raise ValueError(f"No LLM profile assigned for agent '{agent_name}'.")

        if profile_id not in self.providers:
            # 运行期修改 assignments/profile 时，orchestrator 可能仍在复用旧 gateway 实例；
            # 这里做一次按需加载，避免误判为“未加载”。
            self.provider_registry.ensure_loaded(profile_id)

        if profile_id not in self.providers:
            raise ValueError(f"Assigned LLM profile '{profile_id}' not loaded for agent '{agent_name}'.")

        return profile_id

    def get_temperature_for_agent(self, agent_name: str) -> float:
        """
        Get configured temperature (from assigned profile)
        """
        profile_id = self.get_provider_for_agent(agent_name)
        # However, we can't easily peek into provider instance config without casting
        # But we can look at the raw profile data
        profile = llm_config_service.get_profile_by_id(profile_id)
        if profile:
            return profile.get("temperature", 0.7)
        return 0.7

    def thinking_param_for_agent(
        self,
        agent_name: str,
        enabled: bool = False,
        *,
        reasoning_level: str = "auto",
    ) -> Optional[Dict[str, Any]]:
        """构建该 agent 当前模型开启 thinking 时要传给 ``chat(thinking=...)`` 的参数 dict。

        未开启 / 模型不支持「参数级」thinking 切换 → None（能力降级，调用方不传 thinking 即正常）。
        """
        if not enabled and reasoning_level == "auto":
            return None
        try:
            profile = self.get_profile_for_agent(agent_name)
        except Exception:
            return None
        if not profile:
            return None
        from app.llm_gateway.thinking import build_reasoning_param

        level = reasoning_level if reasoning_level != "auto" else "high"
        return build_reasoning_param(
            profile.get("provider", ""),
            profile.get("model", ""),
            level,
            dialect=str(profile.get("reasoning_dialect") or ""),
        )

    def get_profile_for_agent(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get full profile config for specific agent
        获取特定 Agent 的完整配置
        """
        try:
            profile_id = self.get_provider_for_agent(agent_name)
            return llm_config_service.get_profile_by_id(profile_id)
        except ValueError:
            return None

    def get_model_for_agent(self, agent_name: str) -> Optional[str]:
        """
        Get model name for specific agent
        获取特定 Agent 使用的模型名称
        """
        profile = self.get_profile_for_agent(agent_name)
        if profile:
            return profile.get("model")
        return None


# Global gateway instance / 全局网关实例
_gateway_instance: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    """Get or create global gateway instance"""
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = LLMGateway()
    return _gateway_instance


def reset_gateway() -> None:
    """Reset global gateway instance so new config takes effect / 重置全局网关实例以应用新配置"""
    global _gateway_instance
    _gateway_instance = None
