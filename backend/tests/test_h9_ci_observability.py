from __future__ import annotations

from pathlib import Path

from app.context_engine.trace_collector import TraceCollector, TraceEventType
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope
from app.observability.otel import TelemetryRuntime
from app.ops.engineering_gate import engineering_checks
from app.ops.production_evidence import ProductionEvidenceBundle, REQUIRED_CHECKS
from app.ops.release_gate import ReleaseGate


def test_open_source_release_evidence_does_not_require_provider_soak():
    assert "soak" not in REQUIRED_CHECKS
    assert REQUIRED_CHECKS == {"crash_matrix", "migration_matrix", "telemetry_budget", "package_security"}


def test_ci_matrix_and_release_gate_share_engineering_checks():
    repo = Path(__file__).resolve().parents[2]
    workflow = (repo / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    pytest_config = (repo / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert workflow.count('branches: ["develop"]') == 2
    assert "main" not in workflow
    assert "master" not in workflow
    assert "workflow_dispatch:" in workflow
    assert 'python-version: ["3.10", "3.11", "3.12"]' in workflow
    assert "backend-windows-critical" in workflow
    assert "frontend:" not in workflow
    assert "python scripts/engineering_gate.py" in workflow
    assert 'filterwarnings = [' in pytest_config
    assert '"error"' in pytest_config
    assert "python_multipart" in pytest_config
    assert "The 'app' shortcut is now deprecated" in pytest_config
    expected = [name for name, _command, _cwd in engineering_checks(repo)]
    gate = ReleaseGate(repo, runner=lambda _command, _cwd: {"success": True})
    gate._git_state = lambda: {"revision": "revision", "dirty": False}
    report = gate.run(require_campaign_evidence=False, require_clean_worktree=False)
    actual = [row["name"] for row in report["checks"][: len(expected)]]
    assert actual == expected


def test_trace_correlation_is_low_cardinality_and_content_free(monkeypatch, tmp_path: Path):
    import asyncio
    import app.observability.otel as otel_module

    runtime = TelemetryRuntime()
    runtime.configure(tmp_path, exporter_mode="file")
    monkeypatch.setattr(otel_module, "telemetry", runtime)
    collector = TraceCollector()
    scope = new_turn_scope(project_id="project", turn_id="turn-1")

    async def scenario():
        with bind_turn_scope(scope):
            await collector.record(
                TraceEventType.LLM_RESPONSE,
                "writer",
                {
                    "trace_id": scope.trace_id,
                    "turn_id": scope.turn_id,
                    "job_id": "job-1",
                    "request_id": "request-1",
                    "provider": "openai",
                    "model": "model-1",
                    "content": "private manuscript text",
                },
            )

    asyncio.run(scenario())
    assert runtime.force_flush()
    text = runtime.export_path.read_text(encoding="utf-8")
    for expected in (scope.trace_id, "turn-1", "job-1", "request-1", "openai", "model-1"):
        assert expected in text
    assert "private manuscript text" not in text
    runtime.shutdown()


def test_file_exporter_rotates_with_bounded_retention(tmp_path: Path):
    runtime = TelemetryRuntime()
    runtime.configure(tmp_path, exporter_mode="file")
    runtime.exporter.max_file_bytes = 1024
    runtime.exporter.retained_files = 2
    for index in range(20):
        with runtime.span(
            "rotation-test",
            attributes={"wenshape.request_fingerprint": f"{index}-" + ("x" * 400)},
        ):
            pass
        assert runtime.force_flush()
    budget = runtime.exporter.budget_report()
    assert budget["healthy"] is True
    assert budget["rotations"] > 0
    assert len(list(runtime.export_path.parent.glob("spans.jsonl.*"))) <= 2
    runtime.shutdown()


def test_production_evidence_cannot_succeed_for_dirty_source(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    checks = {name: {"success": True} for name in REQUIRED_CHECKS}
    bundle = ProductionEvidenceBundle(tmp_path)
    bundle._git_state = lambda: {"revision": "revision", "dirty": True}
    payload = bundle.build(checks=checks, artifacts=[artifact], output=tmp_path / "manifest.json")
    assert payload["success"] is False
    assert "dirty_worktree" in payload["failure_reasons"]
    assert ReleaseGate._production_check(
        tmp_path / "manifest.json", current_revision="revision", max_age_seconds=60
    )["success"] is False
