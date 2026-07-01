# -*- coding: utf-8 -*-
"""对话历史持久化（Git-Native）回归测试：append/load/replace/count + compact 长对话压缩。
无网络、无 LLM；compact 的 summarizer 用 Fake 注入。
"""

import asyncio

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


def test_compact_does_not_recompress_existing_summary(tmp_path):
    s = _store(tmp_path)
    for i in range(30):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=10, trigger_at=20))
    # 再加 20 条触发二次压缩；已有 summary 应保留在头部、不被重复压
    for i in range(30, 50):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))
    asyncio.run(s.compact("p1", _fake_summarizer, keep_recent=10, trigger_at=20))
    items = asyncio.run(s.load("p1"))
    summaries = [m for m in items if m.get("type") == "summary"]
    assert len(summaries) == 2  # 头部既有摘要 + 新摘要，二者并存
    assert [m["content"] for m in items if m.get("type") != "summary"][-3:] == ["m47", "m48", "m49"]


def test_compact_summarizer_failure_is_safe(tmp_path):
    s = _store(tmp_path)
    for i in range(30):
        asyncio.run(s.append("p1", {"role": "user", "content": f"m{i}"}))

    async def _boom(_msgs):
        raise RuntimeError("llm down")

    r = asyncio.run(s.compact("p1", _boom, keep_recent=10, trigger_at=20))
    assert r["compacted"] is False
    assert asyncio.run(s.count("p1")) == 30  # 失败不丢数据
