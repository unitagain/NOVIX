# -*- coding: utf-8 -*-
"""Phase 12 / 阶段 C · 单 Writer 主循环（统一对话入口）回归测试。

验证 intent continue 分支 + run_chat_turn 的新编排：
  - 默认主路径：写/改统一交给 agent 主循环（run_writing_agent → agentic_write），对齐 AI coding；
  - 能力降级（红线 4）：agent 返回 fallback → 回退既有预判路由（start_session / process_feedback）；
  - plan 仍走显式拆解。
全部用实例属性覆盖 + Fake gateway，无网络、无真实 LLM。
"""

import asyncio

from app.orchestrator.orchestrator import Orchestrator
from app.agents.intent import classify_writing_intent

# ---------- intent continue ----------


def test_intent_continue_when_draft_exists():
    d = asyncio.run(classify_writing_intent("接着写下去", has_selection=False, has_draft=True))
    assert d["action"] == "continue"


def test_intent_continue_without_draft_is_write():
    d = asyncio.run(classify_writing_intent("接着写", has_selection=False, has_draft=False))
    assert d["action"] == "write"


# ---------- run_chat_turn 编排 ----------


def _orch(tmp_path):
    return Orchestrator(str(tmp_path))


def test_chat_turn_agent_is_default_path(tmp_path):
    """默认主路径：run_chat_turn 把写/改统一交给 agent 主循环（agentic_write）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "write"}

    async def fake_agent(pid, ch, msg, **k):
        return {"success": True, "action": "agentic_write", "changed": True}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "写个新场景"))
    assert r["action"] == "agentic_write" and r["success"]
    assert r["route_contract"]["path"] == "agentic_writer"
    assert r["context_plan"]["route_path"] == "agentic_writer"
    assert any(t["name"] == "write_content" for t in r["context_plan"]["tool_loadout"])
    assert "latency_ms" in r["context_plan"]["budget"]
    assert "tool_calls" in r["context_plan"]["budget"]
    assert "llm_requests" in r["context_plan"]["budget"]
    assert (tmp_path / "p" / r["trace_ref"]).exists()


def test_chat_turn_passes_only_writing_service_contract_arguments(tmp_path):
    orch = _orch(tmp_path)

    async def fake_decide(*_args, **_kwargs):
        return {"action": "continue"}

    async def exact_service(pid, chapter, message, *, has_selection=False, thinking=False, target_word_count=3000):
        assert (pid, chapter, message) == ("p", "V1C001", "continue")
        assert has_selection is False
        assert thinking is False
        assert target_word_count == 180
        return {"success": True, "action": "agentic_write"}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = exact_service
    result = asyncio.run(
        orch.run_chat_turn("p", "V1C001", "continue", has_draft=True, target_word_count=180)
    )
    assert result["success"] is True


def test_chat_turn_fallback_signals_write(tmp_path):
    """能力降级：agent fallback → run_chat_turn 返回 fallback 信号（前端用成熟 write 流程兜底）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "write"}

    async def fake_agent(pid, ch, msg, **k):
        return {"success": False, "fallback": True, "reason": "no_tool_calls"}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "写个新场景"))
    assert r.get("fallback") is True and r["action"] == "write"
    assert r["route_contract"]["path"] == "fallback_workflow"
    assert r["context_plan"]["degradation"][0]["status"] == "fallback"


def test_chat_turn_context_plan_includes_actual_ranking_trace(tmp_path):
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "write"}

    async def fake_agent(pid, ch, msg, **k):
        orch.select_engine._last_ranking_trace = {
            "query": "张三 李四",
            "fusion": "lexical",
            "top_results": [{"id": "F1", "score": 1.0}],
        }
        return {"success": True, "action": "agentic_write", "changed": True}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "写个新场景"))
    assert r["context_plan"]["ranking"]["actual"]["top_results"][0]["id"] == "F1"


def test_chat_turn_fallback_signals_edit(tmp_path):
    """能力降级 + edit 意图：agent fallback → 返回 fallback 信号（前端走 editor 精确 diff）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "edit"}

    async def fake_agent(pid, ch, msg, **k):
        return {"fallback": True}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "改下措辞", has_draft=True))
    assert r.get("fallback") is True and r["action"] == "edit"
    assert r["route_contract"]["fallback"] is True


def test_chat_turn_fallback_signals_continue(tmp_path):
    """能力降级 + continue：agent fallback → 返回 fallback 信号（前端续写）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "continue"}

    async def fake_agent(pid, ch, msg, **k):
        return {"fallback": True}

    orch.decide_writing_action = fake_decide
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "接着写", has_draft=True))
    assert r.get("fallback") is True and r["action"] == "continue"


