# -*- coding: utf-8 -*-
"""H2 provider-owned tool-result folding regression tests."""

import asyncio
import copy

from app.agents.agentic import run_agentic_chat
from app.context_engine.token_accounting import fold_payload_to_budget, tool_result_fold_placeholder
from app.context_engine.tool_artifact import ToolArtifactStore


def _artifact_refs(monkeypatch, tmp_path, *outputs):
    root = tmp_path / "tool_artifacts"
    monkeypatch.setattr(ToolArtifactStore, "default_root", staticmethod(lambda: root))
    store = ToolArtifactStore()
    return [
        store.persist(output, turn_id="turn", tool_call_id=str(index), tool_name="q", status="succeeded").artifact_ref
        for index, output in enumerate(outputs, start=1)
    ]


def test_payload_accounting_folds_old_recoverable_openai_result(monkeypatch, tmp_path):
    refs = _artifact_refs(monkeypatch, tmp_path, "A" * 5000, "B" * 5000)
    messages = [
        {
            "role": "tool",
            "tool_call_id": "1",
            "content": "A" * 5000,
            "_recoverable": True,
            "_source_ref": refs[0],
        },
        {
            "role": "tool",
            "tool_call_id": "2",
            "content": "B" * 5000,
            "_recoverable": True,
            "_source_ref": refs[1],
        },
    ]
    fitted, accounting, degradation = fold_payload_to_budget(
        messages,
        tools=None,
        provider="openai",
        model="gpt-4o",
        budget=1600,
    )
    assert fitted[0]["content"] == tool_result_fold_placeholder(refs[0])
    assert fitted[1]["content"] == "B" * 5000
    assert all(not any(str(key).startswith("_") for key in message) for message in fitted)
    assert degradation[0]["source_ref"] == refs[0]
    assert degradation[0]["recoverable"] is True
    assert accounting.upper_bound_tokens <= 1600


def test_payload_accounting_folds_recoverable_anthropic_block(monkeypatch, tmp_path):
    refs = _artifact_refs(monkeypatch, tmp_path, "A" * 5000, "B" * 5000)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "1",
                    "content": "A" * 5000,
                    "_recoverable": True,
                    "_source_ref": refs[0],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "2",
                    "content": "B" * 5000,
                    "_recoverable": True,
                    "_source_ref": refs[1],
                }
            ],
        },
    ]
    fitted, _, degradation = fold_payload_to_budget(
        messages,
        tools=None,
        provider="anthropic",
        model="claude-sonnet-4-5",
        budget=1000,
    )
    assert fitted[0]["content"][0]["content"] == tool_result_fold_placeholder(refs[0])
    assert fitted[1]["content"][0]["content"] == "B" * 5000
    assert degradation[0]["source_ref"] == refs[0]


def test_payload_accounting_never_folds_nonrecoverable_prose():
    messages = [
        {"role": "user", "content": "正文" * 3000},
        {"role": "tool", "content": "不可恢复结果" * 1000, "_recoverable": False},
    ]
    fitted, accounting, degradation = fold_payload_to_budget(
        messages,
        tools=None,
        provider="deepseek",
        model="deepseek-chat",
        budget=100,
    )
    assert fitted[0]["content"] == "正文" * 3000
    assert fitted[1]["content"] == "不可恢复结果" * 1000
    assert degradation == []
    assert accounting.upper_bound_tokens > 100


class _FakeToolset:
    @staticmethod
    def schemas():
        return [{"type": "function", "function": {"name": "q", "description": "x", "parameters": {}}}]

    @staticmethod
    def is_result_recoverable(_name):
        return True

    async def execute(self, _name, _arguments):
        return "X" * 5000


class _FakeGateway:
    def __init__(self, scripted):
        self.scripted = scripted
        self.calls = []

    async def chat(self, messages, **_kwargs):
        self.calls.append(copy.deepcopy(messages))
        index = min(len(self.calls) - 1, len(self.scripted) - 1)
        return self.scripted[index]


def test_agentic_loop_does_not_own_runtime_folding():
    tool_response = {
        "provider": "openai",
        "content": "",
        "tool_calls": [{"id": "1", "name": "q", "arguments": "{}"}],
    }
    final_response = {"provider": "openai", "content": "done", "tool_calls": None}
    gateway = _FakeGateway([tool_response, tool_response, final_response])

    result = asyncio.run(
        run_agentic_chat(
            gateway,
            "openai",
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            _FakeToolset(),
            tool_result_budget=1,
        )
    )

    assert result["content"] == "done"
    tool_contents = [message["content"] for message in gateway.calls[-1] if message.get("role") == "tool"]
    assert tool_contents == ["X" * 5000, "X" * 5000]
