"""W3 frozen context, source revision and final payload accounting contracts."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.agents.tools import WriterToolset
from app.context_engine.context_plan import ContextPlanV2, build_context_plan_v2
from app.context_engine.budget_manager import ContextBudgetManager
from app.context_engine.source_snapshot import SourceDescriptor, SourceRegistry
from app.context_engine.token_accounting import (
    count_text_tokens,
    record_token_estimator_observation,
    token_estimator_calibration,
)
from app.context_engine.tool_artifact import ToolArtifactStore
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.reliability import GatewayReliabilityController, PersistentIdempotencyRegistry
from app.orchestrator.post_turn_service import PostTurnService
from app.orchestrator.context_assembly_service import ContextAssemblyService
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


def _manual_plan(turn_id: str, *, input_tokens: int = 1000, context_limit: int | None = None, tools=()) -> ContextPlanV2:
    return ContextPlanV2(
        plan_id=f"plan_{turn_id}",
        turn_id=turn_id,
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        context_epoch="0",
        provider={"profile_id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
        budget={
            "context_limit_tokens": context_limit if context_limit is not None else input_tokens + 100,
            "input_tokens": input_tokens,
            "output_reserve_tokens": 100,
        },
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
    assert scope.turn_trace.model_requests[0]["estimator_calibration"]["samples"] >= 1
    assert scope.turn_trace.model_requests[0]["estimator_calibration"]["p95_relative_error"] is not None


def test_content_hash_detects_same_size_and_mtime_source_drift(tmp_path: Path):
    project = tmp_path / "p"
    canon = project / "canon" / "facts.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("alpha", encoding="utf-8")
    original = canon.stat()
    registry = SourceRegistry(project_root=project)
    registry.register_file(
        canon,
        source_id="canon.actual",
        asset_type="canon",
        selection_reason="test_actual_read",
    )
    canon.write_text("bravo", encoding="utf-8")
    os.utime(canon, ns=(original.st_atime_ns, original.st_mtime_ns))
    verification = registry.verify_mutable_sources()
    assert verification["valid"] is False
    assert verification["failures"][0]["reason"] == "content_sha256_mismatch"


@pytest.mark.parametrize(
    ("asset_type", "relative"),
    [
        ("cards", "cards/characters/hero.yaml"),
        ("summaries", "summaries/V1C001_summary.yaml"),
        ("scene_brief", "drafts/V1C001/scene_brief.yaml"),
        ("volume_order", "volumes/V1.yaml"),
        ("project_config", "project.yaml"),
    ],
)
def test_each_mutable_context_source_class_fails_after_drift(tmp_path: Path, asset_type: str, relative: str):
    project = tmp_path / "p"
    source = project / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("before", encoding="utf-8")
    registry = SourceRegistry(project_root=project)
    registry.register_file(
        source,
        source_id=f"actual.{asset_type}",
        asset_type=asset_type,
        selection_reason="actual_context_read",
    )

    source.write_text("after", encoding="utf-8")

    verification = registry.verify_mutable_sources()
    assert verification["valid"] is False
    assert verification["failures"] == [{"path": relative, "reason": "content_sha256_mismatch"}]


def test_source_descriptor_fingerprint_is_stable_for_equal_content():
    first = SourceDescriptor.from_content(
        source_id="prompt.writer",
        asset_type="prompt",
        content={"system": "stable", "rules": [1, 2]},
        selection_reason="writer_prompt",
    )
    second = SourceDescriptor.from_content(
        source_id="prompt.writer",
        asset_type="prompt",
        content={"rules": [1, 2], "system": "stable"},
        selection_reason="writer_prompt",
    )
    assert first.content_sha256 == second.content_sha256
    assert first.fingerprint == second.fingerprint


def test_provider_usage_calibrates_estimator_p95_without_changing_policy():
    provider = "calibration-test-provider"
    model = "calibration-test-model"
    for estimated, actual in [(100, 100), (110, 100), (120, 100), (140, 100)]:
        record_token_estimator_observation(
            provider=provider,
            model=model,
            estimated_tokens=estimated,
            actual_tokens=actual,
        )

    calibration = token_estimator_calibration(provider, model)
    accounting = count_text_tokens("校准样本", provider=provider, model=model)
    assert calibration["samples"] == 4
    assert calibration["p95_relative_error"] == pytest.approx(0.4)
    assert calibration["policy_adjusted"] is False
    assert accounting.calibration_samples == 4
    assert accounting.observed_p95_relative_error == pytest.approx(0.4)
    assert accounting.calibration_policy_adjusted is False


def test_provider_calibration_evidence_does_not_change_budget_strategy():
    openai = ContextBudgetManager(
        model_name="gpt-4o",
        max_output_tokens=1000,
        max_context_tokens=10000,
        provider="openai",
    )
    deepseek = ContextBudgetManager(
        model_name="gpt-4o",
        max_output_tokens=1000,
        max_context_tokens=10000,
        provider="deepseek",
    )
    assert openai.total_budget == deepseek.total_budget
    usage = deepseek.track_usage("canon", "设定" * 100)
    expected = count_text_tokens("设定" * 100, provider="deepseek", model="gpt-4o")
    assert usage.used == expected.upper_bound_tokens
    assert deepseek.get_usage_summary()["estimator_calibration"]["policy_adjusted"] is False


def test_source_registry_reports_planned_actual_unexpected_and_not_selected():
    registry = SourceRegistry(
        planned=[
            SourceDescriptor.planned(
                source_id="planned.canon", asset_type="canon", selection_reason="research"
            ),
            SourceDescriptor.planned(
                source_id="planned.summaries", asset_type="summaries", selection_reason="continuity"
            ),
        ]
    )
    registry.register_content(
        source_id="actual.canon",
        asset_type="canon",
        content="fact",
        selection_reason="retrieval",
    )
    registry.register_content(
        source_id="actual.external",
        asset_type="external_web",
        content="unplanned",
        selection_reason="unexpected",
        trust_label="untrusted",
    )

    report = registry.reconcile()
    assert report["summary"]["planned_types"] == ["canon", "summaries"]
    assert report["summary"]["actual_types"] == ["canon", "external_web"]
    assert report["summary"]["unexpected_types"] == ["external_web"]
    assert report["summary"]["not_selected_types"] == ["summaries"]


def test_payload_closure_detects_mutated_message_and_tool_schema():
    registry = SourceRegistry()
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "write"}]
    tools = [{"type": "function", "function": {"name": "lookup_card", "parameters": {}}}]
    registry.register_payload(
        messages,
        tools,
        source_prefix="test.payload",
        selection_reason="test_assembly",
    )

    changed_messages = [*messages[:-1], {"role": "user", "content": "write with hidden context"}]
    changed_tools = [{"type": "function", "function": {"name": "read_chapter", "parameters": {}}}]
    assert registry.verify_payload(messages, tools)["valid"] is True
    assert registry.verify_payload(changed_messages, tools)["missing"][0]["kind"] == "message"
    assert registry.verify_payload(messages, changed_tools)["missing"][0]["kind"] == "tool_schema"


def test_context_plan_declares_all_h1_source_classes(tmp_path: Path):
    plan = build_context_plan_v2(
        turn_id="turn-planned-sources",
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path / "p",
        provider_profile={"id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
    )
    asset_types = {row["asset_type"] for row in plan.to_dict()["sources"]}
    assert {
        "user_message",
        "chapter",
        "draft",
        "project_config",
        "clarification_request",
        "prompt",
        "procedural_knowledge",
        "tool_schema",
        "model_assignment",
        "cards",
        "summaries",
        "scene_brief",
        "volume_order",
        "canon",
        "relations",
        "prose",
    } <= asset_types


def test_gateway_rejects_unregistered_context_before_provider_io(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", turn_id="turn-unregistered")
    scope.activate_plan(
        build_context_plan_v2(
            turn_id=scope.turn_id,
            project_id="p",
            chapter_id="",
            intent="write",
            route_path="agentic_writer",
            project_root=tmp_path / "p",
            provider_profile={"id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
        )
    )

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat([{"role": "user", "content": "hidden"}], provider="profile", max_tokens=50)

    with pytest.raises(RuntimeError, match="context_unregistered_payload:message:0"):
        asyncio.run(scenario())
    assert provider.calls == 0


def test_writer_assembly_closes_initial_provider_payload(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", chapter_id="V1C001", turn_id="turn-writer-assembly")
    scope.activate_plan(
        build_context_plan_v2(
            turn_id=scope.turn_id,
            project_id="p",
            chapter_id="V1C001",
            intent="write",
            route_path="agentic_writer",
            project_root=tmp_path / "p",
            provider_profile={"id": "profile", "provider": "deepseek", "model": "deepseek-chat"},
        )
    )

    async def scenario():
        with bind_turn_scope(scope):
            request = ContextAssemblyService(language="zh").assemble_writer_request(
                message="继续写这一章",
                chapter="V1C001",
                current_text="已有正文。",
                has_selection=False,
                target_word_count=1200,
                context_plan=scope.active_plan,
            )
            return await gateway.chat(request.messages, provider="profile", max_tokens=50)

    response = asyncio.run(scenario())
    assert response["content"] == "ok"
    assert provider.calls == 1
    verification = scope.turn_trace.source_verifications[-1]
    assert verification["valid"] is True
    assert verification["payload"]["missing"] == []
    actual_types = scope.source_registry.reconcile()["summary"]["actual_types"]
    assert {"prompt", "user_message", "chapter", "draft", "project_config", "provider_message"} <= set(
        actual_types
    )


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
    messages = [{"role": "user", "content": "x"}]
    scope.register_source_file(
        source,
        source_id="canon.actual",
        asset_type="canon",
        selection_reason="test_actual_read",
    )
    scope.register_provider_payload(
        messages,
        source_prefix="test.gateway",
        selection_reason="test_assembly",
    )
    source.write_text("after!", encoding="utf-8")
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat(messages, provider="profile", max_tokens=50)

    with pytest.raises(RuntimeError, match="context_actual_source_revision_unavailable"):
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
    scope.register_source_file(
        source,
        source_id="canon.actual",
        asset_type="canon",
        selection_reason="jit_tool_source",
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

    with pytest.raises(RuntimeError, match="context_actual_source_revision_unavailable"):
        asyncio.run(scenario())


def test_final_payload_accounting_folds_only_old_recoverable_tool_results(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    artifact_root = tmp_path / "tool_artifacts"
    monkeypatch.setattr(ToolArtifactStore, "default_root", staticmethod(lambda: artifact_root))
    artifact_store = ToolArtifactStore()
    old_ref = artifact_store.persist(
        "x" * 4000,
        turn_id="turn-fold",
        tool_call_id="old",
        tool_name="q",
        status="succeeded",
    ).artifact_ref
    new_ref = artifact_store.persist(
        "latest evidence",
        turn_id="turn-fold",
        tool_call_id="new",
        tool_name="q",
        status="succeeded",
    ).artifact_ref
    scope = new_turn_scope(project_id="p", turn_id="turn-fold")
    scope.activate_plan(_manual_plan(scope.turn_id, input_tokens=650))
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "instruction"},
        {
            "role": "tool",
            "tool_call_id": "old",
            "content": "x" * 4000,
            "_recoverable": True,
            "_source_ref": old_ref,
        },
        {
            "role": "tool",
            "tool_call_id": "new",
            "content": "latest evidence",
            "_recoverable": True,
            "_source_ref": new_ref,
        },
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
    assert old_ref in provider.messages[2]["content"]
    assert provider.messages[3]["content"] == "latest evidence"


def test_final_payload_rejects_oversized_nonrecoverable_prose(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = RecordingProvider()
    gateway = _gateway(provider, tmp_path)
    scope = new_turn_scope(project_id="p", turn_id="turn-overflow")
    # 显式压小硬窗口，使「点估计 tokens」无论 tokenizer 都远超 hard_ceiling 而稳定触发拒绝；
    # 不再依赖 1.35x 悲观上界勉强越过 8000（判据已改为点估计，见 payload_accounting）。
    scope.activate_plan(_manual_plan(scope.turn_id, input_tokens=100, context_limit=500))

    async def scenario():
        with bind_turn_scope(scope):
            await gateway.chat([{"role": "user", "content": "正文" * 2000}], provider="profile", max_tokens=50)

    with pytest.raises(ValueError, match="context_budget_exceeded"):
        asyncio.run(scenario())
    assert provider.calls == 0


def test_actual_draft_source_binds_control_revision_and_content_hash(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("p", "V1C001", "draft text"))
    registry = SourceRegistry(project_root=tmp_path / "p")
    row = registry.register_file(
        tmp_path / "p" / "drafts" / "V1C001" / "final.md",
        source_id="draft.current",
        asset_type="draft",
        selection_reason="edit_baseline",
    )
    assert row.revision_kind == "control_store"
    assert int(row.revision) >= 1
    assert len(row.content_sha256) == 64


def test_actual_draft_source_rejects_aba_even_when_content_hash_matches(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("p", "V1C001", "original"))
    registry = SourceRegistry(project_root=tmp_path / "p")
    registry.register_file(
        tmp_path / "p" / "drafts" / "V1C001" / "final.md",
        source_id="draft.current",
        asset_type="draft",
        selection_reason="edit_baseline",
    )
    asyncio.run(storage.save_current_draft("p", "V1C001", "changed"))
    asyncio.run(storage.save_current_draft("p", "V1C001", "original"))
    verification = registry.verify_mutable_sources()
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
