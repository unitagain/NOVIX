#!/usr/bin/env python3
"""Run W8 deterministic crash, migration, telemetry and supply-chain validation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.observability.runtime_metrics import RuntimeMetrics
from app.ops.production_validation import ProductionValidationSuite, SupplyChainScanner


def _npm_audit(directory: Path, *, omit_dev: bool) -> dict:
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm") or "npm"
    command = [npm, "audit", "--json"]
    if omit_dev:
        command.append("--omit=dev")
    completed = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding="utf-8")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"error": "npm_audit_invalid_json", "stderr": completed.stderr[-500:]}
    payload["returncode"] = completed.returncode
    return payload


def _python_audit(python: str, requirements: Path) -> dict:
    completed = subprocess.run(
        [
            python,
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "--format",
            "json",
            "--no-deps",
            "--disable-pip",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    try:
        rows = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return {"success": False, "reason": "pip_audit_invalid_json", "stderr": completed.stderr[-500:]}
    dependencies = rows.get("dependencies") or [] if isinstance(rows, dict) else rows
    vulnerabilities = sum(len(row.get("vulns") or []) for row in dependencies if isinstance(row, dict))
    return {
        "success": completed.returncode == 0 and vulnerabilities == 0,
        "vulnerabilities": vulnerabilities,
        "returncode": completed.returncode,
        "stderr_tail": completed.stderr[-500:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=BACKEND_ROOT / ".runtime" / "w8")
    parser.add_argument("--package-root", action="append", default=[])
    parser.add_argument("--pip-audit-python", default=sys.executable)
    parser.add_argument("--package-smoke", type=Path)
    parser.add_argument("--release-channel", choices=("development", "stable"), default="development")
    parser.add_argument("--skip-network-audit", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="wenshape-w8-") as temporary:
        suite = ProductionValidationSuite(Path(temporary))
        crash = suite.run_crash_matrix()
        migration = suite.run_migration_matrix()

    metrics = RuntimeMetrics(max_series=16, max_events=100, max_histogram_samples=20)
    for _ in range(20):
        metrics.increment("writer.turn.success")
        metrics.observe("writer.turn.latency_ms", 10.0)
    telemetry = {"success": metrics.budget_report()["healthy"], **metrics.budget_report()}

    scanner = SupplyChainScanner(REPO_ROOT)
    sbom_path = output / "sbom.cyclonedx.json"
    sbom = scanner.generate_sbom(sbom_path)
    roots = [Path(value).resolve() for value in args.package_root]
    secrets = scanner.scan_secrets(roots) if roots else {"success": False, "reason": "package_root_required"}
    tree = scanner.package_tree_manifest(roots[0], output / "package-tree.json") if roots else {"success": False}
    tree_verified = scanner.verify_package_tree(output / "package-tree.json") if roots else {"success": False}
    signature = (
        scanner.authenticode_report(roots[0], require_signed=args.release_channel == "stable")
        if roots
        else {"success": False, "reason": "package_root_required"}
    )
    package_smoke = (
        json.loads(args.package_smoke.read_text(encoding="utf-8-sig"))
        if args.package_smoke and args.package_smoke.is_file()
        else {"success": False, "reason": "package_smoke_required"}
    )
    if args.skip_network_audit:
        audit = {"success": False, "reason": "network_audit_required"}
    else:
        audit = scanner.audit_report(
            _npm_audit(REPO_ROOT / "frontend", omit_dev=True),
            _npm_audit(REPO_ROOT / "desktop", omit_dev=True),
        )
        python_audit = _python_audit(args.pip_audit_python, BACKEND_ROOT / "requirements.runtime.txt")
        audit = {
            "success": bool(audit["success"] and python_audit["success"]),
            "npm_runtime": audit,
            "python_runtime": python_audit,
            "desktop_build_tools": _npm_audit(REPO_ROOT / "desktop", omit_dev=False).get("metadata", {}),
        }
    package_security = {
        "success": bool(
            sbom["success"]
            and secrets.get("success")
            and audit.get("success")
            and tree.get("success")
            and tree_verified.get("success")
            and signature.get("success")
            and package_smoke.get("success")
        ),
        "sbom": sbom,
        "secret_scan": secrets,
        "dependency_audit": audit,
        "package_tree": tree,
        "package_tree_verification": tree_verified,
        "authenticode": signature,
        "release_channel": args.release_channel,
        "runtime_smoke": package_smoke,
    }
    results = {
        "crash_matrix": crash,
        "migration_matrix": migration,
        "telemetry_budget": telemetry,
        "package_security": package_security,
    }
    for name, payload in results.items():
        (output / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(row.get("success") for row in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
