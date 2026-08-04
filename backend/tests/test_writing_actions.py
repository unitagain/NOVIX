# -*- coding: utf-8 -*-
"""
Phase B 验收：写作动作工具集（write_content / edit_lines / finish_turn）。
纯字符串操作、无网络、无 LLM；agentic loop 段用 Fake gateway 验证 agent 自主调用写作工具。
"""

import asyncio

from app.agents.writing_actions import (
    WritingActionToolset,
    normalize_clarification_questions,
    writing_action_schemas,
)
from app.agents.agentic import run_agentic_chat


# ----------------------------------------------------------- Schema tests --


def test_schemas_cover_write_and_edit():
    names = {s["function"]["name"] for s in writing_action_schemas()}
    assert names == {"ask_clarification", "create_chapter", "write_content", "edit_lines", "finish_turn"}
    schema = next(s["function"] for s in writing_action_schemas() if s["function"]["name"] == "ask_clarification")
    questions = schema["parameters"]["properties"]["questions"]
    assert questions["minItems"] == 1
    assert questions["maxItems"] == 3


def test_toolset_schemas_without_retrieval():
    names = {s["function"]["name"] for s in WritingActionToolset("").schemas()}
    assert names == {"ask_clarification", "create_chapter", "write_content", "edit_lines", "finish_turn"}


def test_clarification_question_count_is_model_selected_and_bounded():
    for count in (1, 2, 3):
        questions = normalize_clarification_questions([{"text": f"问题 {index}"} for index in range(count)])
        assert len(questions) == count
    bounded = normalize_clarification_questions([{"text": f"问题 {index}"} for index in range(4)])
    assert len(bounded) == 3


def test_clarification_questions_are_deduplicated_without_inventing_text():
    questions = normalize_clarification_questions(
        [
            {"text": "这场冲突如何收束？"},
            {"text": " 这场冲突如何收束？ "},
            {"text": "主角继续使用第一人称吗？"},
        ]
    )

    assert [item["text"] for item in questions] == ["这场冲突如何收束？", "主角继续使用第一人称吗？"]


def test_ask_clarification_pauses_before_writing_and_blocks_followup_actions():
    tools = WritingActionToolset("旧正文")
    result = asyncio.run(
        tools.execute(
            "ask_clarification",
            {
                "questions": [
                    {"text": "这次冲突应公开爆发还是暂时压下？", "reason": "决定场景收束方式"},
                    {"text": "本章视角继续跟随林舟吗？"},
                ]
            },
        )
    )

    assert "2 个反问" in result
    assert tools.input_required is True
    assert len(tools.input_required_payload()["questions"]) == 2
    blocked = asyncio.run(tools.execute("write_content", {"content": "不应写入"}))
    assert "clarification_pending" in blocked
    assert tools.working_text == "旧正文"


def test_ask_clarification_is_rejected_after_prose_change():
    tools = WritingActionToolset("旧正文")
    asyncio.run(tools.execute("write_content", {"content": "新正文"}))
    result = asyncio.run(tools.execute("ask_clarification", {"questions": [{"text": "还要改吗？"}]}))
    assert "clarification_must_precede_writing" in result
    assert tools.input_required is False


def test_ask_clarification_is_rejected_after_chapter_targeting():
    tools = WritingActionToolset(
        "",
        existing_chapters=["V1C1"],
        require_chapter_target=True,
    )
    asyncio.run(tools.execute("create_chapter", {"title": "新章"}))

    result = asyncio.run(tools.execute("ask_clarification", {"questions": [{"text": "本章要用谁的视角？"}]}))

    assert "clarification_must_precede_writing" in result
    assert tools.input_required is False


def test_finish_turn_normalizes_terminal_payload():
    ts = WritingActionToolset("旧正文")
    asyncio.run(ts.execute("edit_lines", {"old_text": "旧", "new_text": "新"}))
    asyncio.run(
        ts.execute(
            "finish_turn",
            {
                "change_type": "plot_edit",
                "fact_operation": "merge",
                "chapter_summary": "剧情发生变化。",
                "fact_candidates": [
                    {"statement": "主角改变决定", "evidence": "新正文", "category": "人物状态"}
                ],
                "message": "已完成修改。",
            },
        )
    )

    assert ts.has_terminal_payload is True
    assert ts.terminal_payload() == {
        "change_type": "plot_edit",
        "fact_operation": "merge",
        "chapter_summary": "剧情发生变化。",
        "fact_candidates": [
            {"statement": "主角改变决定", "evidence": "新正文", "category": "人物状态"}
        ],
        "message": "已完成修改。",
    }


# ---------------------------------------------------------- write_content --


