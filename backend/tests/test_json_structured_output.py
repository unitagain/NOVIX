# -*- coding: utf-8 -*-
"""Phase 9 · 结构化输出（call_llm_json）回归测试。

验证：① response_format 透传（能力门控）② 成功/失败记录到 json_metrics ③ fallback 解析仍健壮
④ 指标按 config_agent 归类。无网络 / 无 key：用 FakeGateway。
"""

import asyncio

from app.agents.base import BaseAgent
from app.utils.json_metrics import json_metrics_snapshot, reset_json_metrics


class _StubAgent(BaseAgent):
    def get_agent_name(self) -> str:
        return "stub_agent"

    async def execute(self, *args, **kwargs):
        return {}


class _FakeGateway:
    """最小网关：返回固定 content，并记录最后一次 chat 的 kwargs（验证 response_format 透传）。"""

    def __init__(self, content):
        self.content = content
        self.last_kwargs = None

    def get_provider_for_agent(self, name):
        return "fake"

    def get_temperature_for_agent(self, name):
        return 0.5

    async def chat(self, **kwargs):
        self.last_kwargs = kwargs
        return {"content": self.content, "usage": {}, "model": "fake", "provider": "fake"}


def _make_agent(content):
    gw = _FakeGateway(content)
    return _StubAgent(gw, None, None, None), gw


def test_call_llm_json_success_records_metric_and_passes_response_format():
    reset_json_metrics()
    agent, gw = _make_agent('{"a": 1}')
    data, err, raw = asyncio.run(agent.call_llm_json([{"role": "user", "content": "x"}], expected_type=dict))
    assert err == ""
    assert data == {"a": 1}
    # 能力门控：response_format 已透传给网关（OpenAI 族强制 JSON；Anthropic 内部忽略）
    assert gw.last_kwargs.get("response_format") == {"type": "json_object"}
    snap = json_metrics_snapshot()
    assert snap["stub_agent"]["success"] == 1
    assert snap["stub_agent"]["success_rate"] == 1.0


def test_call_llm_json_failure_records_and_returns_error():
    reset_json_metrics()
    agent, _ = _make_agent("not json at all")
    data, err, _raw = asyncio.run(agent.call_llm_json([{"role": "user", "content": "x"}], expected_type=dict))
    assert err != ""
    assert data is None
    snap = json_metrics_snapshot()
    assert snap["stub_agent"]["fail"] == 1
    assert snap["stub_agent"]["success_rate"] == 0.0


def test_call_llm_json_fallback_extracts_json_from_noisy_text():
    """fallback：JSON 埋在文字里（response_format 不生效的 provider 的兜底）仍能解析成功。"""
    reset_json_metrics()
    agent, _ = _make_agent('好的，结果如下：\n{"ok": true}\n以上。')
    data, err, _raw = asyncio.run(agent.call_llm_json([{"role": "user", "content": "x"}], expected_type=dict))
    assert err == ""
    assert data == {"ok": True}
    assert json_metrics_snapshot()["stub_agent"]["success"] == 1


def test_call_llm_json_metric_keyed_by_config_agent():
    """指标按 config_agent 归类（editor 复用 base 时记到 editor 名下）。"""
    reset_json_metrics()
    agent, _ = _make_agent('{"x": 1}')
    asyncio.run(agent.call_llm_json([{"role": "user", "content": "x"}], expected_type=dict, config_agent="editor"))
    snap = json_metrics_snapshot()
    assert "editor" in snap and snap["editor"]["success"] == 1
    assert "stub_agent" not in snap  # 未用默认名


def test_call_llm_json_type_mismatch_is_failure():
    """期望 dict 但模型给了 list：视为失败并计入指标。"""
    reset_json_metrics()
    agent, _ = _make_agent("[1, 2, 3]")
    data, err, _raw = asyncio.run(agent.call_llm_json([{"role": "user", "content": "x"}], expected_type=dict))
    assert err != "" and data is None
    assert json_metrics_snapshot()["stub_agent"]["fail"] == 1
