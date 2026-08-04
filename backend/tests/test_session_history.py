# -*- coding: utf-8 -*-
"""对话历史持久化（Git-Native）回归测试：append/load/replace/count + compact 长对话压缩。
无网络、无 LLM；compact 的 summarizer 用 Fake 注入。
"""

import asyncio
import json

from app.context_engine.token_accounting import count_provider_payload
from app.storage.session_history import SessionHistoryStorage


def _store(tmp_path):
    return SessionHistoryStorage(str(tmp_path))


def test_append_and_load_roundtrip(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.append("p1", {"role": "user", "content": "写第一章"}))
    asyncio.run(s.append("p1", {"role": "assistant", "content": "已完成"}))
    items = asyncio.run(s.load("p1"))
    assert [m["role"] for m in items] == ["user", "assistant"]
    assert items[0]["content"] == "写第一章"
    assert all(isinstance(m["ts"], int) and m["ts"] > 0 for m in items)


def test_load_empty_project_is_empty_list(tmp_path):
    assert asyncio.run(_store(tmp_path).load("nope")) == []


def test_normalize_role_and_type(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.append("p1", {"role": "weird", "content": "x", "type": "summary"}))
    item = asyncio.run(s.load("p1"))[0]
    assert item["role"] == "user"  # 非法 role 归一为 user
    assert item["type"] == "summary"


def test_load_limit_returns_recent(tmp_path):
    s = _store(tmp_path)
    for i in range(10):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    recent = asyncio.run(s.load("p1", limit=3))
    assert [m["content"] for m in recent] == ["m7", "m8", "m9"]


def test_replace_overwrites(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.append("p1", {"role": "user", "content": "old"}))
    asyncio.run(s.replace("p1", [{"role": "system", "content": "fresh"}]))
    items = asyncio.run(s.load("p1"))
    assert len(items) == 1 and items[0]["content"] == "fresh"


def test_count(tmp_path):
    s = _store(tmp_path)
    for _ in range(5):
        asyncio.run(s.append("p1", {"role": "user", "content": "x"}))
    assert asyncio.run(s.count("p1")) == 5


def test_multiple_conversations_are_isolated_and_switchable(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.append("p1", {"role": "user", "content": "legacy"}))
    created = asyncio.run(s.create_conversation("p1", title="第二条线"))
    asyncio.run(s.append("p1", {"role": "user", "content": "new"}))

    assert [item["content"] for item in asyncio.run(s.load("p1"))] == ["new"]
    assert [item["content"] for item in asyncio.run(s.load("p1", conversation_id="legacy"))] == ["legacy"]
    conversations = asyncio.run(s.list_conversations("p1"))
    assert any(item["id"] == created["id"] and item["active"] for item in conversations)

    asyncio.run(s.activate_conversation("p1", "legacy"))
    assert [item["content"] for item in asyncio.run(s.load("p1"))] == ["legacy"]


def test_activate_unknown_conversation_fails_without_changing_active(tmp_path):
    s = _store(tmp_path)
    created = asyncio.run(s.create_conversation("p1"))
    try:
        asyncio.run(s.activate_conversation("p1", "missing"))
        assert False, "expected missing conversation to fail"
    except FileNotFoundError:
        pass
    assert s.active_conversation_id("p1") == created["id"]


def test_rollback_last_turn_removes_user_message_and_following_responses(tmp_path):
    s = _store(tmp_path)
    created = asyncio.run(s.create_conversation("p1", title="回退测试"))
    conversation_id = created["id"]
    asyncio.run(s.append("p1", {"role": "user", "content": "第一轮"}, conversation_id=conversation_id))
    asyncio.run(s.append("p1", {"role": "assistant", "content": "第一轮完成"}, conversation_id=conversation_id))
    asyncio.run(s.append("p1", {"role": "user", "content": "第二轮"}, conversation_id=conversation_id))
    asyncio.run(s.append("p1", {"role": "system", "content": "本轮执行失败"}, conversation_id=conversation_id))

    result = asyncio.run(s.rollback_last_turn("p1", conversation_id))

    assert result == {"removed": 2, "conversation_id": conversation_id, "restored_input": "第二轮"}
    assert [item["content"] for item in asyncio.run(s.load("p1", conversation_id=conversation_id))] == [
        "第一轮",
        "第一轮完成",
    ]