def test_create_chapter_proposes_next_id_without_persisting():
    ts = WritingActionToolset(
        "旧章正文",
        active_chapter="V1C3",
        existing_chapters=["V1C1", "V1C2", "V1C3"],
        require_chapter_target=True,
    )

    out = asyncio.run(ts.execute("create_chapter", {"title": "归途"}))

    assert "V1C4" in out
    assert ts.chapter_target() == {"chapter": "V1C4", "title": "归途", "create": True}
    assert ts.working_text == ""


def test_write_requires_chapter_target_when_runtime_enforces_it():
    ts = WritingActionToolset("", require_chapter_target=True)
    out = asyncio.run(ts.execute("write_content", {"content": "正文"}))
    assert "create_chapter" in out
    assert ts.changed is False


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
    assert names == [
        "query_canon",
        "ask_clarification",
        "create_chapter",
        "write_content",
        "edit_lines",
        "finish_turn",
    ]  # 检索在前，便于先查后写


def test_execute_delegates_unknown_to_retrieval():
    ts = WritingActionToolset("", retrieval_toolset=_FakeRetrieval())
    out = asyncio.run(ts.execute("query_canon", {"query": "x"}))
    assert "检索结果" in out


def test_unknown_tool_graceful_without_retrieval():
    out = asyncio.run(WritingActionToolset("").execute("no_such", {}))
    assert "未知工具" in out


# ---------------------------------------------------- agentic loop 集成 --


class _WritingLoopGateway:
    """先写正文，再尝试直接结束，最后按合同调用 finish_turn。"""

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
        if self.n == 2:
            return {
                "content": "已完成本章初稿。",
                "tool_calls": None,
                "usage": {},
                "model": "fake",
                "finish_reason": "stop",
            }
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "f1",
                    "type": "function",
                    "name": "finish_turn",
                    "arguments": (
                        '{"change_type":"chapter_write","fact_operation":"replace_chapter",'
                        '"chapter_summary":"张三在夜色中独自上路。","fact_candidates":[],'
                        '"message":"已完成本章初稿。"}'
                    ),
                }
            ],
            "usage": {},
            "model": "fake",
            "finish_reason": "tool_calls",
        }


def test_agent_autonomously_writes_via_tool():
    gw = _WritingLoopGateway()
    ts = WritingActionToolset("")
    resp = asyncio.run(run_agentic_chat(gw, "fake", [{"role": "user", "content": "写第一章"}], ts, max_iterations=3))
    assert resp["content"] == "已完成本章初稿。"
    assert resp["terminal_payload"]["change_type"] == "chapter_write"
    assert gw.n == 3
    assert ts.working_text == "夜色四合，张三独自上路。"  # agent 自主生成的正文已落入工作副本
    assert ts.changed is True


class _ClarificationGateway:
    async def chat(self, _messages, **_kwargs):
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "ask-1",
                    "type": "function",
                    "name": "ask_clarification",
                    "arguments": '{"questions":[{"text":"要让主角在本章暴露身份吗？"}]}',
                },
                {
                    "id": "write-1",
                    "type": "function",
                    "name": "write_content",
                    "arguments": '{"content":"这段不应执行。"}',
                },
            ],
            "usage": {},
            "model": "fake",
            "finish_reason": "tool_calls",
        }


def test_agentic_loop_stops_same_batch_after_clarification_tool():
    tools = WritingActionToolset("")
    response = asyncio.run(
        run_agentic_chat(
            _ClarificationGateway(),
            "fake",
            [{"role": "user", "content": "写这一章"}],
            tools,
            max_iterations=1,
        )
    )

    assert response.incomplete is True
    assert response.finish_reason == "clarification_requested"
    assert len(response["questions"]) == 1
    assert len(response.tool_results) == 1
    assert tools.working_text == ""


class _ReversedClarificationGateway:
    async def chat(self, _messages, **_kwargs):
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "name": "write_content",
                    "arguments": '{"content":"这段不应执行。"}',
                },
                {
                    "id": "ask-1",
                    "type": "function",
                    "name": "ask_clarification",
                    "arguments": '{"questions":[{"text":"要让主角在本章暴露身份吗？"}]}',
                },
            ],
            "usage": {},
            "model": "fake",
            "finish_reason": "tool_calls",
        }


def test_input_request_dominates_same_batch_even_when_provider_orders_write_first():
    tools = WritingActionToolset("")
    response = asyncio.run(
        run_agentic_chat(
            _ReversedClarificationGateway(),
            "fake",
            [{"role": "user", "content": "写这一章"}],
            tools,
            max_iterations=1,
        )
    )

    assert response.incomplete is True
    assert response.finish_reason == "clarification_requested"
    assert [result.tool_name for result in response.tool_results] == ["ask_clarification"]
    assert tools.working_text == ""