def test_chat_turn_routes_plan(tmp_path):
    """plan 仍走显式拆解（不进 agent 写作主循环）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "plan"}

    async def fake_create(pid, goal, **k):
        return {"id": "p1", "steps": []}

    orch.decide_writing_action = fake_decide
    orch.application.plans.create_plan = fake_create
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "在6-8章回收伏笔"))
    assert r["action"] == "plan" and r["plan"]["id"] == "p1"
    assert r["route_contract"]["path"] == "plan_workflow"
    assert r["context_plan"]["route_path"] == "plan_workflow"


def test_chat_turn_plan_unsplittable_then_fallback_signal(tmp_path):
    """plan 拆不出 → 转写作；agent 也降级 → 返回 fallback 信号（前端兜底）。"""
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "plan"}

    async def fake_create(pid, goal, **k):
        return None  # 拆不出可执行步骤

    async def fake_agent(pid, ch, msg, **k):
        return {"fallback": True}

    orch.decide_writing_action = fake_decide
    orch.application.plans.create_plan = fake_create
    orch.writing_service.run = fake_agent
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "随便写点"))
    assert r.get("fallback") is True and r["action"] == "write"


def test_chat_turn_auto_execute_plan(tmp_path):
    orch = _orch(tmp_path)

    async def fake_decide(*a, **k):
        return {"action": "plan"}

    async def fake_create(pid, goal, **k):
        return {"id": "p1", "steps": []}

    async def fake_exec(pid, plan_id, **k):
        return {"success": True, "plan": {"status": "done"}}

    orch.decide_writing_action = fake_decide
    orch.create_plan = fake_create
    orch.execute_plan = fake_exec
    r = asyncio.run(orch.run_chat_turn("p", "V1C001", "在6-8章回收", auto_execute_plan=True))
    assert r["action"] == "plan" and r["execution"]["success"]
    assert "permission_gate" in [s["stage"] for s in r["route_contract"]["stages"]]


# ---------- run_writing_agent（阶段 C 核心：agent 自主用写作工具） ----------


class _AgentGateway:
    """Fake gateway：第 1 轮让 agent 调 write_content（自己生成 content），第 2 轮收尾。"""

    def get_provider_for_agent(self, name):
        return "fake"

    async def chat(self, messages, provider=None, temperature=None, max_tokens=None, *, tools=None, **kwargs):
        if any(m.get("role") == "tool" for m in messages):  # 已回灌工具结果 → 收尾
            return {"content": "已完成本章。", "tool_calls": None, "usage": {}, "model": "fake", "finish_reason": "stop"}
        return {
            "content": None,
            "tool_calls": [
                {"id": "w1", "type": "function", "name": "write_content", "arguments": '{"content":"夜色四合。"}'}
            ],
            "usage": {},
            "model": "fake",
            "finish_reason": "tool_calls",
        }


class _NoToolGateway:
    """Fake gateway：provider 不调任何工具（模拟不支持工具）→ run_writing_agent 应 fallback。"""

    def get_provider_for_agent(self, name):
        return "fake"

    async def chat(self, messages, provider=None, temperature=None, max_tokens=None, *, tools=None, **kwargs):
        return {"content": "（直接回答，未调工具）", "tool_calls": None, "usage": {}, "model": "fake", "finish_reason": "stop"}


class _EmptyDraft:
    async def list_draft_versions(self, pid, ch):
        return []

    async def get_draft(self, pid, ch, v):
        return None


def test_run_writing_agent_writes_and_streams(tmp_path):
    """agent 调 write_content → working_text 变 → 推 stream_start/stream_end + 返回 agentic_write。"""
    orch = _orch(tmp_path)
    events = []

    async def _cb(payload):
        events.append(payload.get("type"))

    async def _no_proposals(pid, text):
        return []

    orch.writing_service.gateway = _AgentGateway()
    orch.writing_service.draft_storage = _EmptyDraft()
    orch.writing_service.progress_callback = _cb
    orch.writing_service.detect_proposals = _no_proposals
    r = asyncio.run(orch.writing_service.run("p", "V1C001", "写第一章"))
    assert r["success"] and r["action"] == "agentic_write" and r["changed"] is True
    assert "stream_start" in events and "stream_end" in events  # WS 流式 diff 已推送


def test_run_writing_agent_fallback_when_no_tool_calls(tmp_path):
    """provider 未调工具（不支持）→ fallback，供 run_chat_turn 回退预判路由。"""
    orch = _orch(tmp_path)
    orch.writing_service.gateway = _NoToolGateway()
    orch.writing_service.draft_storage = _EmptyDraft()
    r = asyncio.run(orch.writing_service.run("p", "V1C001", "写第一章"))
    assert r.get("fallback") is True
