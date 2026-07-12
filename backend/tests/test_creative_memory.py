# -*- coding: utf-8 -*-
"""Phase 10 · 创作 Memory 持久层回归测试。

验证：① 写/读单条 ② MEMORY.md 索引重建 ③ list_headers 不含 body（JIT 索引层）
④ recall 命中相关偏好 ⑤ 跨会话（新 storage 实例）复用。无网络 / 无 key。
"""

import asyncio

from app.storage.creative_memory import CreativeMemoryStorage


def _store(tmp_path):
    return CreativeMemoryStorage(str(tmp_path))


def test_write_and_read_memory(tmp_path):
    s = _store(tmp_path)
    slug = asyncio.run(s.write_memory("p1", "short dialogue", "作者偏好短句对白", "多次要求对白简短有力", "preference"))
    assert slug == "short-dialogue"  # 空白被规范化
    mem = asyncio.run(s.read_memory("p1", slug))
    assert mem["description"] == "作者偏好短句对白"
    assert mem["type"] == "preference"
    assert "对白简短" in mem["body"]


def test_index_rebuilt_on_write(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_memory("p1", "m1", "desc one", "body one", "preference"))
    asyncio.run(s.write_memory("p1", "m2", "desc two", "body two", "progress"))
    idx = asyncio.run(s.read_index("p1"))
    assert "desc one" in idx and "desc two" in idx
    assert "(progress)" in idx


def test_list_headers_excludes_body(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_memory("p1", "m1", "偏好短句", "很长的正文 body 内容不应出现在 header", "preference"))
    headers = asyncio.run(s.list_headers("p1"))
    assert len(headers) == 1
    assert headers[0]["description"] == "偏好短句"
    assert "body" not in headers[0]  # header 层不含 body（JIT）


def test_recall_hits_relevant_memory(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_memory("p1", "dialogue", "作者偏好短句对白少形容词", "...", "preference"))
    asyncio.run(s.write_memory("p1", "pacing", "第二卷主打悬疑节奏", "...", "decision"))
    hits = asyncio.run(s.recall("p1", "这段对白该怎么写", top_k=3))
    assert hits
    assert hits[0]["name"] == "dialogue"  # 对白相关召回最前


def test_recall_empty_when_no_match(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_memory("p1", "dialogue", "作者偏好短句对白", "...", "preference"))
    assert asyncio.run(s.recall("p1", "xyz123zzz", top_k=3)) == []


def test_cross_session_reuse(tmp_path):
    """模拟跨会话：实例 A 写入，新实例 B（新会话）仍能召回。"""
    s1 = _store(tmp_path)
    asyncio.run(s1.write_memory("p1", "tone", "作者喜欢冷峻文风", "避免煽情", "preference"))
    s2 = CreativeMemoryStorage(str(tmp_path))  # 新实例 = 新会话
    hits = asyncio.run(s2.recall("p1", "文风偏好", top_k=3))
    assert any("冷峻文风" in h["description"] for h in hits)


def test_upsert_overwrites_same_slug(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_memory("p1", "tone", "旧描述", "旧 body", "preference"))
    asyncio.run(s.write_memory("p1", "tone", "新描述", "新 body", "decision"))
    mem = asyncio.run(s.read_memory("p1", "tone"))
    assert mem["description"] == "新描述" and mem["type"] == "decision"
    headers = asyncio.run(s.list_headers("p1"))
    assert len(headers) == 1  # 同 slug 覆盖，不重复


def test_candidate_memory_waits_for_review_and_not_recalled(tmp_path):
    s = _store(tmp_path)
    asyncio.run(
        s.write_candidate_memory(
            "p1",
            "auto-tone",
            "AI 推断作者喜欢冷峻文风",
            "来自章节定稿后的自动抽取",
            "preference",
            source="chapter_finalize:V1C001",
        )
    )
    assert asyncio.run(s.recall("p1", "冷峻文风", top_k=3)) == []
    review_items = asyncio.run(s.list_review_items("p1"))
    assert len(review_items) == 1
    assert review_items[0]["status"] == "needs_review"
    assert review_items[0]["source"] == "chapter_finalize:V1C001"


def test_confirm_and_reject_memory_status(tmp_path):
    s = _store(tmp_path)
    asyncio.run(s.write_candidate_memory("p1", "tone", "作者喜欢冷峻文风", "避免煽情", "preference"))
    assert asyncio.run(s.confirm_memory("p1", "tone"))
    hits = asyncio.run(s.recall("p1", "文风", top_k=3))
    assert hits and hits[0]["status"] == "active"
    assert "recall_reason" in hits[0]

    asyncio.run(s.write_candidate_memory("p1", "bad", "错误偏好", "误抽取", "preference"))
    assert asyncio.run(s.reject_memory("p1", "bad"))
    rejected = asyncio.run(s.read_memory("p1", "bad"))
    assert rejected["status"] == "rejected"
    assert asyncio.run(s.recall("p1", "错误偏好", top_k=3)) == []