# ---------------------------------------------------------------- compact --


async def _fake_summarizer(messages):
    return f"摘要：压缩了 {len(messages)} 条早期对话。"


def test_compact_noop_below_trigger(tmp_path):
    s = _store(tmp_path)
    for i in range(10):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    r = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=5, trigger_at=50))
    assert r["compacted"] is False and r["total"] == 10
    assert asyncio.run(s.count("p1")) == 10  # 未改动


def test_compact_folds_old_keeps_recent(tmp_path):
    s = _store(tmp_path)
    for i in range(30):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    r = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=10, trigger_at=20))
    assert r["compacted"] is True
    assert r["summarized"] == 20  # 最旧 20 条被压
    items = asyncio.run(s.load("p1"))
    # 结构：1 条 summary + 近 10 条原文
    assert len(items) == 11
    assert items[0]["type"] == "summary" and "压缩了 20 条" in items[0]["content"]
    assert items[0]["compacted_count"] == 20
    assert [m["content"] for m in items[1:]] == [f"m{i}" for i in range(20, 30)]


def test_compact_advances_epoch_and_replaces_runtime_projection(tmp_path):
    s = _store(tmp_path)
    for i in range(30):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    first = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=10, trigger_at=20))
    # 再加 20 条触发二次压缩；运行时只保留新 epoch 的投影，旧 artifact 仍可恢复。
    for i in range(30, 50):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    second = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=10, trigger_at=20))
    items = asyncio.run(s.load("p1"))
    summaries = [m for m in items if m.get("type") == "summary"]
    assert len(summaries) == 1
    assert summaries[0]["context_epoch"] == 2
    assert summaries[0]["parent_epoch"] == 1
    assert [m["content"] for m in items if m.get("type") != "summary"][-3:] == ["m47", "m48", "m49"]
    assert first["lineage_verification"]["depth"] == 0
    assert second["lineage_verification"]["depth"] == 1
    assert second["layers"]["previous_compact_artifact_id"] == first["compact_artifact_id"]


def test_compact_summarizer_failure_is_safe(tmp_path):
    s = _store(tmp_path)
    for i in range(30):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))

    async def _boom(_msgs):
        raise RuntimeError("llm down")

    r = asyncio.run(s.compact("p1", _boom, keep_recent=10, trigger_at=20))
    assert r["compacted"] is False
    assert asyncio.run(s.count("p1")) == 30  # 失败不丢数据


def test_compact_preserves_complete_user_assistant_tool_turn(tmp_path):
    s = _store(tmp_path)
    first_turn = [
        {"role": "user", "content": "用户请求" * 80, "event_id": "u1"},
        {
            "role": "assistant",
            "content": "调用工具" * 80,
            "event_id": "a1",
            "tool_calls": [{"id": "call-1", "name": "query_canon"}],
        },
        {
            "role": "tool",
            "content": "工具结果" * 80,
            "event_id": "t1",
            "tool_call_id": "call-1",
            "name": "query_canon",
        },
        {"role": "assistant", "content": "最终答复" * 80, "event_id": "a2"},
    ]
    second_turn = [
        {"role": "user", "content": "新请求" * 80, "event_id": "u2"},
        {"role": "assistant", "content": "新答复" * 80, "event_id": "a3"},
    ]
    for message in [*first_turn, *second_turn]:
        asyncio.run(s.append("p1", message))
    captured = []

    async def summarize(messages):
        captured.extend(messages)
        return "完整保留早期工具回合。"

    result = asyncio.run(s.compact("p1", summarize, keep_recent=1, trigger_at=1))
    assert result["compacted"] is True
    assert [item["event_id"] for item in captured] == ["u1", "a1", "t1", "a2"]
    assert result["summarized_turns"] == 1
    assert result["preserved_tail_id"] == "turn_u2"
    artifact = asyncio.run(s.read_compact_artifact("p1", result["compact_artifact_id"]))
    assert artifact["selected_turn_ids"] == ["turn_u1"]
    recovered = asyncio.run(s.recover_compact_sources("p1", artifact["id"]))
    assert [item["event_id"] for item in recovered] == [
        "u1",
        "a1",
        "t1",
        "a2",
    ]
    assert recovered[1]["tool_calls"][0]["id"] == "call-1"
    assert recovered[2]["tool_call_id"] == "call-1"


