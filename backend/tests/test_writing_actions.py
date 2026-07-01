# -*- coding: utf-8 -*-
"""
Phase B 验收：写作动作工具集（write_content / edit_lines）—— 对齐 AI coding 的 Write/Edit。
纯字符串操作、无网络、无 LLM；agentic loop 段用 Fake gateway 验证 agent 自主调用写作工具。
"""

import asyncio

from app.agents.writing_actions import WritingActionToolset, writing_action_schemas
from app.agents.agentic import run_agentic_chat


# ----------------------------------------------------------- Schema tests --


def test_schemas_cover_write_and_edit():
    names = {s["function"]["name"] for s in writing_action_schemas()}
    assert names == {"write_content", "edit_lines"}


def test_toolset_schemas_without_retrieval():
    names = {s["function"]["name"] for s in WritingActionToolset("").schemas()}
    assert names == {"write_content", "edit_lines"}


# ---------------------------------------------------------- write_content --


def test_write_content_replace_on_empty():
    ts = WritingActionToolset("")
    out = asyncio.run(ts.execute("write_content", {"content": "第一章 风起"}))
    assert "写入" in out
    assert ts.working_text == "第一章 风起"
    assert ts.changed is True


def test_write_content_replace_overwrites():
    ts = WritingActionToolset("旧正文")
    asyncio.run(ts.execute("write_content", {"content": "全新正文", "mode": "replace"}))
    assert ts.working_text == "全新正文"


def test_write_content_append_keeps_existing():
    ts = WritingActionToolset("第一段。")
    out = asyncio.run(ts.execute("write_content", {"content": "第二段。", "mode": "append"}))
    assert "追加" in out
    assert ts.working_text == "第一段。\n\n第二段。"


def test_write_content_append_on_empty_falls_back_to_replace():
    ts = WritingActionToolset("")
    asyncio.run(ts.execute("write_content", {"content": "首段", "mode": "append"}))
    assert ts.working_text == "首段"


def test_write_content_empty_is_rejected():
    ts = WritingActionToolset("原文")
    out = asyncio.run(ts.execute("write_content", {"content": "   "}))
    assert "需要非空" in out
    assert ts.working_text == "原文"  # 未改动


# ------------------------------------------------------------- edit_lines --


def test_edit_lines_unique_replace():
    ts = WritingActionToolset("张三走进迷雾森林，四下张望。")
    out = asyncio.run(ts.execute("edit_lines", {"old_text": "四下张望", "new_text": "屏息凝神"}))
    assert "已替换" in out
    assert ts.working_text == "张三走进迷雾森林，屏息凝神。"


def test_edit_lines_delete_with_empty_new():
    ts = WritingActionToolset("多余的话。正文。")
    asyncio.run(ts.execute("edit_lines", {"old_text": "多余的话。", "new_text": ""}))
    assert ts.working_text == "正文。"


def test_edit_lines_not_found():
    ts = WritingActionToolset("正文")
    out = asyncio.run(ts.execute("edit_lines", {"old_text": "不存在", "new_text": "x"}))
    assert "未找到" in out
    assert ts.changed is False


def test_edit_lines_not_unique_refuses():
    ts = WritingActionToolset("猫。猫。")
    out = asyncio.run(ts.execute("edit_lines", {"old_text": "猫。", "new_text": "狗。"}))
    assert "不唯一" in out
    assert ts.working_text == "猫。猫。"  # 不唯一 → 不替换，保稳


def test_edit_lines_missing_old_text():
    out = asyncio.run(WritingActionToolset("x").execute("edit_lines", {"new_text": "y"}))
    assert "需要 old_text" in out


# ------------------------------------------------------- retrieval 组合 --


class _FakeRetrieval:
    def schemas(self):
        return [{"type": "function", "function": {"name": "query_canon", "parameters": {}}}]

    async def execute(self, name, arguments):
        return f"[检索结果:{name}]"


def test_schemas_merge_retrieval_then_writing():
    ts = WritingActionToolset("", retrieval_toolset=_FakeRetrieval())
    names = [s["function"]["name"] for s in ts.schemas()]
    assert names == ["query_canon", "write_content", "edit_lines"]  # 检索在前，便于先查后写


def test_execute_delegates_unknown_to_retrieval():
    ts = WritingActionToolset("", retrieval_toolset=_FakeRetrieval())
    out = asyncio.run(ts.execute("query_canon", {"query": "x"}))
    assert "检索结果" in out


def test_unknown_tool_graceful_without_retrieval():
    out = asyncio.run(WritingActionToolset("").execute("no_such", {}))
    assert "未知工具" in out


# ---------------------------------------------------- agentic loop 集成 --


class _WritingLoopGateway:
    """第 1 次 agent 调 write_content（自己生成 content），第 2 次产出收尾语。验证 agent 自主写作。"""

    def __init__(self):
        self.n = 0

    async def chat(
        self, messages, provider=None, temperature=None, max_tokens=None, retry=True, *, tools=None, **kwargs
    ):
        self.n += 1
        if self.n == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "w1",
                        "type": "function",
                        "name": "write_content",
                        "arguments": '{"content":"夜色四合，张三独自上路。"}',
                    }
                ],
                "usage": {},
                "model": "fake",
                "finish_reason": "tool_calls",
            }
        return {"content": "已完成本章初稿。", "tool_calls": None, "usage": {}, "model": "fake", "finish_reason": "stop"}


def test_agent_autonomously_writes_via_tool():
    gw = _WritingLoopGateway()
    ts = WritingActionToolset("")
    resp = asyncio.run(run_agentic_chat(gw, "fake", [{"role": "user", "content": "写第一章"}], ts, max_iterations=3))
    assert resp["content"] == "已完成本章初稿。"
    assert ts.working_text == "夜色四合，张三独自上路。"  # agent 自主生成的正文已落入工作副本
    assert ts.changed is True
