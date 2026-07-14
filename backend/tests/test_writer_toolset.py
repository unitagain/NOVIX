# -*- coding: utf-8 -*-
"""
Phase 3 验收：Writer 检索工具集 + agentic 工具循环。
全部用 Fake 依赖 + asyncio.run，无网络、无真实 API key、不依赖 pytest-asyncio。
"""

import asyncio

from app.agents.tools import WriterToolset
from app.agents.agentic import run_agentic_chat
from app.agents.writing_actions import WritingActionToolset

# ---------------------------------------------------------------- Fakes ----


class _FakeDraft:
    async def get_final_draft(self, pid, ch):
        return ("迷雾森林中，张三缓缓开口。" * 8) if ch == "V1C001" else None

    async def get_latest_draft(self, pid, ch):
        return None


class _FakeAdapter:
    def __init__(self):
        self.draft = _FakeDraft()

    async def get_character_card(self, pid, name):
        return {"name": name, "性格": "沉稳", "目标": "查明真相"} if name == "张三" else None

    async def get_world_card(self, pid, name):
        return {"name": name, "规则": "外人不得擅入"} if name == "迷雾森林" else None

    async def search_text_chunks(self, pid, query, limit=5):
        return [{"chapter": "V1C001", "text": f"一段包含「{query}」的正文片段。"}]


class _Item:
    type = None

    def __init__(self, content):
        self.content = content


class _FakeSelect:
    async def retrieval_select(self, **kwargs):
        return [_Item("张三在第3章揭穿了李四的真实身份")]


def _toolset():
    return WriterToolset("p1", _FakeAdapter(), _FakeSelect(), current_chapter="V1C010", total_chapters=10)


# ----------------------------------------------------------- Toolset tests --


def test_schemas_cover_five_tools():
    names = {s["function"]["name"] for s in WriterToolset.schemas()}
    assert names == {"lookup_card", "query_canon", "query_relations", "read_chapter", "search_prose"}


def test_lookup_card_character_dict_args():
    out = asyncio.run(_toolset().execute("lookup_card", {"name": "张三"}))
    assert "张三" in out and "沉稳" in out


def test_lookup_card_world_and_missing_json_args():
    out_world = asyncio.run(_toolset().execute("lookup_card", '{"name":"迷雾森林"}'))
    assert "迷雾森林" in out_world and "外人不得擅入" in out_world
    out_missing = asyncio.run(_toolset().execute("lookup_card", '{"name":"不存在的人"}'))
    assert "未找到" in out_missing


def test_query_canon_returns_relevant_fact():
    out = asyncio.run(_toolset().execute("query_canon", {"query": "李四"}))
    assert "李四" in out


