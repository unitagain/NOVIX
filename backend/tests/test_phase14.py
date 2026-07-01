# -*- coding: utf-8 -*-
"""Phase 14 · canon 治理 + 权限回归测试。

验证：③ Fact 风险等级（默认 confirmed 向后兼容 / AI 抽取 needs_review）+ confirm_facts；
④ 权限分级策略。验收核心：抽错事实进 needs_review 不污染 confirmed 主 canon。无网络 / 无 key。
"""

import asyncio

from app.schemas.canon import Fact
from app.storage.canon import CanonStorage
from app.utils.permissions import permission_for, is_allowed, is_denied, requires_confirmation

# ---------- 动作③ Fact 风险等级 ----------


def test_fact_default_status_confirmed():
    """旧数据 / 作者数据默认 confirmed（向后兼容）。"""
    f = Fact(id="F1", statement="x", source="c1", introduced_in="c1")
    assert f.status == "confirmed"


def test_fact_explicit_needs_review():
    f = Fact(id="F1", statement="x", source="c1", introduced_in="c1", status="needs_review")
    assert f.status == "needs_review"


def test_confirm_facts_only_targets(tmp_path):
    """confirm 只把指定事实转 confirmed，其余 needs_review 保持——抽错的不被误确认。"""
    s = CanonStorage(str(tmp_path))
    asyncio.run(
        s.add_fact("p1", Fact(id="F1", statement="AI 抽的", source="c1", introduced_in="c1", status="needs_review"))
    )
    asyncio.run(
        s.add_fact("p1", Fact(id="F2", statement="另一条", source="c1", introduced_in="c1", status="needs_review"))
    )
    n = asyncio.run(s.confirm_facts("p1", ["F1"]))
    assert n == 1
    by_id = {f.id: f for f in asyncio.run(s.get_all_facts("p1"))}
    assert by_id["F1"].status == "confirmed"
    assert by_id["F2"].status == "needs_review"  # 未确认的仍是待审，不污染


def test_confirm_facts_missing_id_noop(tmp_path):
    s = CanonStorage(str(tmp_path))
    asyncio.run(s.add_fact("p1", Fact(id="F1", statement="x", source="c1", introduced_in="c1", status="needs_review")))
    assert asyncio.run(s.confirm_facts("p1", ["NOPE"])) == 0


# ---------- 动作④ 权限分级 ----------


def test_permission_readonly_allow():
    assert permission_for("query_canon") == "allow"
    assert is_allowed("read_chapter")
    assert not requires_confirmation("query_relations")
    assert not is_denied("query_relations")


def test_permission_write_ask():
    assert permission_for("edit_chapter") == "ask"
    assert not is_allowed("add_fact")
    assert requires_confirmation("add_fact")
    assert not is_denied("add_fact")


def test_permission_delete_deny():
    assert permission_for("delete_chapter") == "deny"
    assert is_denied("delete_fact")
    assert not requires_confirmation("delete_fact")


def test_permission_unknown_defaults_ask():
    assert permission_for("some_unknown_op") == "ask"  # 保守：未知不放行
    assert not is_allowed("some_unknown_op")
    assert requires_confirmation("some_unknown_op")
