"""W4 OpenTelemetry, SLO, consistent recovery and release evidence contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import zipfile
from pathlib import Path

import pytest

from app.context_engine.trace_collector import TraceCollector, TraceEventType
from app.control_plane.store import SQLiteControlStore
from app.observability.otel import TelemetryRuntime
from app.observability.runtime_metrics import RuntimeMetrics
from app.observability.slo import SLOEvaluator
from app.ops.project_maintenance import ProjectMaintenanceService
from app.ops.release_gate import ReleaseGate
from app.storage.base import BaseStorage
from app.storage.drafts import DraftStorage


def test_real_otel_sdk_file_export_is_content_free(monkeypatch, tmp_path: Path):
    import app.observability.otel as otel_module

    runtime = TelemetryRuntime()
    runtime.configure(tmp_path, exporter_mode="file")
    monkeypatch.setattr(otel_module, "telemetry", runtime)
    collector = TraceCollector()

    async def scenario():
        event = await collector.record(
            TraceEventType.LLM_REQUEST,
            "writer",
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "request_id": "req-1",
                "prompt": "private prompt must not leave telemetry",
            },
        )
        return event

    event = asyncio.run(scenario())
    assert event.otel_trace_id and event.otel_trace_id != "0" * 32
    assert runtime.force_flush()
    text = runtime.export_path.read_text(encoding="utf-8")
    assert "wenshape.llm_request" in text
    assert "deepseek" in text
    assert "private prompt" not in text
    runtime.shutdown()


def test_http_traceparent_is_propagated_into_server_span(monkeypatch, tmp_path: Path):
    import app.observability.otel as otel_module
    from opentelemetry import trace

    runtime = TelemetryRuntime()
    runtime.configure(tmp_path, exporter_mode="file")
    monkeypatch.setattr(otel_module, "telemetry", runtime)
    observed = {}

    async def inner(_scope, _receive, send):
        context = trace.get_current_span().get_span_context()
        observed["trace_id"] = f"{context.trace_id:032x}"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = otel_module.OpenTelemetryMiddleware(inner)
    inbound_trace = "0123456789abcdef0123456789abcdef"

    async def scenario():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [(b"traceparent", f"00-{inbound_trace}-0123456789abcdef-01".encode())],
                "server": ("127.0.0.1", 8000),
            },
            receive,
            send,
        )

    asyncio.run(scenario())
    assert observed["trace_id"] == inbound_trace
    assert runtime.force_flush()
    runtime.shutdown()


def test_windowed_slo_reports_error_budget_and_low_cardinality_alerts():
    metrics = RuntimeMetrics()
    for _ in range(99):
        metrics.increment("writer.turn.success")
    metrics.increment("writer.turn.failure")
    metrics.observe("writer.turn.latency_ms", 130_000)
    metrics.increment("gateway.success", 98)
    metrics.increment("gateway.retry_exhausted", 2)
    report = SLOEvaluator(metrics).evaluate(
        window_seconds=300,
        queue={"oldest_queued_age_seconds": 400, "dead_letter": 1},
    )
    assert report["healthy"] is False
    assert set(report["alerts"]) >= {
        "writer_turn_p95_ms",
        "queue_oldest_age_seconds",
        "provider_retry_exhaustion_rate",
        "dead_letter_count",
    }
    assert report["error_budget"]["provider_remaining"] == 0.0


def test_backup_waits_for_stable_multi_file_generation(tmp_path: Path):
    data = tmp_path / "data"
    storage = BaseStorage(str(data))
    service = ProjectMaintenanceService(data)
    backup_path = tmp_path / "consistent.zip"

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def writer():
            async with storage.content_transaction("p"):
                await storage.write_text(data / "p" / "a.txt", "new-a")
                entered.set()
                await release.wait()
                await storage.write_text(data / "p" / "b.txt", "new-b")

        task = asyncio.create_task(writer())
        await entered.wait()
        backup_task = asyncio.create_task(asyncio.to_thread(service.backup, "p", backup_path))
        await asyncio.sleep(0.1)
        assert backup_task.done() is False
        release.set()
        await task
        return await backup_task

    result = asyncio.run(scenario())
    assert result["generation"] % 2 == 0
    with zipfile.ZipFile(backup_path, "r") as archive:
        assert archive.read("project/a.txt") == b"new-a"
        assert archive.read("project/b.txt") == b"new-b"


def test_cancelled_content_transaction_closes_generation(tmp_path: Path):
    data = tmp_path / "data"
    storage = BaseStorage(str(data))

    async def scenario():
        entered = asyncio.Event()

        async def writer():
            async with storage.content_transaction("p"):
                entered.set()
                await asyncio.sleep(10)

        task = asyncio.create_task(writer())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    generation = SQLiteControlStore(data / "_system" / "control.sqlite3").project_generation("p")
    assert generation["stable"] is True
    assert generation["active_writers"] == 0


def test_backup_restore_recovers_project_scoped_revision_checkpoint(tmp_path: Path):
    data = tmp_path / "data"
    storage = DraftStorage(str(data))
    asyncio.run(storage.save_current_draft("source", "V1C001", "content"))
    source_revision = storage.get_draft_revision("source", "V1C001")
    service = ProjectMaintenanceService(data)
    backup = service.backup("source", tmp_path / "source.zip")
    restored = service.restore(tmp_path / "source.zip", project_id="restored")
    target_revision = SQLiteControlStore(data / "_system" / "control.sqlite3").get_revision(
        "draft", "restored/V1C001/final.md"
    )
    assert backup["generation"] == restored["generation"]
    assert target_revision["revision"] == source_revision["revision"]
    assert target_revision["fingerprint"] == source_revision["fingerprint"]
    assert restored["smoke"]["valid"] is True


def test_tampered_control_checkpoint_is_rejected(tmp_path: Path):
    data = tmp_path / "data"
    project = data / "p"
    project.mkdir(parents=True)
    (project / "value.txt").write_text("value", encoding="utf-8")
    service = ProjectMaintenanceService(data)
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    service.backup("p", original)
    with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for member in source.infolist():
            content = source.read(member.filename)
            if member.filename == "control/control.sqlite3":
                content += b"tampered"
            target.writestr(member, content)
    with pytest.raises(ValueError, match="control_checkpoint_hash_mismatch"):
        service.restore(tampered, project_id="restored")
    assert not (data / "restored").exists()


def test_restore_control_import_failure_rolls_back_existing_target(monkeypatch, tmp_path: Path):
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

    def fail_import(*_args, **_kwargs):
        raise RuntimeError("injected_control_import_failure")

    monkeypatch.setattr(SQLiteControlStore, "import_project_checkpoint", fail_import)
    with pytest.raises(RuntimeError, match="injected_control_import_failure"):
        service.restore(backup, project_id="target", overwrite=True)
    assert (target / "value.txt").read_text(encoding="utf-8") == "target"


def _release_fixture(tmp_path: Path, *, revision: str = "revision", created_at: float | None = None):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    directory = tmp_path / "campaign"
    directory.mkdir()
    config = {"id": "campaign", "corpora": [{"benchmark_id": "public"}]}
    config_path = directory / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    now = time.time() if created_at is None else created_at
    manifest = {
        "schema_version": 2,
        "campaign_id": "campaign",
        "campaign_fingerprint": ReleaseGate._stable_fingerprint(config),
        "code_revision": revision,
        "dirty_worktree": False,
        "evidence_scope": "global",
        "quality_gates": {"provider_scope_gate_passed": True, "global_scope_gate_passed": True},
        "artifacts": {"config.json": hashlib.sha256(config_path.read_bytes()).hexdigest()},
        "created_at": now,
        "expires_at": now + 3600,
    }
    manifest_path = directory / "release_quality_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def runner(_command, _cwd):
        return {"success": True, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    gate = ReleaseGate(repo, runner=runner)
    gate._git_state = lambda: {"revision": "revision", "dirty": False}
    return gate, manifest_path, config_path


@pytest.mark.parametrize("failure", ["stale", "revision", "artifact", "dirty"])
def test_release_gate_rejects_unbound_or_stale_evidence(tmp_path: Path, failure: str):
    created_at = time.time() - 7200 if failure == "stale" else None
    gate, manifest, config = _release_fixture(tmp_path, created_at=created_at)
    if failure == "revision":
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["code_revision"] = "other"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
    elif failure == "artifact":
        config.write_text('{"changed":true}', encoding="utf-8")
    elif failure == "dirty":
        gate._git_state = lambda: {"revision": "revision", "dirty": True}
    report = gate.run(
        campaign_manifests=[manifest],
        require_global_campaign=True,
        max_evidence_age_seconds=3600,
    )
    assert report["success"] is False


def test_release_gate_engineering_only_is_explicit(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)

    def runner(_command, _cwd):
        return {"success": True, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    gate = ReleaseGate(repo, runner=runner)
    gate._git_state = lambda: {"revision": "revision", "dirty": False}
    assert gate.run(require_campaign_evidence=False)["success"] is True
    assert gate.run(require_campaign_evidence=True)["success"] is False
