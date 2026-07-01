# -*- coding: utf-8 -*-
"""Phase 11 · Plan 编排引擎回归测试（持久化 / 规划 / 意图三件套）。

无网络 / 无 key：用 FakeGateway。串行执行器的端到端在 test_plan_executor 另测。
"""

import asyncio

from app.storage.plan_store import PlanStore
from app.agents.planner import generate_plan
from app.agents.intent import classify_writing_intent

# ---------- PlanStore ----------


def test_plan_store_write_read(tmp_path):
    s = PlanStore(str(tmp_path))
    plan = {
        "id": "p1",
        "goal": "在6-8章回收第3章伏笔",
        "steps": [{"id": 1, "action": "write", "description": "写第6章", "status": "pending"}],
        "status": "running",
    }
    asyncio.run(s.write_plan("proj", plan))
    got = asyncio.run(s.read_plan("proj", "p1"))
    assert got["goal"] == "在6-8章回收第3章伏笔"
    assert len(got["steps"]) == 1


def test_plan_store_update_step_persists(tmp_path):
    s = PlanStore(str(tmp_path))
    plan = {
        "id": "p1",
        "goal": "g",
        "steps": [{"id": 1, "action": "write", "description": "d", "status": "pending"}],
        "status": "running",
    }
    asyncio.run(s.write_plan("proj", plan))
    ok = asyncio.run(s.update_step("proj", "p1", 1, "done", result="ok"))
    assert ok
    got = asyncio.run(s.read_plan("proj", "p1"))
    assert got["steps"][0]["status"] == "done"
    assert got["steps"][0]["result"] == "ok"


def test_plan_store_list(tmp_path):
    s = PlanStore(str(tmp_path))
    asyncio.run(s.write_plan("proj", {"id": "p1", "goal": "g1", "steps": [], "status": "done"}))
    plans = asyncio.run(s.list_plans("proj"))
    assert len(plans) == 1 and plans[0]["id"] == "p1"


def test_plan_store_read_missing_returns_none(tmp_path):
    assert asyncio.run(PlanStore(str(tmp_path)).read_plan("proj", "nope")) is None


# ---------- planner ----------


class _FakeGateway:
    def __init__(self, content):
        self.content = content

    async def chat(self, *args, **kwargs):
        return {"content": self.content}


def test_generate_plan_parses_steps():
    gw = _FakeGateway(
        '{"steps":[{"action":"research","description":"查第3章伏笔"},'
        '{"action":"write","description":"写第6章回收","chapter":"V1C006"}]}'
    )
    steps = asyncio.run(generate_plan(gw, "fake", "在6-8章回收第3章伏笔"))
    assert len(steps) == 2
    assert steps[0]["action"] == "research"
    assert steps[1]["chapter"] == "V1C006"
    assert all(s["status"] == "pending" for s in steps)
    assert [s["id"] for s in steps] == [1, 2]  # 顺序编号


def test_generate_plan_empty_goal_skips_llm():
    assert asyncio.run(generate_plan(_FakeGateway('{"steps":[]}'), "fake", "")) == []


def test_generate_plan_bad_json_returns_empty():
    assert asyncio.run(generate_plan(_FakeGateway("不是 JSON"), "fake", "写点东西")) == []


def test_generate_plan_filters_invalid_action():
    gw = _FakeGateway('{"steps":[{"action":"fly","description":"x"},{"action":"write","description":"写"}]}')
    steps = asyncio.run(generate_plan(gw, "fake", "g"))
    assert len(steps) == 1 and steps[0]["action"] == "write"


# ---------- intent plan branch ----------


def test_intent_detects_multichapter_plan():
    d = asyncio.run(classify_writing_intent("在6-8章回收第3章的伏笔", has_selection=False, has_draft=True))
    assert d["action"] == "plan"


def test_intent_plan_hint():
    d = asyncio.run(classify_writing_intent("逐章把这条支线写完", has_selection=False, has_draft=True))
    assert d["action"] == "plan"


def test_intent_selection_beats_plan():
    # 有选中优先 edit/selection，即使含 plan 关键词
    d = asyncio.run(classify_writing_intent("逐章修改", has_selection=True, has_draft=True))
    assert d["action"] == "edit"


def test_intent_simple_still_write():
    d = asyncio.run(classify_writing_intent("写一个新场景", has_selection=False, has_draft=False))
    assert d["action"] == "write"
