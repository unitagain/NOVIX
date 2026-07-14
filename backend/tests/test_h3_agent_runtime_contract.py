"""H3 tool artifact, deadline, cancellation, and terminal-result contracts."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from app.agents.agent_task import AgentTask, AgentTaskRunner, AgentTaskStatus
from app.agents.agentic import run_agentic_chat
from app.agents.runtime_result import AgentRunStatus
from app.context_engine.token_accounting import fold_payload_to_budget
from app.context_engine.tool_artifact import ToolArtifactStore, ToolExecutionStatus
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope


class _ScriptedGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(message) for message in messages], "kwargs": dict(kwargs)})
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, BaseException):
            raise response
        return dict(response)


class _Toolset:
    def __init__(self, execute=None):
        self._execute = execute
        self.calls = 0

    @staticmethod
    def schemas():
        return [{"type": "function", "function": {"name": "q", "description": "query", "parameters": {}}}]

    @staticmethod
    def is_result_recoverable(_name):
        return True

    async def execute(self, name, arguments):
        self.calls += 1
        if self._execute is not None:
            return await self._execute(name, arguments)
        return "full-result"


def _tool_response(provider: str = "openai"):
    return {
        "provider": provider,
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "q", "arguments": "{}"}],
        "finish_reason": "tool_calls",
    }


def _final_response(provider: str = "openai"):
    return {"provider": provider, "content": "done", "tool_calls": None, "finish_reason": "stop"}


def _patch_artifact_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "tool_artifacts"
    monkeypatch.setattr(ToolArtifactStore, "default_root", staticmethod(lambda: root))
    return root


def test_tool_artifact_is_hash_bound_and_retention_controlled(tmp_path: Path):
    store = ToolArtifactStore(tmp_path, retention_seconds=60)
    artifact = store.persist(
        "完整结果",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="q",
        status="succeeded",
        now=100,
    )

    payload = store.read(artifact.artifact_ref, now=120)
    assert payload["output"] == "完整结果"
    assert payload["output_hash"] == hashlib.sha256("完整结果".encode("utf-8")).hexdigest()
    assert store.cleanup(now=161)["removed"] == 1
    assert not store.exists(artifact.artifact_ref, now=161)


def test_tool_artifact_capacity_removes_oldest_first(tmp_path: Path):
    store = ToolArtifactStore(tmp_path, max_artifact_bytes=1024, max_total_bytes=1600)
    first = store.persist("A" * 900, turn_id="t", tool_call_id="1", tool_name="q", status="succeeded", now=100)
    second = store.persist("B" * 900, turn_id="t", tool_call_id="2", tool_name="q", status="succeeded", now=101)

    assert not store.exists(first.artifact_ref, now=102)
    assert store.exists(second.artifact_ref, now=102)


def test_broken_artifact_ref_is_never_folded(monkeypatch, tmp_path: Path):
    _patch_artifact_root(monkeypatch, tmp_path)
    messages = [
        {
            "role": "tool",
            "tool_call_id": "1",
            "content": "X" * 5000,
            "_recoverable": True,
            "_source_ref": "tool-artifact://00000000000000000000000000000000",
        },
        {"role": "user", "content": "tail"},
    ]

    fitted, accounting, degradations = fold_payload_to_budget(
        messages,
        tools=None,
        provider="openai",
        model="gpt-4o",
        budget=10,
    )

    assert fitted[0]["content"] == "X" * 5000
    assert accounting.upper_bound_tokens > 10
    assert degradations == []


def test_openai_and_anthropic_replay_share_artifact_semantics(monkeypatch, tmp_path: Path):
    root = _patch_artifact_root(monkeypatch, tmp_path)

    async def run(provider: str):
        gateway = _ScriptedGateway([_tool_response(provider), _final_response(provider)])
        scope = new_turn_scope(project_id="project")
        with bind_turn_scope(scope):
            result = await run_agentic_chat(gateway, provider, [{"role": "user", "content": "q"}], _Toolset())
        return gateway, result

    openai_gateway, openai_result = asyncio.run(run("openai"))
    anthropic_gateway, anthropic_result = asyncio.run(run("anthropic"))
    openai_message = next(
        message for message in openai_gateway.calls[1]["messages"] if message.get("role") == "tool"
    )
    anthropic_block = next(
        block
        for message in anthropic_gateway.calls[1]["messages"]
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    )

    for key in ("content", "_tool_status", "_recoverable", "_output_hash"):
        assert openai_message[key] == anthropic_block[key]
    assert openai_message["_source_ref"] != anthropic_block["_source_ref"]
    assert ToolArtifactStore(root).exists(openai_message["_source_ref"])
    assert ToolArtifactStore(root).exists(anthropic_block["_source_ref"])
    assert openai_result.status == anthropic_result.status == AgentRunStatus.COMPLETED.value


def test_max_iterations_returns_incomplete_without_extra_provider_request(monkeypatch, tmp_path: Path):
    _patch_artifact_root(monkeypatch, tmp_path)
    gateway = _ScriptedGateway([_tool_response()])
    toolset = _Toolset()
    scope = new_turn_scope(project_id="project")
    with bind_turn_scope(scope):
        result = asyncio.run(
            run_agentic_chat(gateway, "openai", [{"role": "user", "content": "q"}], toolset, max_iterations=2)
        )

    assert result.status == AgentRunStatus.INCOMPLETE.value
    assert result.finish_reason == "max_iterations"
    assert len(gateway.calls) == 2
    assert toolset.calls == 2


def test_provider_failure_maps_to_safe_agent_run_result():
    secret = "provider secret payload"
    gateway = _ScriptedGateway([RuntimeError(secret)])
    result = asyncio.run(run_agentic_chat(gateway, "openai", [{"role": "user", "content": "q"}], _Toolset()))

    assert result.status == AgentRunStatus.FAILED.value
    assert result.finish_reason == "provider_failure"
    assert secret not in str(result.to_dict())


def test_tool_failure_is_typed_and_model_can_finish(monkeypatch, tmp_path: Path):
    _patch_artifact_root(monkeypatch, tmp_path)

    async def fail(_name, _arguments):
        raise RuntimeError("private tool body")

    gateway = _ScriptedGateway([_tool_response(), _final_response()])
    scope = new_turn_scope(project_id="project")
    with bind_turn_scope(scope):
        result = asyncio.run(run_agentic_chat(gateway, "openai", [{"role": "user", "content": "q"}], _Toolset(fail)))

    assert result.status == AgentRunStatus.COMPLETED.value
    assert result.tool_results[0].status == ToolExecutionStatus.FAILED.value
    assert result.tool_results[0].error
    assert "private tool body" not in str(result.to_dict())


def test_cancellation_stops_tool_publication_and_future_requests(monkeypatch, tmp_path: Path):
    root = _patch_artifact_root(monkeypatch, tmp_path)

    async def scenario():
        started = asyncio.Event()

        async def slow(_name, _arguments):
            started.set()
            await asyncio.sleep(10)
            return "late-result"

        gateway = _ScriptedGateway([_tool_response(), _final_response()])
        scope = new_turn_scope(project_id="project")
        with bind_turn_scope(scope):
            task = asyncio.create_task(
                run_agentic_chat(gateway, "openai", [{"role": "user", "content": "q"}], _Toolset(slow))
            )
            await started.wait()
            scope.cancel()
            result = await task
        return gateway, result

    gateway, result = asyncio.run(scenario())
    assert result.status == AgentRunStatus.CANCELLED.value
    assert len(gateway.calls) == 1
    assert list(root.glob("*.json")) == []


def test_tool_deadline_cannot_publish_late_result(monkeypatch, tmp_path: Path):
    root = _patch_artifact_root(monkeypatch, tmp_path)

    async def late(_name, _arguments):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return "late-result-after-timeout"
        return "unreachable"

    gateway = _ScriptedGateway([_tool_response(), _final_response()])
    scope = new_turn_scope(project_id="project", timeout_seconds=0.03)
    with bind_turn_scope(scope):
        result = asyncio.run(run_agentic_chat(gateway, "openai", [{"role": "user", "content": "q"}], _Toolset(late)))

    assert result.status == AgentRunStatus.FAILED.value
    assert result.tool_results[0].status == ToolExecutionStatus.TIMED_OUT.value
    assert len(gateway.calls) == 1
    assert list(root.glob("*.json")) == []


def test_event_callback_failure_is_degradation_only():
    async def broken_callback(_event):
        raise RuntimeError("ui callback unavailable")

    final = _final_response()
    final["thinking"] = "internal progress"
    gateway = _ScriptedGateway([final])
    result = asyncio.run(
        run_agentic_chat(
            gateway,
            "openai",
            [{"role": "user", "content": "q"}],
            _Toolset(),
            on_event=broken_callback,
        )
    )

    assert result.status == AgentRunStatus.COMPLETED.value
    assert result.content == "done"
    assert result.degradations[0]["type"] == "event_callback_failure"


def test_worker_terminal_state_maps_to_agent_run_result():
    task = AgentTask(id="worker-1", kind="summarize", input={"text": "abc"})
    completed = asyncio.run(AgentTaskRunner().run(task))
    mapped = completed.to_agent_run_result()

    assert completed.status == AgentTaskStatus.COMPLETED.value
    assert mapped.status == AgentRunStatus.COMPLETED.value
    assert mapped.response["worker_output"]["summary"] == "abc"


def test_cancelled_worker_does_not_publish_handler_output():
    called = False

    async def handler(_task):
        nonlocal called
        called = True
        return {"value": "unexpected"}

    scope = new_turn_scope(project_id="project")
    scope.cancel()
    task = AgentTask(id="worker-cancel", kind="custom")
    with bind_turn_scope(scope):
        result = asyncio.run(AgentTaskRunner({"custom": handler}).run(task))

    assert result.status == AgentTaskStatus.CANCELLED.value
    assert result.to_agent_run_result().status == AgentRunStatus.CANCELLED.value
    assert called is False
