"""W8 production validation, recovery and release-evidence contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from app.observability.otel import JsonlSpanExporter
from app.observability.runtime_metrics import RuntimeMetrics
from app.ops.production_evidence import ProductionEvidenceBundle, REQUIRED_CHECKS
from app.ops.production_validation import ProductionValidationSuite, SupplyChainScanner, resource_growth_report
from app.ops.project_maintenance import ProjectMaintenanceService
from app.ops.release_gate import ReleaseGate
from app.security.headers import CONTENT_SECURITY_POLICY, SecurityHeadersMiddleware


def test_runtime_and_exporter_budgets_are_bounded(tmp_path: Path):
    metrics = RuntimeMetrics(max_series=2, max_events=3, max_histogram_samples=2)
    metrics.increment("one")
    metrics.observe("two", 1)
    metrics.observe("two", 2)
    metrics.observe("two", 3)
    report = metrics.budget_report()
    assert report["healthy"] is True
    assert report["event_count"] == 3
    assert report["histogram_samples"] == 2

    metrics.increment("three")
    assert metrics.budget_report()["checks"]["series_cardinality"] is False
    exporter = JsonlSpanExporter(tmp_path / "spans.jsonl")
    assert exporter.budget_report(max_file_bytes=1)["healthy"] is True


def test_security_headers_are_applied_to_http_only():
    messages = []

    async def inner(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    import asyncio

    asyncio.run(SecurityHeadersMiddleware(inner)({"type": "http"}, receive, send))
    headers = dict(messages[0]["headers"])
    assert headers[b"content-security-policy"].decode("ascii") == CONTENT_SECURITY_POLICY
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"


def test_resource_growth_report_blocks_unbounded_growth():
    stable = resource_growth_report([{"timestamp": 1, "memory": 100}, {"timestamp": 2, "memory": 110}])
    growing = resource_growth_report([{"timestamp": 1, "memory": 100}, {"timestamp": 2, "memory": 200}])
    assert stable["success"] is True
    assert growing["success"] is False


def test_crash_and_migration_matrices_cover_declared_boundaries(tmp_path: Path):
    suite = ProductionValidationSuite(tmp_path)
    crash = suite.run_crash_matrix()
    migration = suite.run_migration_matrix()
    assert crash["success"] is True
    assert {row["boundary"] for row in crash["cases"]} == {
        "file_staging",
        "file_replace",
        "generation_begin_end",
        "sqlite_commit",
        "provider_completion",
        "job_completion",
        "restore_switch",
    }
    assert migration["success"] is True


def test_interrupted_operation_recovery_restores_rollback(tmp_path: Path):
    target = tmp_path / "project"
    target.mkdir()
    (target / "value.txt").write_text("new", encoding="utf-8")
    staging = tmp_path / "_restore_staging" / "restore-test"
    rollback = staging / "rollback"
    rollback.mkdir(parents=True)
    (rollback / "value.txt").write_text("old", encoding="utf-8")
    (staging / "operation.json").write_text(
        json.dumps({"operation": "restore", "target": str(target), "rollback": str(rollback), "stage": "switched"}),
        encoding="utf-8",
    )
    result = ProjectMaintenanceService(tmp_path).recover_interrupted_operations()
    assert result["success"] is True
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"


def test_supply_chain_sbom_secret_scan_and_audit_contract(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "desktop").mkdir()
    (repo / "backend" / "requirements.lock").write_text("fastapi==1.2.3\n", encoding="utf-8")
    lock = {"packages": {"node_modules/react": {"version": "18.2.0"}}}
    for directory in ("frontend", "desktop"):
        (repo / directory / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    scanner = SupplyChainScanner(repo)
    sbom = scanner.generate_sbom(tmp_path / "sbom.json")
    clean = tmp_path / "package"
    clean.mkdir()
    (clean / "app.txt").write_text("clean", encoding="utf-8")
    assert sbom["components"] == 2
    assert scanner.scan_secrets([clean])["success"] is True
    assert scanner.audit_report({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}})["success"]
    (clean / "secret.txt").write_text("api_key=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")
    assert scanner.scan_secrets([clean])["success"] is False


def test_authenticode_policy_records_unsigned_dev_and_blocks_unsigned_stable(tmp_path: Path, monkeypatch):
    package = tmp_path / "wenshape-win32-x64"
    package.mkdir()
    (package / "wenshape.exe").write_bytes(b"launcher")
    monkeypatch.setattr("app.ops.production_validation.sys.platform", "win32")

    def unsigned_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout='{"Status":"NotSigned"}', stderr="")

    development = SupplyChainScanner.authenticode_report(package, require_signed=False, runner=unsigned_runner)
    stable = SupplyChainScanner.authenticode_report(package, require_signed=True, runner=unsigned_runner)
    assert development["success"] is True
    assert development["policy"] == "record_only"
    assert development["launchers"][0]["status"] == "NotSigned"
    assert stable["success"] is False
    assert stable["policy"] == "required"


def test_production_manifest_is_revision_bound_and_required_by_release_gate(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "soak.json"
    artifact.write_text('{"success":true}', encoding="utf-8")
    checks = {name: {"success": True} for name in REQUIRED_CHECKS}
    bundle = ProductionEvidenceBundle(repo)
    bundle._git_state = lambda: {"revision": "revision", "dirty": False}
    manifest = evidence_dir / "production.json"
    payload = bundle.build(checks=checks, artifacts=[artifact], output=manifest)
    assert payload["success"] is True

    def runner(_command, _cwd):
        return {"success": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    gate = ReleaseGate(repo, runner=runner)
    gate._git_state = lambda: {"revision": "revision", "dirty": False}
    report = gate.run(
        require_campaign_evidence=False,
        require_production_evidence=True,
        production_manifests=[manifest],
    )
    assert report["success"] is True
    artifact.write_text('{"success":false}', encoding="utf-8")
    assert gate.run(
        require_campaign_evidence=False,
        require_production_evidence=True,
        production_manifests=[manifest],
    )["success"] is False


def test_production_manifest_keeps_large_evidence_in_hash_bound_artifacts(tmp_path: Path):
    artifact = tmp_path / "package_security.json"
    artifact.write_text("{}", encoding="utf-8")
    checks = {name: {"success": True} for name in REQUIRED_CHECKS}
    checks["soak"].update({"samples": [{"memory": index} for index in range(100)], "cycles": 100})
    checks["package_security"].update(
        {
            "package_tree": {"files": [{"path": str(index)} for index in range(100)], "file_count": 100},
            "secret_scan": {"findings": [], "files_scanned": 100},
        }
    )
    bundle = ProductionEvidenceBundle(tmp_path)
    bundle._git_state = lambda: {"revision": "revision", "dirty": False}
    payload = bundle.build(checks=checks, artifacts=[artifact], output=tmp_path / "manifest.json")
    assert "samples" not in payload["checks"]["soak"]
    assert "files" not in payload["checks"]["package_security"]
    assert payload["checks"]["package_security"]["package_file_count"] == 100


def test_production_manifest_rejects_stale_or_dirty_evidence(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "code_revision": "revision",
        "dirty_worktree": True,
        "created_at": time.time() - 100,
        "expires_at": time.time() + 100,
        "checks": {name: {"success": True} for name in REQUIRED_CHECKS},
        "required_checks": sorted(REQUIRED_CHECKS),
        "missing_checks": [],
        "artifacts": {"artifact.json": hashlib.sha256(artifact.read_bytes()).hexdigest()},
        "success": True,
    }
    payload["fingerprint"] = ReleaseGate._stable_fingerprint(payload)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = ReleaseGate._production_check(manifest, current_revision="revision", max_age_seconds=10)
    assert result["success"] is False
    assert result["checks"]["clean_evidence"] is False
    assert result["checks"]["fresh"] is False