def test_query_relations_traverses_graph(tmp_path):
    import json as _json

    rel_file = tmp_path / "relations.jsonl"
    rel_file.write_text(
        _json.dumps(
            {"subject": "张三", "relation": "敌对", "object": "李四", "change": "盟友→敌对", "chapter": "V3C005"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    class _AdapterWithRelations(_FakeAdapter):
        def get_relations_path(self, pid):
            return rel_file

    ts = WriterToolset("p1", _AdapterWithRelations(), _FakeSelect())
    out = asyncio.run(ts.execute("query_relations", {"entity": "张三", "other": "李四"}))
    assert "敌对" in out and "V3C005" in out  # 关系 + 章节出处


def test_query_relations_excludes_future_relationships(tmp_path):
    import json as _json

    rel_file = tmp_path / "relations.jsonl"
    rows = [
        {"subject": "张三", "relation": "盟友", "object": "李四", "chapter": "V1C009"},
        {"subject": "张三", "relation": "敌对", "object": "李四", "chapter": "V1C011"},
    ]
    rel_file.write_text(
        "\n".join(_json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    class _AdapterWithRelations(_FakeAdapter):
        def get_relations_path(self, pid):
            return rel_file

    ts = WriterToolset("p1", _AdapterWithRelations(), _FakeSelect(), current_chapter="V1C010")
    out = asyncio.run(ts.execute("query_relations", {"entity": "张三", "other": "李四"}))

    assert "盟友" in out and "V1C009" in out
    assert "敌对" not in out and "V1C011" not in out


def test_query_relations_missing_entity_is_graceful():
    out = asyncio.run(_toolset().execute("query_relations", {}))
    assert "需要 entity" in out


def test_read_chapter_head_tail():
    out = asyncio.run(_toolset().execute("read_chapter", {"chapter_id": "V1C001"}))
    assert "V1C001" in out and "张三" in out


def test_read_chapter_rejects_future_chapter():
    out = asyncio.run(_toolset().execute("read_chapter", {"chapter_id": "V1C011"}))

    assert "拒绝读取" in out
    assert "V1C011" in out and "V1C010" in out


def test_search_prose_hits():
    out = asyncio.run(_toolset().execute("search_prose", {"query": "迷雾"}))
    assert "迷雾" in out


def test_search_prose_excludes_future_chunks_and_backfills_past_results():
    class _TemporalAdapter(_FakeAdapter):
        def __init__(self):
            super().__init__()
            self.requested_limit = 0

        async def search_text_chunks(self, pid, query, limit=5):
            self.requested_limit = limit
            rows = [
                {"chapter": "V1C012", "text": "未来片段一"},
                {"chapter": "V1C011", "text": "未来片段二"},
                {"chapter": "V1C009", "text": "过去片段一"},
                {"chapter": "V1C008", "text": "过去片段二"},
            ]
            return rows[:limit]

    adapter = _TemporalAdapter()
    ts = WriterToolset("p1", adapter, _FakeSelect(), current_chapter="V1C010")
    out = asyncio.run(ts.execute("search_prose", {"query": "片段", "top_k": 2}))

    assert adapter.requested_limit > 2
    assert "过去片段一" in out and "过去片段二" in out
    assert "未来片段" not in out


def test_unknown_tool_is_graceful():
    out = asyncio.run(_toolset().execute("no_such_tool", {}))
    assert "未知工具" in out


# ------------------------------------------------------ Agentic loop tests --


class _LoopGateway:
    """第 1 次返回 tool_call(lookup_card)，第 2 次返回最终正文；记录是否回灌了工具结果。"""

    def __init__(self):
        self.n = 0
        self.saw_tool_result = False
        self.saw_tools_param = False

    async def chat(
        self, messages, provider=None, temperature=None, max_tokens=None, retry=True, *, tools=None, **kwargs
    ):
        self.n += 1
        if tools:
            self.saw_tools_param = True
        if any(m.get("role") == "tool" for m in messages):
            self.saw_tool_result = True
        if self.n == 1:
            return {
                "content": None,
                "tool_calls": [{"id": "t1", "type": "function", "name": "lookup_card", "arguments": '{"name":"张三"}'}],
                "usage": {},
                "model": "fake",
                "finish_reason": "tool_calls",
            }
        return {"content": "最终正文", "tool_calls": None, "usage": {}, "model": "fake", "finish_reason": "stop"}


def test_agentic_loop_executes_tool_then_finishes():
    gw = _LoopGateway()
    events = []

    async def _on(ev):
        events.append(ev["type"])

    resp = asyncio.run(
        run_agentic_chat(
            gw, "fake", [{"role": "user", "content": "写第十章"}], _toolset(), max_iterations=3, on_event=_on
        )
    )
    assert resp["content"] == "最终正文"
    assert gw.saw_tools_param is True  # 工具 schema 已透传
    assert gw.saw_tool_result is True  # 工具结果被回灌给模型
    assert gw.n == 2  # 一轮工具 + 一次产出
    assert "tool_call" in events and "tool_result" in events


class _StreamingActionGateway:
    def __init__(self):
        self.calls = 0

    async def agentic_chat(self, messages, *, on_stream_event=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            fragments = ['{"content":"流式', '正文第一段。', '","mode":"replace"}']
            for fragment in fragments:
                await on_stream_event(
                    {
                        "type": "tool_call_delta",
                        "index": 0,
                        "id": "call-1" if fragment == fragments[0] else "",
                        "name": "write_content" if fragment == fragments[0] else "",
                        "arguments": fragment,
                    }
                )
            return {
                "provider": "openai",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "name": "write_content",
                        "arguments": "".join(fragments),
                    }
                ],
                "usage": {},
                "finish_reason": "tool_calls",
            }
        await on_stream_event({"type": "content_delta", "content": "完成"})
        return {
            "provider": "openai",
            "content": "完成",
            "tool_calls": None,
            "usage": {},
            "finish_reason": "stop",
        }


def test_agentic_streams_provisional_writing_tool_content_before_execution():
    gateway = _StreamingActionGateway()
    toolset = WritingActionToolset("")
    events = []

    async def on_event(event):
        events.append(event)

    result = asyncio.run(
        run_agentic_chat(
            gateway,
            "openai",
            [{"role": "user", "content": "写"}],
            toolset,
            max_iterations=2,
            on_event=on_event,
        )
    )
    streamed = "".join(
        str(event.get("content") or "") for event in events if event.get("type") == "provisional_content"
    )
    assert streamed.startswith("流式正文第一段。")
    assert toolset.working_text == "流式正文第一段。"
    assert result.success is True


# ------------------------------------- thinking + Anthropic format (5A) --


class _ThinkingGateway:
    """第 1 次返回 thinking + tool_call，第 2 次返回最终；记录回放消息以验格式。"""

    def __init__(self, provider_name="fake"):
        self.n = 0
        self.provider_name = provider_name
        self.replayed = []

    async def chat(
        self,
        messages,
        provider=None,
        temperature=None,
        max_tokens=None,
        retry=True,
        *,
        tools=None,
        thinking=None,
        **kwargs,
    ):
        self.n += 1
        if self.n == 1:
            return {
                "content": None,
                "thinking": "我需要先查张三的设定再动笔",
                "tool_calls": [{"id": "t1", "type": "function", "name": "lookup_card", "arguments": '{"name":"张三"}'}],
                "provider": self.provider_name,
                "usage": {},
                "model": "fake",
                "finish_reason": "tool_calls",
            }
        self.replayed = list(messages)
        return {
            "content": "写作须知：张三沉稳。",
            "tool_calls": None,
            "provider": self.provider_name,
            "usage": {},
            "model": "fake",
            "finish_reason": "stop",
        }


def test_agentic_emits_thinking_event():
    gw = _ThinkingGateway()
    events = []

    async def _on(ev):
        events.append(ev)

    resp = asyncio.run(
        run_agentic_chat(gw, "fake", [{"role": "user", "content": "写"}], _toolset(), max_iterations=3, on_event=_on)
    )
    assert resp["content"].startswith("写作须知")
    types = [e["type"] for e in events]
    assert "thinking" in types and "tool_call" in types and "tool_result" in types
    thinking_ev = next(e for e in events if e["type"] == "thinking")
    assert "张三" in thinking_ev["content"]  # 真实思考内容（非伪进度）


def test_agentic_anthropic_replay_uses_block_format():
    """Anthropic provider 下，回放消息用 tool_use/tool_result 内容块（非 OpenAI 格式）。"""
    gw = _ThinkingGateway(provider_name="anthropic")
    asyncio.run(run_agentic_chat(gw, "claude", [{"role": "user", "content": "写"}], _toolset(), max_iterations=3))
    assistant_msg = next(m for m in gw.replayed if m.get("role") == "assistant" and isinstance(m.get("content"), list))
    assert any(b.get("type") == "tool_use" for b in assistant_msg["content"])  # assistant 用 tool_use 块
    user_msg = next(m for m in gw.replayed if m.get("role") == "user" and isinstance(m.get("content"), list))
    assert any(b.get("type") == "tool_result" for b in user_msg["content"])  # 结果用 tool_result 块
    # 不应出现 OpenAI 风格的 role:"tool" 消息
    assert not any(m.get("role") == "tool" for m in gw.replayed)
