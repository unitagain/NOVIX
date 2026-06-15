# -*- coding: utf-8 -*-
"""
Phase 2 验收：LLM 网关增强（tools / response_format / tool_calls 透传）。
用 FakeProvider + asyncio.run，无需网络与真实 API key、不依赖 pytest-asyncio 配置。
"""

import asyncio

from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.providers.base import BaseLLMProvider


class _FakeProvider(BaseLLMProvider):
    """记录收到的可选参数，并按是否带 tools 返回 tool_calls。"""

    def __init__(self):
        super().__init__(api_key="x", model="fake-model")
        self.last_kwargs = None

    async def chat(
        self,
        messages,
        temperature=None,
        max_tokens=None,
        *,
        tools=None,
        tool_choice=None,
        response_format=None,
        thinking=None,
        extra_body=None,
    ):
        self.last_kwargs = {
            "tools": tools,
            "tool_choice": tool_choice,
            "response_format": response_format,
            "thinking": thinking,
            "extra_body": extra_body,
        }
        tool_calls = [{"id": "c1", "type": "function", "name": "lookup_card", "arguments": "{}"}] if tools else None
        return {
            "content": None if tools else "ok",
            "tool_calls": tool_calls,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": self.model,
            "finish_reason": "tool_calls" if tools else "stop",
        }

    def get_provider_name(self):
        return "fake"


def _make_gateway() -> LLMGateway:
    # 跳过 __init__ 的 profile 加载，直接注入 FakeProvider
    gw = LLMGateway.__new__(LLMGateway)
    gw.providers = {"fake": _FakeProvider()}
    gw.max_retries = 1
    gw.retry_delays = [0]
    gw.max_retry_delay = 0
    gw.total_tokens = 0
    gw.total_requests = 0
    return gw


def test_chat_threads_tools_and_surfaces_tool_calls():
    gw = _make_gateway()
    tools = [{"type": "function", "function": {"name": "lookup_card", "parameters": {}}}]
    resp = asyncio.run(gw.chat([{"role": "user", "content": "hi"}], provider="fake", tools=tools))
    assert gw.providers["fake"].last_kwargs["tools"] == tools
    assert resp["tool_calls"] and resp["tool_calls"][0]["name"] == "lookup_card"


def test_chat_threads_response_format():
    gw = _make_gateway()
    rf = {"type": "json_object"}
    resp = asyncio.run(gw.chat([{"role": "user", "content": "hi"}], provider="fake", response_format=rf))
    assert gw.providers["fake"].last_kwargs["response_format"] == rf
    assert resp["tool_calls"] is None  # 无 tools 时不应有 tool_calls


def test_chat_backward_compatible_text_path():
    gw = _make_gateway()
    resp = asyncio.run(gw.chat([{"role": "user", "content": "hi"}], provider="fake"))
    assert resp["content"] == "ok"
    # 旧路径：未传任何新参，provider 收到的应全为 None
    assert all(v is None for v in gw.providers["fake"].last_kwargs.values())
