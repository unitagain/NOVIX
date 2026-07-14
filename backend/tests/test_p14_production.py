"""P14 production reliability, security, recovery and release-gate tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from app.context_engine.trace_collector import TraceEvent, TraceEventType
from app.jobs.durable_queue import DurableTaskQueue, DurableTaskWorker
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.reliability import GatewayReliabilityController, PersistentIdempotencyRegistry
from app.observability.runtime_metrics import trace_to_otel_spans
from app.ops.project_maintenance import ProjectMaintenanceService
from app.ops.release_gate import ReleaseGate
from app.security.egress_ledger import EgressLedger
from app.orchestrator.architecture import service_boundaries


class Provider:
    def __init__(self, *, name="deepseek", failures=0, delay=0.0):
        self.name = name
        self.failures = failures
        self.delay = delay
        self.calls = 0
        self.model = "test-model"
        self.last_options = {}

    def get_provider_name(self):
        return self.name

    async def chat(self, messages, **options):
        self.calls += 1
        self.last_options = options
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.failures:
            raise TimeoutError("injected_timeout")
        return {
            "content": "ok",
            "model": self.model,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def _gateway(provider):
    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": provider}
    gateway.max_retries = 1
    gateway.retry_delays = [0]
    gateway.max_retry_delay = 0
    gateway.request_timeout = 1.0
    gateway.reliability = GatewayReliabilityController(failure_threshold=2, cooldown_seconds=30)
    gateway.total_tokens = 0
    gateway.total_requests = 0
    return gateway


def test_gateway_timeout_retry_idempotency_and_capability(monkeypatch, tmp_path):
    import app.llm_gateway.gateway as gateway_module

    ledger = EgressLedger(str(tmp_path))
    monkeypatch.setattr(gateway_module, "egress_ledger", ledger)
    provider = Provider(failures=1)
    gateway = _gateway(provider)
    gateway.idempotency_registry = PersistentIdempotencyRegistry(tmp_path / "idempotency")
    first = asyncio.run(
        gateway.chat(
            [{"role": "user", "content": "private-secret"}],
            provider="profile",
            idempotency_key="stable-request",
        )
    )
    second = asyncio.run(
        gateway.chat(
            [{"role": "user", "content": "private-secret"}],
            provider="profile",
            idempotency_key="stable-request",
        )
    )
    assert provider.calls == 2
    assert first["request_id"]
    assert second["idempotency_replayed"] is True
    ledger_text = ledger.path.read_text(encoding="utf-8")
    assert "private-secret" not in ledger_text
    assert "request_fingerprint" in ledger_text

    anthropic = Provider(name="anthropic")
    gateway = _gateway(anthropic)
    gateway.idempotency_registry = PersistentIdempotencyRegistry(tmp_path / "idempotency-anthropic")
    asyncio.run(
        gateway.chat(
            [{"role": "user", "content": "x"}],
            provider="profile",
            response_format={"type": "json_object"},
            retry=False,
        )
    )
    assert "response_format" not in anthropic.last_options


def test_gateway_timeout_and_circuit_breaker(monkeypatch, tmp_path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = Provider(delay=0.05)
    gateway = _gateway(provider)
    gateway.idempotency_registry = PersistentIdempotencyRegistry(tmp_path / "idempotency-timeout")
    gateway.reliability.failure_threshold = 1
    with pytest.raises(Exception):
        asyncio.run(
            gateway.chat(
                [{"role": "user", "content": "x"}],
                provider="profile",
                timeout_seconds=0.02,
                retry=False,
            )
        )
    with pytest.raises(RuntimeError, match="provider_circuit_open"):
        asyncio.run(gateway.chat([{"role": "user", "content": "x"}], provider="profile", retry=False))


def test_otel_mapping_contains_standard_ids():
    event = TraceEvent(
        id="evt",
        type=TraceEventType.LLM_RESPONSE,
        agent_name="writer",
        timestamp=1.0,
        trace_id="trace_abc",
        span_id="1",
        data={"provider": "deepseek", "latency_ms": 10, "content": "must-not-export"},
    )
    span = trace_to_otel_spans([event])[0]
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    assert span["attributes"]["wenshape.provider"] == "deepseek"
    assert "content" not in json.dumps(span)


def test_persistent_idempotency_blocks_restart_reissue(tmp_path):
    registry = PersistentIdempotencyRegistry(tmp_path / "registry")
    first = asyncio.run(registry.begin("key", "fingerprint"))
    assert first["_existing"] is False
    asyncio.run(registry.complete("key", "fingerprint", {"provider": "deepseek", "model": "m"}))
    restarted = PersistentIdempotencyRegistry(tmp_path / "registry")
    second = asyncio.run(restarted.begin("key", "fingerprint"))
    assert second["_existing"] is True
    assert second["status"] == "completed"


def test_durable_queue_idempotency_lease_retry_and_dead_letter(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")
    first = asyncio.run(queue.enqueue("work", {"value": 1}, idempotency_key="same", max_attempts=2))
    second = asyncio.run(queue.enqueue("work", {"value": 1}, idempotency_key="same", max_attempts=2))
    assert first["id"] == second["id"]
    claimed = asyncio.run(queue.claim("worker"))
    claimed["lease_expires_at"] = 0
    queue._write(claimed)
    assert asyncio.run(queue.recover_expired()) == 1
    claimed = asyncio.run(queue.claim("worker"))
    assert asyncio.run(queue.fail(claimed["id"], "worker", "boom", retry_delay=0))
    assert queue.get(claimed["id"])["status"] == "dead_letter"


def test_durable_worker_completes_persisted_job(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")

    async def handler(payload):
        return {"value": payload["value"] + 1}

    async def scenario():
        job = await queue.enqueue("work", {"value": 1}, idempotency_key="job")
        worker = DurableTaskWorker(queue, {"work": handler}, poll_seconds=0.01)
        await worker.start()
        for _ in range(100):
            if (queue.get(job["id"]) or {}).get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        await worker.stop()
        return queue.get(job["id"])

    completed = asyncio.run(scenario())
    assert completed["status"] == "completed"
    assert completed["result"] == {"value": 2}


def test_backup_restore_migration_and_corruption_scan(tmp_path):
    data = tmp_path / "data"
    project = data / "source"
    (project / "canon").mkdir(parents=True)
    (project / "canon" / "facts.jsonl").write_text('{"id":"f1"}\n', encoding="utf-8")
    (project / "drafts").mkdir()
    (project / "drafts" / "V1C001.md").write_text("正文", encoding="utf-8")
    service = ProjectMaintenanceService(data)
    backup = tmp_path / "source.wenshape-backup.zip"
    created = service.backup("source", backup)
    assert created["success"] is True
    restored = service.restore(backup, project_id="restored")
    assert restored["project_fingerprint"] == created["project_fingerprint"]
    assert service.scan("restored")["valid"] is True
    migration = service.migrate("restored")
    assert migration["version"] == 1
    (data / "restored" / "canon" / "facts.jsonl").write_text("{broken\n", encoding="utf-8")
    scan = service.scan("restored")
    assert scan["valid"] is False
    assert scan["issues"][0]["type"] == "parse_error"


def test_restore_failure_does_not_overwrite_existing_project(tmp_path):
    data = tmp_path / "data"
    source = data / "source"
    source.mkdir(parents=True)
    (source / "value.txt").write_text("source", encoding="utf-8")
    target = data / "target"
    target.mkdir(parents=True)
    (target / "value.txt").write_text("target", encoding="utf-8")
    service = ProjectMaintenanceService(data)
    backup = tmp_path / "backup.zip"
    service.backup("source", backup)
    with pytest.raises(FileExistsError):
        service.restore(backup, project_id="target", overwrite=False)
    assert (target / "value.txt").read_text(encoding="utf-8") == "target"


def test_release_gate_combines_commands_and_campaign(tmp_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    config = {"id": "c", "corpora": [{"benchmark_id": "demo"}]}
    config_path = campaign_dir / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    fingerprint = ReleaseGate._stable_fingerprint(config)
    manifest = campaign_dir / "campaign.json"
    now = time.time()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "campaign_id": "c",
                "campaign_fingerprint": fingerprint,
                "code_revision": "revision",
                "dirty_worktree": False,
                "evidence_scope": "global",
                "quality_gates": {"global_scope_gate_passed": True, "provider_scope_gate_passed": True},
                "artifacts": {"config.json": config_hash},
                "created_at": now,
                "expires_at": now + 3600,
            }
        ),
        encoding="utf-8",
    )

    def runner(command, cwd):
        return {"success": True, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    gate = ReleaseGate(repo, runner=runner)
    gate._git_state = lambda: {"revision": "revision", "dirty": False}
    report = gate.run(campaign_manifests=[manifest], require_global_campaign=True)
    assert report["success"] is True
    assert report["code_revision"] == "revision"
    assert report["fingerprint"]
    commands = {item["name"]: item["command"] for item in report["checks"]}
    assert commands["pytest"] == ["python", "-m", "pytest", "-W", "error"]
    assert commands["error_contract"] == ["python", "scripts/error_contract_audit.py"]
    assert commands["architecture"] == ["python", "scripts/architecture_profile.py", "--check"]


def test_p14_architecture_contract_declares_production_owners():
    boundaries = {row["name"]: row for row in service_boundaries()}
    assert "reliability controller" in boundaries["provider_reliability"]["current"]
    assert "DurableTaskQueue" in boundaries["durable_jobs"]["current"]
    assert "Generation-consistent" in boundaries["operations"]["current"]
    assert "OpenTelemetry SDK" in boundaries["observability"]["current"]
