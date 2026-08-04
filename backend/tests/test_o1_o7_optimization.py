from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.observability.runtime_metrics import RuntimeMetrics
from app.observability.usage_diagnostics import (
    build_usage_diagnostics,
    memory_semantic_recall_decision,
    record_agent_run,
    record_provider_usage,
    source_bucket,
    tool_bucket,
)
from app.orchestrator.context_assembly_service import ContextAssemblyService
from app.context_engine.token_accounting import (
    record_token_estimator_observation,
    token_error_bound_recommendation,
)
from app.llm_gateway.contracts import ProviderUsage
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.providers.base import BaseLLMProvider
from app.llm_gateway.providers.anthropic_provider import AnthropicProvider
from app.llm_gateway.providers.openai_provider import OpenAIProvider
from app.agents.writing_actions import WritingActionToolset
from app.orchestrator.architecture import maintenance_freeze_policies


def test_usage_diagnostics_reports_insufficient_evidence_without_content(monkeypatch):
    metrics = RuntimeMetrics(max_series=64, max_events=100, max_histogram_samples=20)
    monkeypatch.setattr("app.observability.usage_diagnostics.runtime_metrics", metrics)
    record_agent_run(status="completed", iterations=2, tool_calls=3, elapsed_ms=100)
    report = build_usage_diagnostics(metrics, minimum_turns=2)
    assert report["status"] == "insufficient_evidence"
    assert report["sample_turns"] == 1
    assert "private manuscript" not in str(report).lower()
    assert "prompt" not in report


def test_usage_diagnostics_becomes_ready_at_fixed_sample_threshold(monkeypatch):
    metrics = RuntimeMetrics(max_series=64, max_events=100, max_histogram_samples=20)
    monkeypatch.setattr("app.observability.usage_diagnostics.runtime_metrics", metrics)
    record_agent_run(status="completed", iterations=1, tool_calls=1, elapsed_ms=50)
    record_agent_run(status="incomplete", iterations=6, tool_calls=5, elapsed_ms=500)
    report = build_usage_diagnostics(metrics, minimum_turns=2)
    assert report["status"] == "ready"
    assert report["terminal_states"]["completed"] == 1
    assert report["terminal_states"]["incomplete"] == 1
    assert report["budget"]["healthy"] is True


def test_usage_diagnostic_labels_are_fixed_low_cardinality_buckets():
    assert tool_bucket("query_canon") == "canon"
    assert tool_bucket("user-defined-private-tool-name") == "other"
    assert source_bucket("style_card") == "style"
    assert source_bucket("private-project-card-name") == "other"


def test_edit_unique_match_failure_records_content_free_counter(monkeypatch):
    metrics = RuntimeMetrics(max_series=64, max_events=100, max_histogram_samples=20)
    monkeypatch.setattr("app.observability.usage_diagnostics.runtime_metrics", metrics)
    result = asyncio.run(
        WritingActionToolset("重复 重复").execute(
            "edit_lines",
            {"old_text": "重复", "new_text": "替换"},
        )
    )
    assert "不唯一" in result
    assert metrics.snapshot()["counters"]["usage.edit.unique_match_miss"] == 1


def test_edit_draft_projection_uses_token_budget_and_keeps_recovery_marker():
    body = "开场" + ("中段内容" * 4000) + "结尾"
    projected, changed = ContextAssemblyService.project_draft_to_tokens(body, budget_tokens=600)
    assert changed is True
    assert projected.startswith("开场")
    assert projected.endswith("结尾")
    assert "read_chapter" in projected


def test_context_supply_report_distinguishes_available_from_pushed_sources():
    class Plan:
        budget = {"input_tokens": 10_000, "output_reserve_tokens": 4_096}
        snapshot = ({"asset_type": "style_card"}, {"asset_type": "canon"})

    service = ContextAssemblyService()
    request = service.assemble_writer_request(
        message="修改中段",
        chapter="V1C001",
        current_text="正文" * 5000,
        has_selection=False,
        target_word_count=3000,
        context_plan=Plan(),
    )
    assert "style" in request.supply_report["available"]
    assert "style" not in request.supply_report["pushed"]
    assert request.supply_report["draft_pushed_tokens"] <= 6000
    assert request.supply_report["omitted"][0]["recoverable"] is True


