# -*- coding: utf-8 -*-
"""Phase 10 · 创作记忆提取 + 端到端（提取→存→跨会话召回）回归测试。

验证 Archivist.extract_creative_memory 的解析/降级，以及「两轮会话」复用作者偏好的验收主场景。
无网络 / 无 key：用 FakeGateway。
"""

import asyncio

from app.agents.archivist import ArchivistAgent
from app.storage.creative_memory import CreativeMemoryStorage


class _FakeGateway:
    def __init__(self, content):
        self.content = content

    def get_provider_for_agent(self, name):
        return "fake"

    def get_temperature_for_agent(self, name):
        return 0.3

    async def chat(self, **kwargs):
        return {"content": self.content, "usage": {}, "model": "fake", "provider": "fake"}


def _archivist(content):
    return ArchivistAgent(_FakeGateway(content), None, None, None)


def test_extract_parses_items():
    arch = _archivist(
        '[{"slug":"short-dialogue","description":"作者偏好短句对白","body":"对白简短","type":"preference"}]'
    )
    out = asyncio.run(arch.extract_creative_memory("正文内容", user_feedback="对白短一点"))
    assert len(out) == 1
    assert out[0]["type"] == "preference"
    assert out[0]["description"] == "作者偏好短句对白"
    assert out[0]["slug"] == "short-dialogue"


def test_extract_empty_input_skips_llm():
    arch = _archivist("[]")
    out = asyncio.run(arch.extract_creative_memory("", user_feedback=""))
    assert out == []


def test_extract_bad_json_returns_empty():
    arch = _archivist("这不是 JSON")
    out = asyncio.run(arch.extract_creative_memory("正文", user_feedback="改改"))
    assert out == []


def test_extract_skips_items_without_description():
    arch = _archivist('[{"slug":"x","type":"preference"}, {"description":"有效偏好","type":"decision"}]')
    out = asyncio.run(arch.extract_creative_memory("正文", user_feedback="反馈"))
    assert len(out) == 1
    assert out[0]["description"] == "有效偏好"


def test_extract_then_recall_cross_session(tmp_path):
    """端到端：第一轮提取并写入，第二轮（新实例=新会话）召回命中——Phase 10 验收主场景。"""
    arch = _archivist(
        '[{"slug":"cold-tone","description":"作者喜欢冷峻克制的文风","body":"避免煽情","type":"preference"}]'
    )
    items = asyncio.run(arch.extract_creative_memory("正文", user_feedback="文风冷一点，别煽情"))

    store1 = CreativeMemoryStorage(str(tmp_path))
    for it in items:
        asyncio.run(store1.write_memory("p1", it["slug"], it["description"], it["body"], it["type"]))

    store2 = CreativeMemoryStorage(str(tmp_path))  # 新会话
    hits = asyncio.run(store2.recall("p1", "这一章的文风该怎么把握", top_k=3))
    assert any("冷峻" in h["description"] for h in hits)
    # MEMORY.md 索引也已重建并含该偏好
    idx = asyncio.run(store2.read_index("p1"))
    assert "冷峻克制" in idx
