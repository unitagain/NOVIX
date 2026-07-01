# -*- coding: utf-8 -*-
"""
Phase 6 验收：Anthropic prompt caching（结构级，无网络、无真实 key）。
验证开启开关后 system 被标记为 ephemeral 可缓存块；关闭则为纯字符串。
真实缓存命中需 Anthropic key，此处只验请求构造契约。
"""

import asyncio
from types import SimpleNamespace

import app.llm_gateway.providers.anthropic_provider as ap


class _FakeMessages:
    def __init__(self):
        self.captured = None

    async def create(self, **kwargs):
        self.captured = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="claude",
            stop_reason="end_turn",
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _provider():
    p = ap.AnthropicProvider(api_key="x")
    fc = _FakeClient()
    p.client = fc
    return p, fc


def test_prompt_caching_marks_system_block(monkeypatch):
    monkeypatch.setattr(ap, "_prompt_caching_enabled", lambda: True)
    p, fc = _provider()
    asyncio.run(p.chat([{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]))
    system_arg = fc.messages.captured["system"]
    assert isinstance(system_arg, list)
    assert system_arg[0]["cache_control"]["type"] == "ephemeral"
    assert system_arg[0]["text"] == "SYS"


def test_no_caching_system_is_plain_string(monkeypatch):
    monkeypatch.setattr(ap, "_prompt_caching_enabled", lambda: False)
    p, fc = _provider()
    asyncio.run(p.chat([{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]))
    assert fc.messages.captured["system"] == "SYS"  # 默认纯字符串，行为不变