def test_cache_usage_preserves_unavailable_instead_of_fabricating_zero():
    unavailable = ProviderUsage.from_mapping({"prompt_tokens": 10, "completion_tokens": 2})
    available = ProviderUsage.from_mapping(
        {"prompt_tokens": 10, "completion_tokens": 2, "cache_read_tokens": 0}
    )
    assert unavailable.cache_read_tokens is None
    assert available.cache_read_tokens == 0


def test_cache_usage_diagnostics_distinguishes_unknown_from_zero(monkeypatch):
    metrics = RuntimeMetrics(max_series=64, max_events=100, max_histogram_samples=20)
    monkeypatch.setattr("app.observability.usage_diagnostics.runtime_metrics", metrics)
    record_provider_usage(ProviderUsage.from_mapping({"prompt_tokens": 10}).to_dict())
    record_provider_usage(
        ProviderUsage.from_mapping({"prompt_tokens": 10, "cache_read_tokens": 0}).to_dict()
    )
    counters = metrics.snapshot()["counters"]
    assert counters["usage.provider.requests"] == 2
    assert counters["usage.provider.cache_usage_unavailable"] == 1


def test_token_margin_recommendation_requires_fixed_evidence_and_never_applies():
    provider, model = "test-provider-o4", "test-model-o4"
    before = token_error_bound_recommendation(provider, model, current_error_bound=0.35)
    assert before["status"] == "insufficient_evidence"
    for _ in range(32):
        record_token_estimator_observation(
            provider=provider,
            model=model,
            estimated_tokens=105,
            actual_tokens=100,
        )
    after = token_error_bound_recommendation(provider, model, current_error_bound=0.35)
    assert after["status"] == "recommendation_available"
    assert 0.10 <= after["recommended_error_bound"] < 0.35
    assert after["applied"] is False


def test_memory_semantic_recall_remains_disabled_without_labeled_miss_evidence():
    blocked = memory_semantic_recall_decision(lexical_queries=100, labeled_semantic_misses=3)
    allowed = memory_semantic_recall_decision(lexical_queries=100, labeled_semantic_misses=20)
    assert blocked["status"] == "insufficient_evidence"
    assert blocked["default_enabled"] is False
    assert allowed["status"] == "experiment_allowed"
    assert allowed["default_enabled"] is False


def test_low_usage_maintenance_surfaces_have_explicit_expansion_gates():
    policies = {item["area"]: item for item in maintenance_freeze_policies()}
    assert set(policies) == {"eval", "durable_queue", "permission_policy"}
    assert policies["durable_queue"]["policy"] == "existing_consumers_only"


class _AsyncRows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.rows:
            raise StopAsyncIteration
        return self.rows.pop(0)


class _AnthropicStreamManager:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return _AsyncRows(self.rows)

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_anthropic_agentic_stream_normalizes_tool_deltas_and_usage():
    captured = {}
    rows = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=0,
                    cache_creation_input_tokens=2,
                    cache_read_input_tokens=3,
                )
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="tool-1", name="write_content"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"content":"正文"}'),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=5),
        ),
    ]

    def stream(**kwargs):
        captured.update(kwargs)
        return _AnthropicStreamManager(rows)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-test"
    provider.max_tokens = 100
    provider.temperature = 0.7
    provider.client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_content",
                "description": "write",
                "parameters": {"type": "object"},
            },
        }
    ]

    async def scenario():
        return [event async for event in provider.stream_chat_events([], tools=tools)]

    events = asyncio.run(scenario())
    assert captured["tools"][0]["input_schema"] == {"type": "object"}
    assert any(event["type"] == "tool_call_delta" and event["name"] == "write_content" for event in events)
    assert sum(int((event.get("usage") or {}).get("total_tokens") or 0) for event in events) == 15


def test_anthropic_usage_preserves_unavailable_cache_fields():
    usage = AnthropicProvider._normalize_usage(SimpleNamespace(input_tokens=4, output_tokens=2))
    assert usage == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}


