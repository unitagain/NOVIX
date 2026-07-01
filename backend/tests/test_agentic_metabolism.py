# -*- coding: utf-8 -*-
"""Phase 8 · agentic 循环代谢（工具结果折叠）回归测试。

验证多轮 agentic 循环里早期工具结果被折叠为占位符（避免上下文膨胀），最近结果保留。
覆盖 OpenAI 兼容（role="tool"）与 Anthropic（user 内 tool_result 块）两种回放格式。
无网络 / 无 key：用脚本化 FakeGateway + FakeToolset。
"""

import asyncio
import copy

from app.agents.agentic import run_agentic_chat, _fold_old_tool_results, _FOLD_PLACEHOLDER


def test_fold_openai_keeps_recent_folds_old():
    """OpenAI 格式：预算内的最近结果保留，更早的折叠为占位符。"""
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "A" * 5000},
        {"role": "tool", "tool_call_id": "2", "content": "B" * 5000},
    ]
    folded = _fold_old_tool_results(msgs, budget=6000)
    assert folded == 1
    assert msgs[1]["content"] == "B" * 5000  # 最近的保留
    assert msgs[0]["content"] == _FOLD_PLACEHOLDER  # 更早的折叠


def test_fold_anthropic_tool_result_blocks():
    """Anthropic 格式：user 消息内 tool_result 块同样被折叠。"""
    msgs = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "A" * 5000}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": "B" * 5000}]},
    ]
    folded = _fold_old_tool_results(msgs, budget=6000)
    assert folded == 1
    assert msgs[1]["content"][0]["content"] == "B" * 5000
    assert msgs[0]["content"][0]["content"] == _FOLD_PLACEHOLDER


def test_fold_disabled_when_budget_zero():
    """budget<=0：关闭折叠，内容原样保留（能力降级，零行为变化）。"""
    msgs = [{"role": "tool", "content": "A" * 5000}, {"role": "tool", "content": "B" * 5000}]
    assert _fold_old_tool_results(msgs, budget=0) == 0
    assert msgs[0]["content"] == "A" * 5000


def test_fold_idempotent_does_not_refold_placeholder():
    """已折叠的占位符不会被重复计数（幂等）。"""
    msgs = [
        {"role": "tool", "content": _FOLD_PLACEHOLDER},
        {"role": "tool", "content": "B" * 5000},
    ]
    assert _fold_old_tool_results(msgs, budget=6000) == 0


class _FakeToolset:
    @staticmethod
    def schemas():
        return [{"type": "function", "function": {"name": "q", "description": "x", "parameters": {"type": "object"}}}]

    async def execute(self, name, arguments):
        return "X" * 5000  # 长结果，触发跨轮折叠


class _FakeGateway:
    """脚本化网关：按调用次序返回预设响应，并记录每次收到的 msgs 快照。"""

    def __init__(self, scripted):
        self.scripted = scripted
        self.calls = []

    async def chat(self, msgs, **kwargs):
        self.calls.append(copy.deepcopy(msgs))
        idx = min(len(self.calls) - 1, len(self.scripted) - 1)
        return self.scripted[idx]


def test_run_agentic_chat_folds_across_rounds():
    """端到端：多轮工具调用后，最终一次 chat 收到的 msgs 里早期工具结果已折叠。"""
    resp_tool = {"provider": "openai", "content": "", "tool_calls": [{"id": "1", "name": "q", "arguments": "{}"}]}
    resp_final = {"provider": "openai", "content": "done", "tool_calls": None}
    gw = _FakeGateway([resp_tool, resp_tool, resp_final])

    result = asyncio.run(
        run_agentic_chat(
            gw,
            "openai",
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            _FakeToolset(),
            tool_result_budget=6000,
        )
    )
    assert result["content"] == "done"
    # 第 3 次 chat（resp_final）收到的 msgs：累计两条 5000 字结果 > 6000，最早一条应被折叠
    last_msgs = gw.calls[-1]
    tool_contents = [m["content"] for m in last_msgs if m.get("role") == "tool"]
    assert tool_contents.count(_FOLD_PLACEHOLDER) == 1
    assert ("X" * 5000) in tool_contents  # 最近一条仍保留