def test_recent_tail_obeys_turn_count_and_token_budget_deterministically(tmp_path):
    s = _store(tmp_path)
    for index in range(5):
        asyncio.run(
            s.append(
                "p1",
                {"role": "user", "content": f"U{index}-" + "甲" * 400, "event_id": f"u{index}"},
            )
        )
        asyncio.run(
            s.append(
                "p1",
                {"role": "assistant", "content": f"A{index}-" + "乙" * 400, "event_id": f"a{index}"},
            )
        )
    items = asyncio.run(s.load("p1"))
    two_turn_budget = count_provider_payload(
        items[-4:],
        provider="deepseek",
        model="deepseek-chat",
    ).upper_bound_tokens

    result = asyncio.run(
        s.compact(
            "p1",
            _fake_summarizer,
            keep_recent=4,
            trigger_at=1,
            recent_token_budget=two_turn_budget,
            provider="deepseek",
            model="deepseek-chat",
        )
    )
    assert result["preserved_tail_turns"] == 2
    assert result["preserved_tail_id"] == "turn_u3"
    assert result["accounting"]["recent_tail"]["upper_bound_tokens"] <= two_turn_budget
    assert result["layers"] == {
        "previous_compact_messages": 0,
        "previous_compact_artifact_id": "",
        "selected_raw_messages": 6,
        "selected_raw_turns": 3,
        "recent_tail_messages": 4,
        "recent_tail_turns": 2,
    }


def test_single_oversized_recent_turn_is_preserved_without_splitting(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.append("p1", {"role": "user", "content": "old" * 500, "event_id": "u-old"}))
    asyncio.run(s.append("p1", {"role": "assistant", "content": "old-a" * 500, "event_id": "a-old"}))
    asyncio.run(s.append("p1", {"role": "user", "content": "new" * 3000, "event_id": "u-new"}))
    asyncio.run(s.append("p1", {"role": "assistant", "content": "new-a" * 3000, "event_id": "a-new"}))

    result = asyncio.run(
        s.compact("p1", _fake_summarizer, keep_recent=2, trigger_at=1, recent_token_budget=10)
    )
    assert result["compacted"] is True
    assert result["tail_budget_overflow"] is True
    active = asyncio.run(s.load("p1"))
    assert [item["event_id"] for item in active[1:]] == ["u-new", "a-new"]


def test_empty_or_overflow_summary_never_rewrites_projection(tmp_path):
    s = _store(tmp_path)
    for index in range(4):
        asyncio.run(s.append("p1", {"role": "user", "content": f"source-{index}-" + "甲" * 300}))
    before = asyncio.run(s.load("p1"))

    async def empty(_messages):
        return ""

    empty_result = asyncio.run(s.compact("p1", empty, keep_recent=1, trigger_at=1))
    assert empty_result["error"] == "compact_empty_summary"
    assert asyncio.run(s.load("p1")) == before

    async def overflow(_messages):
        return "膨胀摘要" * 10000

    overflow_result = asyncio.run(s.compact("p1", overflow, keep_recent=1, trigger_at=1))
    assert overflow_result["error"] == "compact_summary_overflow"
    assert asyncio.run(s.load("p1")) == before
    assert asyncio.run(s.current_context_epoch("p1")) == 0


def test_compact_lineage_cycle_blocks_next_epoch(tmp_path):
    s = _store(tmp_path)
    for index in range(4):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{index}-" + "甲" * 200}))
    first = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=1, trigger_at=1))
    artifact_path = tmp_path / "p1" / "sessions" / "compact" / f"{first['compact_artifact_id']}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["parent_artifact_id"] = artifact["id"]
    artifact["parent_epoch"] = artifact["epoch"]
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    for index in range(4, 7):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{index}-" + "乙" * 200}))
    before = asyncio.run(s.load("p1"))

    result = asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=1, trigger_at=1))
    assert result["error"] == "compact_lineage_verification_failed"
    assert "lineage_cycle" in result["lineage_verification"]["errors"]
    assert asyncio.run(s.load("p1")) == before
    assert asyncio.run(s.current_context_epoch("p1")) == 1