def test_openai_agentic_stream_requests_usage_and_preserves_zero_temperature():
    captured = {}
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="正文", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=4,
                completion_tokens=2,
                total_tokens=6,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1),
            ),
            choices=[],
        ),
    ]

    async def create(**kwargs):
        captured.update(kwargs)
        return _AsyncRows(chunks)

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.model = "openai-test"
    provider.max_tokens = 100
    provider.temperature = 0.7
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def scenario():
        return [event async for event in provider.stream_chat_events([], temperature=0.0)]

    events = asyncio.run(scenario())
    assert captured["temperature"] == 0.0
    assert captured["stream_options"] == {"include_usage": True}
    assert any(event["type"] == "content_delta" for event in events)
    assert any((event.get("usage") or {}).get("cache_read_tokens") == 1 for event in events)


class _NativeStreamProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__(api_key="x", model="native-stream")

    async def chat(self, messages, temperature=None, max_tokens=None, **kwargs):
        raise AssertionError("native stream path must not call chat")

    def supports_agentic_stream(self) -> bool:
        return True

    async def stream_chat_events(self, messages, temperature=None, max_tokens=None, **kwargs):
        yield {"type": "content_delta", "content": "第一段"}
        yield {"type": "content_delta", "content": "第二段"}
        yield {
            "type": "usage",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cache_read_tokens": 1},
        }
        yield {"type": "finish", "finish_reason": "stop"}

    def get_provider_name(self):
        return "openai"


class _Telemetry:
    def __init__(self):
        self.statuses = []

    async def record_request_plan(self, **kwargs):
        return {"request_id": "req", "request_fingerprint": "fingerprint"}

    async def record_egress(self, request_trace, **kwargs):
        self.statuses.append(kwargs["status"])

    async def close_provider_stream(self, stream):
        close = getattr(stream, "aclose", None)
        if callable(close):
            await close()


def test_gateway_native_agentic_stream_emits_before_return_and_preserves_usage():
    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": _NativeStreamProvider()}
    gateway.total_requests = 0
    gateway.total_tokens = 0
    gateway.telemetry = _Telemetry()
    events = []

    async def scenario():
        async def on_event(event):
            events.append(dict(event))

        return await gateway.agentic_chat(
            [{"role": "user", "content": "synthetic"}],
            provider="profile",
            max_tokens=10,
            on_stream_event=on_event,
            data_classification="synthetic",
        )

    response = asyncio.run(scenario())
    assert [event["content"] for event in events if event["type"] == "content_delta"] == ["第一段", "第二段"]
    assert response["content"] == "第一段第二段"
    assert response["usage"]["cache_read_tokens"] == 1
    assert gateway.telemetry.statuses == ["attempted", "completed"]


def test_gateway_stream_callback_failure_does_not_fail_provider_request():
    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": _NativeStreamProvider()}
    gateway.total_requests = 0
    gateway.total_tokens = 0
    gateway.telemetry = _Telemetry()

    async def scenario():
        async def failing_callback(_event):
            raise RuntimeError("synthetic callback failure")

        return await gateway.agentic_chat(
            [{"role": "user", "content": "synthetic"}],
            provider="profile",
            max_tokens=10,
            on_stream_event=failing_callback,
            data_classification="synthetic",
        )

    response = asyncio.run(scenario())
    assert response["content"] == "第一段第二段"
    assert gateway.telemetry.statuses == ["attempted", "completed"]


def test_gateway_agentic_stream_cancellation_closes_without_completed_egress():
    class BlockingProvider(_NativeStreamProvider):
        async def stream_chat_events(self, messages, temperature=None, max_tokens=None, **kwargs):
            await asyncio.Event().wait()
            yield {"type": "content_delta", "content": "unreachable"}

    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": BlockingProvider()}
    gateway.total_requests = 0
    gateway.total_tokens = 0
    gateway.telemetry = _Telemetry()

    async def scenario():
        task = asyncio.create_task(
            gateway.agentic_chat(
                [{"role": "user", "content": "synthetic"}],
                provider="profile",
                max_tokens=10,
                data_classification="synthetic",
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError("cancelled stream unexpectedly completed")

    asyncio.run(scenario())
    assert gateway.telemetry.statuses == ["attempted", "cancelled_uncertain"]
