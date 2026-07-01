# -*- coding: utf-8 -*-
"""
Phase 6 验收：按需一致性评审（Evaluator）。
FakeGateway 返回结构化 issues + CanonStorage 关系护栏报警；无网络、无真实 key。
"""

import asyncio

from app.agents.archivist import ArchivistAgent
from app.storage.canon import CanonStorage


class _FakeGateway:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def get_provider_for_agent(self, agent_name):
        return "fake"

    async def chat(self, messages, provider=None, temperature=None, response_format=None, **kwargs):
        self.calls += 1
        return {"content": self.content}


def _archivist(gateway, canon):
    return ArchivistAgent(gateway=gateway, card_storage=None, canon_storage=canon, draft_storage=None, language="zh")


def test_review_parses_issues_and_includes_alerts(tmp_path):
    canon = CanonStorage(data_dir=str(tmp_path))
    # 落两条冲突关系（无 change）→ 确定性护栏应报警
    asyncio.run(
        canon.add_relations(
            "p",
            [
                {"subject": "张三", "relation": "盟友", "object": "李四", "chapter": "V1C002"},
                {"subject": "张三", "relation": "敌对", "object": "李四", "chapter": "V3C005"},
            ],
        )
    )
    gw = _FakeGateway(
        '{"issues":[{"type":"consistency","severity":"high","excerpt":"他用了火球术","detail":"设定中张三不会法术","suggestion":"改为剑技"}]}'
    )
    agent = _archivist(gw, canon)
    result = asyncio.run(agent.review_consistency("p", "V3C006", "正文：张三用了火球术。"))
    assert gw.calls == 1
    assert len(result["issues"]) == 1 and result["issues"][0]["type"] == "consistency"
    assert any("Relation Conflict" in a for a in result["alerts"])  # 确定性护栏并入


def test_review_empty_draft_skips_llm(tmp_path):
    canon = CanonStorage(data_dir=str(tmp_path))
    gw = _FakeGateway("{}")
    agent = _archivist(gw, canon)
    result = asyncio.run(agent.review_consistency("p", "V1C001", "   "))
    assert result == {"issues": [], "alerts": []}
    assert gw.calls == 0  # 空草稿不调用 LLM


def test_review_malformed_llm_output_safe(tmp_path):
    canon = CanonStorage(data_dir=str(tmp_path))
    gw = _FakeGateway("这不是 JSON")
    agent = _archivist(gw, canon)
    result = asyncio.run(agent.review_consistency("p", "V1C001", "一段正文"))
    assert result["issues"] == []  # 解析失败安全降级，不抛
