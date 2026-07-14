"""W7 ownership, dependency and shared-contract regressions."""

from __future__ import annotations

from pathlib import Path

from app.eval.longform_benchmark import LongformBenchmarkHarness
from app.llm_gateway.capabilities import CapabilityNegotiator
from app.llm_gateway.contracts import ProviderUsage
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.payload_accounting import PayloadAccountingPort
from app.llm_gateway.provider_registry import ProviderRegistry
from app.llm_gateway.telemetry import GatewayTelemetryPort
from app.orchestrator.application_ports import OrchestratorApplicationPorts
from app.orchestrator.orchestrator import Orchestrator
from scripts.architecture_profile import _external_private_accesses, architecture_violations, build_architecture_profile


def test_architecture_has_no_dependency_cycles_or_external_private_accesses():
    profile = build_architecture_profile()
    assert profile["schema_version"] == 3
    assert profile["dependency_cycle_count"] == 0
    assert profile["dependency_cycles"] == []
    assert profile["external_private_accesses"] == []
    assert profile["change_fanout"]
    assert architecture_violations(profile) == []


def test_architecture_check_rejects_cycles_and_private_accesses():
    assert architecture_violations(
        {
            "dependency_cycles": [["app.a", "app.b"]],
            "external_private_accesses": ["tests/test_example.py:1:private_attribute:gateway._owner"],
        }
    ) == ["dependency_cycles:1", "external_private_accesses:1"]


def test_architecture_private_owner_check_follows_import_alias_and_assignment(tmp_path: Path):
    source = tmp_path / "alias.py"
    source.write_text(
        "from app.llm_gateway.gateway import LLMGateway as Gateway\n"
        "first = Gateway()\n"
        "second = first\n"
        "second._execute_chat\n",
        encoding="utf-8",
    )
    violations = _external_private_accesses([tmp_path])
    assert any("second._execute_chat" in item for item in violations)


def test_orchestrator_exposes_owned_application_ports(tmp_path: Path):
    orchestrator = Orchestrator(str(tmp_path))
    assert isinstance(orchestrator.application, OrchestratorApplicationPorts)
    assert orchestrator.application.conversation.session_history is orchestrator.session_history
    assert orchestrator.application.plans is orchestrator.plan_execution_service
    assert orchestrator.application.volumes is orchestrator.volume_summary_service
    for removed_facade in (
        "append_conversation",
        "load_conversation",
        "compact_conversation",
        "create_plan",
        "execute_plan",
    ):
        assert not hasattr(orchestrator, removed_facade)


def test_gateway_owns_explicit_component_ports():
    gateway = LLMGateway()
    assert isinstance(gateway.provider_registry, ProviderRegistry)
    assert isinstance(gateway.capability_negotiator, CapabilityNegotiator)
    assert isinstance(gateway.payload_accounting, PayloadAccountingPort)
    assert isinstance(gateway.telemetry, GatewayTelemetryPort)


def test_runtime_and_benchmark_share_provider_usage_contract():
    runtime = ProviderUsage.from_mapping({"input_tokens": 11, "output_tokens": 7}, requests=1)
    judge = ProviderUsage.from_mapping({"prompt_tokens": 5, "completion_tokens": 3, "requests": 2})
    merged = runtime.merge(judge)
    assert merged.to_dict() == {
        "requests": 3,
        "prompt_tokens": 16,
        "completion_tokens": 10,
        "total_tokens": 26,
        "elapsed_seconds": 0.0,
        "status": "reported",
        "requested_max_tokens": 0,
    }


def test_longform_benchmark_exposes_six_owned_stages(tmp_path: Path):
    harness = LongformBenchmarkHarness(tmp_path)
    pipeline = harness.pipeline
    assert pipeline.corpus.backend is harness
    assert pipeline.generation.backend is harness
    assert pipeline.judge.backend is harness
    assert pipeline.statistics.backend is harness
    assert pipeline.ledger.backend is harness
    assert pipeline.report.backend is harness
    candidate = pipeline.generation.artifact(
        artifact_id="candidate-1",
        response={"provider": "deepseek", "model": "writer", "usage": {"total_tokens": 12}},
        content="synthetic candidate",
    )
    judge = pipeline.judge.artifact(
        artifact_id="judge-1",
        provider="deepseek",
        model="judge",
        pair_fingerprint="pair",
        usage_rows=[{"prompt_tokens": 4, "completion_tokens": 2}],
        comparable=True,
    )
    assert candidate.usage.requests == 1
    assert candidate.content_fingerprint
    assert judge.usage.total_tokens == 6
    assert judge.comparable is True
