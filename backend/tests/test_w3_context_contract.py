"""W3 frozen context, source revision and final payload accounting contracts."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.agents.tools import WriterToolset
from app.context_engine.context_plan import ContextPlanV2, build_context_plan_v2
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.reliability import GatewayReliabilityController, PersistentIdempotencyRegistry
from app.orchestrator.post_turn_service import PostTurnService
from app.security.egress_ledger import EgressLedger
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.drafts import DraftStorage
from app.storage.session_history import SessionHistoryStorage


class RecordingProvider:
    model = "deepseek-chat"
    max_tokens = 1024

    def __init__(self):
        self.calls = 0
        self.messages = []

    def get_provider_name(self):
        return "deepseek"

    async def chat(self, messages, **_options):
        self.calls += 1
        self.messages = messages
        return {
            "content": "ok",
            "model": self.model,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def _gateway(provider: RecordingProvider, tmp_path: Path) -> LLMGateway:
    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": provider}
    gateway.max_retries = 0
    gateway.retry_delays = []
    gateway.max_retry_delay = 0
    gateway.request_timeout = 1.0
    gateway.reliability = GatewayReliabilityController()
    gateway.idempotency_registry = PersistentIdempotencyRegistry(tmp_path / "idempotency")
    gateway.total_tokens = 0
    gateway.total_requests = 0
    return gateway


def _manual_plan(turn_id: str, *, input_tokens: int = 1000, tools=()) -> ContextPlanV2:
    return ContextPlanV2(
        plan_id=f"plan_{turn_id}",
        turn_id=turn_id,
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        context_epoch="0",
        provider={"profile_id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
        budget={"context_limit_tokens": input_tokens + 100, "input_tokens": input_tokens, "output_reserve_tokens": 100},
        tool_loadout=[{"name": name} for name in tools],
        fingerprints={"plan": "frozen"},
    )


def test_context_plan_is_deep_frozen_and_requests_live_in_turn_trace(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", turn_id="turn-frozen")
    plan = _manual_plan(scope.turn_id)
    scope.activate_plan(plan)

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat([{"role": "user", "content": "hello"}], provider="profile", max_tokens=50)

    asyncio.run(scenario())
    with pytest.raises(TypeError):
        plan.budget["input_tokens"] = 1
    with pytest.raises(TypeError):
        plan.fingerprints["request"] = "changed"
    assert "requests" not in plan.to_dict()
    assert len(scope.turn_trace.model_requests) == 1
    assert scope.turn_trace.model_requests[0]["final_payload_fingerprint"]
    assert scope.turn_trace.model_requests[0]["actual_input_tokens"] == 1
    assert scope.turn_trace.model_requests[0]["token_reconciliation"] == "provider_reported"
    assert scope.turn_trace.model_requests[0]["actual_provider"] == "deepseek"
    assert scope.turn_trace.model_requests[0]["actual_model"] == "deepseek-chat"


def test_content_hash_detects_same_size_and_mtime_source_drift(tmp_path: Path):
    project = tmp_path / "p"
    canon = project / "canon" / "facts.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("alpha", encoding="utf-8")
    original = canon.stat()
    plan = build_context_plan_v2(
        turn_id="turn-source",
        project_id="p",
        chapter_id="",
        intent="write",
        route_path="agentic_writer",
        project_root=project,
    )
    canon.write_text("bravo", encoding="utf-8")
    os.utime(canon, ns=(original.st_atime_ns, original.st_mtime_ns))
    verification = plan.verify_sources()
    assert verification["valid"] is False
    assert verification["failures"][0]["reason"] == "content_sha256_mismatch"


def test_gateway_blocks_source_drift_before_provider_io(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    project = tmp_path / "p"
    source = project / "canon" / "facts.md"
    source.parent.mkdir(parents=True)
    source.write_text("before", encoding="utf-8")
    scope = new_turn_scope(project_id="p", turn_id="turn-drift")
    scope.activate_plan(
        build_context_plan_v2(
            turn_id=scope.turn_id,
            project_id="p",
            chapter_id="",
            intent="write",
            route_path="agentic_writer",
            project_root=project,
            provider_profile={"id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
        )
    )
    source.write_text("after!", encoding="utf-8")
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat([{"role": "user", "content": "x"}], provider="profile", max_tokens=50)

    with pytest.raises(RuntimeError, match="context_source_revision_unavailable"):
        asyncio.run(scenario())
    assert provider.calls == 0


def test_jit_tool_rejects_source_change_during_read(tmp_path: Path):
    project = tmp_path / "p"
    source = project / "canon" / "facts.md"
    source.parent.mkdir(parents=True)
    source.write_text("stable", encoding="utf-8")
    scope = new_turn_scope(project_id="p", turn_id="turn-tool-drift")
    scope.activate_plan(
        build_context_plan_v2(
            turn_id=scope.turn_id,
            project_id="p",
            chapter_id="",
            intent="write",
            route_path="agentic_writer",
            project_root=project,
        )
    )

    class Adapter:
        async def get_character_card(self, _project_id, _name):
            source.write_text("drift!", encoding="utf-8")
            return {"name": "角色", "description": "设定"}

        async def get_world_card(self, _project_id, _name):
            return None

    toolset = WriterToolset("p", Adapter(), object())

    async def scenario():
        with bind_turn_scope(scope):
            await toolset.execute("lookup_card", {"name": "角色"})

    with pytest.raises(RuntimeError, match="context_source_revision_unavailable"):
        asyncio.run(scenario())


def test_final_payload_accounting_folds_only_old_recoverable_tool_results(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", turn_id="turn-fold")
    scope.activate_plan(_manual_plan(scope.turn_id, input_tokens=650))
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "instruction"},
        {"role": "tool", "tool_call_id": "old", "content": "x" * 4000},
        {"role": "tool", "tool_call_id": "new", "content": "latest evidence"},
    ]

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat(messages, provider="profile", max_tokens=50)

    asyncio.run(scenario())
    request = scope.turn_trace.model_requests[0]
    assert request["input_tokens"] > 0
    assert request["input_upper_bound_tokens"] <= 650
    assert request["tokenizer"]
    assert request["token_count_exact"] is False
    assert request["degradation"][0]["type"] == "tool_result_folding"
    assert "原始结果可由 trace/source ref 恢复" in provider.messages[2]["content"]
    assert provider.messages[3]["content"] == "latest evidence"


def test_final_payload_rejects_oversized_nonrecoverable_prose(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", turn_id="turn-overflow")
    scope.activate_plan(_manual_plan(scope.turn_id, input_tokens=100))

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat([{"role": "user", "content": "正文" * 2000}], provider="profile", max_tokens=50)

    with pytest.raises(ValueError, match="context_budget_exceeded"):
        asyncio.run(scenario())
    assert provider.calls == 0


def test_draft_snapshot_binds_control_revision_and_content_hash(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("p", "V1C001", "draft text"))
    plan = build_context_plan_v2(
        turn_id="turn-draft",
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path / "p",
    )
    row = next(item for item in plan.to_dict()["snapshot"] if item["path"] == "drafts/V1C001/final.md")
    assert row["revision_kind"] == "control_store"
    assert int(row["revision"]) >= 1
    assert len(row["content_sha256"]) == 64


def test_draft_snapshot_rejects_aba_even_when_content_hash_matches(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("p", "V1C001", "original"))
    plan = build_context_plan_v2(
        turn_id="turn-aba",
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path / "p",
    )
    asyncio.run(storage.save_current_draft("p", "V1C001", "changed"))
    asyncio.run(storage.save_current_draft("p", "V1C001", "original"))
    verification = plan.verify_sources()
    assert verification["valid"] is False
    assert any(row["reason"] == "control_revision_mismatch" for row in verification["failures"])


def test_compact_generated_memory_binds_artifact_epoch_and_source_hash(tmp_path: Path):
    session = SessionHistoryStorage(str(tmp_path))
    memory = CreativeMemoryStorage(str(tmp_path))
    for index in range(8):
        asyncio.run(session.append("p", {"role": "user", "content": f"message-{index}"}))

    class Archivist:
        async def extract_creative_memory(self, **_kwargs):
            return [{"slug": "tone", "description": "保持克制", "body": "用户偏好", "type": "preference"}]

    async def summarize(_conversation):
        return {"recent_summary": "讨论完成", "constraints": ["保持克制"]}

    service = PostTurnService(
        session_history=session,
        archivist=Archivist(),
        creative_memory_storage=memory,
        summarize_conversation=summarize,
    )
    result = asyncio.run(service.compact_conversation("p", keep_recent=2, trigger_at=4))
    record = asyncio.run(memory.read_memory("p", "tone"))
    assert result["compacted"] is True
    assert record["source_refs"] == [f"compact:{result['compact_artifact_id']}"]
    assert record["version_refs"][0]["context_epoch"] == result["context_epoch"]
    assert record["version_refs"][0]["revision"] == result["source_snapshot_sha256"]
