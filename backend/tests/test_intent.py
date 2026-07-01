# -*- coding: utf-8 -*-
"""
Phase 5 验收：写作意图判定器（vibe writing 后端骨干）。
纯本地、asyncio.run + FakeGateway，无网络、无真实 key。
"""

import asyncio

from app.agents.intent import classify_writing_intent, _extract_json


class _FakeGateway:
    """chat() 返回预置 content（模拟 LLM 结构化判定输出）；记录调用次数。"""

    def __init__(self, content):
        self.content = content
        self.calls = 0

    async def chat(self, messages, provider=None, temperature=None, response_format=None, **kwargs):
        self.calls += 1
        return {"content": self.content}


def _run(**kwargs):
    return asyncio.run(classify_writing_intent(**kwargs))


# ----------------------------------------------------- heuristic fast-paths --


def test_selection_is_edit_selection():
    d = _run(message="更紧凑些", has_selection=True, has_draft=True)
    assert d["action"] == "edit" and d["scope"] == "selection" and d["via"] == "heuristic"


def test_no_draft_is_write():
    d = _run(message="写第一章：主角登场", has_selection=False, has_draft=False)
    assert d["action"] == "write" and d["via"] == "heuristic"


def test_ambiguous_no_gateway_falls_back_to_edit_document():
    d = _run(message="改一下这里", has_selection=False, has_draft=True)
    assert d["action"] == "edit" and d["scope"] == "document" and d["via"] == "heuristic"


# --------------------------------------------------------- LLM ambiguous ----


def test_ambiguous_llm_classifies_write():
    gw = _FakeGateway('{"action":"write","scope":"document","reason":"要写新场景"}')
    # message 不含「续写/改」等关键词，才是真正含糊、需 LLM 判定的情形
    # （"接着写"等续写词现由 Phase 12 continue 启发式快路径处理，不再走 LLM）。
    d = _run(message="他们终于走到森林深处", has_selection=False, has_draft=True, gateway=gw, provider="p")
    assert d["action"] == "write" and d["via"] == "llm" and gw.calls == 1


def test_ambiguous_llm_classifies_edit_with_codefence():
    gw = _FakeGateway('```json\n{"action":"edit","scope":"document","reason":"润色措辞"}\n```')
    d = _run(message="这段读着别扭", has_selection=False, has_draft=True, gateway=gw, provider="p")
    assert d["action"] == "edit" and d["via"] == "llm"


def test_ambiguous_llm_malformed_falls_back_to_heuristic():
    gw = _FakeGateway("我觉得应该编辑吧")  # 非 JSON
    d = _run(message="嗯", has_selection=False, has_draft=True, gateway=gw, provider="p")
    assert d["action"] == "edit" and d["via"] == "heuristic"  # 安全降级


def test_heuristic_skips_llm_entirely():
    """选中/无草稿走快路径时不应触碰 gateway（零成本零延迟）。"""
    gw = _FakeGateway('{"action":"write"}')
    _run(message="x", has_selection=True, has_draft=True, gateway=gw, provider="p")
    assert gw.calls == 0


# --------------------------------------------------------- json extraction --


def test_extract_json_variants():
    assert _extract_json('{"a":1}')["a"] == 1
    assert _extract_json('前缀文字 {"a":2} 后缀文字')["a"] == 2
    assert _extract_json('```json\n{"a":3}\n```')["a"] == 3
    assert _extract_json("根本没有 json") is None
    assert _extract_json("") is None
