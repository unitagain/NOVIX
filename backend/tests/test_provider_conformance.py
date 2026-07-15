"""Offline provider adapter inventory and conformance matrix."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.llm_gateway.providers.anthropic_provider as anthropic_module
import app.llm_gateway.providers.custom_provider as custom_module
import app.llm_gateway.providers.deepseek_provider as deepseek_module
import app.llm_gateway.providers.gemini_provider as gemini_module
import app.llm_gateway.providers.openai_provider as openai_module
from app.llm_gateway.capabilities import CapabilityNegotiator
from app.llm_gateway.provider_conformance import PROVIDER_ADAPTER_PROFILES, provider_adapter_inventory
from app.llm_gateway.provider_registry import ProviderRegistry
from app.llm_gateway.providers.base import BaseLLMProvider, normalize_openai_usage, stream_openai_events
from app.llm_gateway.providers.custom_provider import CustomProvider
from app.llm_gateway.providers.deepseek_provider import DeepSeekProvider
from app.llm_gateway.providers.gemini_provider import GeminiProvider
from app.llm_gateway.providers.openai_provider import OpenAIProvider


REGISTERED_TYPES = ProviderRegistry.SUPPORTED_TYPES


class _AsyncRows:
    def __init__(self, rows):
        self.rows = list(rows)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.rows:
            raise StopAsyncIteration
        return self.rows.pop(0)

    async def aclose(self):
        self.closed = True


class _Completions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs.get("stream"):
            return self.response
        return self.response


class _Client:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_Completions(response))


def _response(*, usage=None):
    message = SimpleNamespace(content="ok", tool_calls=[])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage, model="model")


def _provider(provider_class):
    provider = provider_class.__new__(provider_class)
    BaseLLMProvider.__init__(provider, api_key="key", model="model", max_tokens=800, temperature=0.7)
    provider.client = _Client(
        _response(usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
    )
    return provider


def test_inventory_covers_every_registered_adapter_and_classifies_stream_mode():
    assert set(PROVIDER_ADAPTER_PROFILES) == REGISTERED_TYPES
    rows = {row["provider"]: row for row in provider_adapter_inventory()}
    assert rows["openai"]["stream_mode"] == "native_tool_stream"
    assert rows["anthropic"]["stream_mode"] == "native_tool_stream"
    assert rows["deepseek"]["stream_mode"] == "openai_compatible_tool_stream"
    assert rows["gemini"]["stream_mode"] == "non_stream_fallback"
    assert rows["gemini"]["native_agentic_stream"] is False


def test_registry_constructs_every_inventory_adapter_with_expected_base_url(monkeypatch):
    captured = []

    def openai_client(*, api_key, base_url=None):
        captured.append(base_url or "")
        return object()

    monkeypatch.setattr(openai_module, "create_async_openai_client", openai_client)
    monkeypatch.setattr(deepseek_module, "create_async_openai_client", openai_client)
    monkeypatch.setattr(gemini_module, "create_async_openai_client", openai_client)
    monkeypatch.setattr(custom_module, "create_async_openai_client", openai_client)
    monkeypatch.setattr(anthropic_module, "create_async_anthropic_client", lambda **_kwargs: object())

    for name in sorted(REGISTERED_TYPES):
        profile = {"provider": name, "id": name, "api_key": "key", "model": "model"}
        if name == "custom":
            profile["base_url"] = "https://custom.invalid/v1"
        provider = ProviderRegistry.create(profile)
        assert provider is not None
        assert provider.get_provider_name() == name
        assert provider.supports_agentic_stream() is PROVIDER_ADAPTER_PROFILES[name].native_agentic_stream

    expected_urls = {
        row.default_base_url for row in PROVIDER_ADAPTER_PROFILES.values() if row.default_base_url
    } | {"https://custom.invalid/v1"}
    assert expected_urls <= set(captured)


@pytest.mark.parametrize("provider_class", [OpenAIProvider, DeepSeekProvider, CustomProvider, GeminiProvider])
def test_openai_style_request_mapping_preserves_zero_temperature_and_options(provider_class):
    provider = _provider(provider_class)
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    result = asyncio.run(
        provider.chat(
            [{"role": "user", "content": "x"}],
            temperature=0.0,
            max_tokens=123,
            tools=tools,
            tool_choice="required",
            response_format={"type": "json_object"},
            extra_body={"trace": False},
        )
    )
    request = provider.client.chat.completions.calls[0]

    assert request["temperature"] == 0.0
    assert request["max_tokens"] == 123
    assert request["tools"] == tools
    assert request["tool_choice"] == "required"
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"trace": False}
    assert result["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 0
    assert "cache_read_tokens" not in result["usage"]


@pytest.mark.parametrize("provider_class", [OpenAIProvider, DeepSeekProvider, CustomProvider])
def test_openai_style_thinking_is_explicit_request_metadata(provider_class):
    provider = _provider(provider_class)
    asyncio.run(
        provider.chat(
            [{"role": "user", "content": "x"}],
            thinking={"reasoning_effort": "high"},
        )
    )
    request = provider.client.chat.completions.calls[0]
    assert request["extra_body"] == {"reasoning_effort": "high"}
    if provider_class is OpenAIProvider:
        assert "temperature" not in request


def test_capability_negotiation_is_inventory_driven_and_fail_closed():
    gemini = _provider(GeminiProvider)
    negotiated = CapabilityNegotiator().negotiate(
        gemini,
        {
            "tools": [{"type": "function"}],
            "tool_choice": "required",
            "response_format": {"type": "json_object"},
            "thinking": {"budget_tokens": 100},
        },
    )
    assert negotiated["options"] == {}
    assert negotiated["capabilities"]["stream_mode"] == "non_stream_fallback"
    assert {row["capability"] for row in negotiated["degradation"]} == {"tools", "json_mode", "thinking"}

    class UnknownProvider(BaseLLMProvider):
        async def chat(self, messages, temperature=None, max_tokens=None, **kwargs):
            return {}

        def get_provider_name(self):
            return "unknown"

    unknown = UnknownProvider("key", "model")
    result = CapabilityNegotiator().negotiate(unknown, {"tools": [{"type": "function"}]})
    assert result["capabilities"]["assumed"] is True
    assert result["capabilities"]["tools"] is False
    assert result["options"] == {}


def test_shared_openai_stream_normalizes_content_tool_finish_and_usage():
    chunks = _AsyncRows(
        [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="part",
                            reasoning_content="thought",
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(name="lookup", arguments='{"id":1}'),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
            ),
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=2,
                    completion_tokens=1,
                    total_tokens=3,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
                choices=[],
            ),
        ]
    )
    client = _Client(chunks)

    async def collect():
        return [event async for event in stream_openai_events(client, {"model": "m"})]

    events = asyncio.run(collect())
    assert {event["type"] for event in events} == {
        "thinking_delta",
        "content_delta",
        "tool_call_delta",
        "finish",
        "usage",
    }
    usage = next(event["usage"] for event in events if event["type"] == "usage")
    assert usage["cache_read_tokens"] == 0


def test_usage_preserves_unknown_cache_fields_instead_of_fabricating_zero():
    unknown = normalize_openai_usage(SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3))
    zero = normalize_openai_usage(
        SimpleNamespace(
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
    )
    assert "cache_read_tokens" not in unknown
    assert zero["cache_read_tokens"] == 0


def test_non_native_adapter_emits_explicit_response_fallback():
    provider = _provider(GeminiProvider)

    async def collect():
        return [event async for event in provider.stream_chat_events([{"role": "user", "content": "x"}])]

    events = asyncio.run(collect())
    assert events[0]["type"] == "response"
    assert events[0]["native"] is False
