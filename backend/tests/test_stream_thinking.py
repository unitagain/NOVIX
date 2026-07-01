# -*- coding: utf-8 -*-
"""
Phase 5 验收：token 级流式 thinking（reasoning_content 旁路）。
纯本地、asyncio.run + 假 openai 客户端，无网络、无真实 key。
"""

import asyncio
from types import SimpleNamespace

from app.llm_gateway.providers.base import _extract_reasoning_delta
from app.llm_gateway.providers.custom_provider import CustomProvider


class _Chunk:
    def __init__(self, delta):
        self.choices = [SimpleNamespace(delta=delta)]


async def _aiter(items):
    for it in items:
        yield it


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        return _aiter(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def _provider(chunks):
    p = CustomProvider(api_key="x", base_url="http://example.com", model="m")
    p.client = _FakeClient(chunks)
    return p


# ----------------------------------------------- _extract_reasoning_delta --


def test_extract_reasoning_delta_variants():
    assert _extract_reasoning_delta(SimpleNamespace(reasoning_content="x")) == "x"
    assert _extract_reasoning_delta(SimpleNamespace(content="y")) is None
    assert _extract_reasoning_delta(SimpleNamespace(model_extra={"reasoning_content": "z"})) == "z"
    assert _extract_reasoning_delta(SimpleNamespace(model_extra={})) is None
    assert _extract_reasoning_delta(None) is None


# ----------------------------------------------------- streaming thinking --


def test_stream_separates_thinking_from_content():
    chunks = [
        _Chunk(SimpleNamespace(content=None, reasoning_content="我在想")),
        _Chunk(SimpleNamespace(content="正文A")),  # 无 reasoning_content 属性
        _Chunk(SimpleNamespace(content="正文B", reasoning_content="继续想")),
    ]
    provider = _provider(chunks)
    thoughts = []

    async def on_thinking(delta):
        thoughts.append(delta)

    async def run():
        out = []
        async for c in provider.stream_chat([{"role": "user", "content": "hi"}], on_thinking=on_thinking):
            out.append(c)
        return out

    out = asyncio.run(run())
    assert "".join(out) == "正文A正文B"  # 正文只含 content（思考不混入正文）
    assert thoughts == ["我在想", "继续想"]  # 思考经旁路回调


def test_stream_default_path_unchanged_without_callback():
    """不传 on_thinking（默认）：只产出正文、不报错——与历史行为一致。"""
    chunks = [
        _Chunk(SimpleNamespace(content="甲", reasoning_content="忽略")),
        _Chunk(SimpleNamespace(content="乙")),
    ]
    provider = _provider(chunks)

    async def run():
        out = []
        async for c in provider.stream_chat([{"role": "user", "content": "hi"}]):
            out.append(c)
        return out

    assert "".join(asyncio.run(run())) == "甲乙"
