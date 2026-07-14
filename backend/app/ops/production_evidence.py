"""Revision-bound W8 production evidence manifests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable


REQUIRED_CHECKS = {"crash_matrix", "migration_matrix", "telemetry_budget", "package_security"}


class ProductionEvidenceBundle:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def build(
        self,
        *,
        checks: Dict[str, Dict[str, Any]],
        artifacts: Iterable[str | Path],
        output: str | Path,
        expires_in_seconds: float = 30 * 86400,
    ) -> Dict[str, Any]:
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_hashes: Dict[str, str] = {}
        for value in artifacts:
            path = Path(value).resolve()
            relative = path.relative_to(output_path.parent).as_posix()
            artifact_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        git_state = self._git_state()
        created_at = time.time()
        missing = sorted(REQUIRED_CHECKS - set(checks))
        payload = {
            "schema_version": 1,
            "code_revision": git_state["revision"],
            "dirty_worktree": git_state["dirty"],
            "created_at": created_at,
            "expires_at": created_at + max(1.0, float(expires_in_seconds)),
            "checks": {name: self._check_summary(name, report) for name, report in checks.items()},
            "required_checks": sorted(REQUIRED_CHECKS),
            "missing_checks": missing,
            "artifacts": artifact_hashes,
            "success": (
                not git_state["dirty"]
                and bool(git_state["revision"])
                and not missing
                and all(bool((checks.get(name) or {}).get("success")) for name in REQUIRED_CHECKS)
            ),
        }
        payload["failure_reasons"] = [
            reason
            for reason, failed in (
                ("dirty_worktree", git_state["dirty"]),
                ("missing_revision", not bool(git_state["revision"])),
                ("missing_checks", bool(missing)),
                ("failed_checks", not all(bool((checks.get(name) or {}).get("success")) for name in REQUIRED_CHECKS)),
            )
            if failed
        ]
        payload["fingerprint"] = self._fingerprint(payload)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    @staticmethod
    def _check_summary(name: str, report: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"success": bool(report.get("success"))}
        if name == "soak":
            for key in (
                "classification",
                "elapsed_seconds",
                "cycles",
                "sessions",
                "requests",
                "tokens",
                "compacts",
                "backups",
                "memory_growth_bytes",
                "slo_calibration",
            ):
                if key in report:
                    summary[key] = report[key]
            runtime = report.get("runtime_smoke") or {}
            if runtime:
                summary["runtime_smoke"] = {
                    key: runtime[key]
                    for key in ("success", "action", "runtime_state", "route", "plan_version", "llm_requests")
                    if key in runtime
                }
        elif name == "crash_matrix":
            summary["boundaries"] = int(report.get("boundaries") or len(report.get("cases") or []))
        elif name == "migration_matrix":
            summary["checks"] = report.get("checks") or {}
        elif name == "telemetry_budget":
            for key in (
                "healthy",
                "checks",
                "series",
                "series_limit",
                "event_count",
                "event_limit",
                "histogram_samples",
                "histogram_sample_limit_per_series",
            ):
                if key in report:
                    summary[key] = report[key]
        elif name == "package_security":
            audit = report.get("dependency_audit") or {}
            tree = report.get("package_tree") or {}
            secrets = report.get("secret_scan") or {}
            summary.update(
                {
                    "sbom_components": int((report.get("sbom") or {}).get("components") or 0),
                    "secret_findings": len(secrets.get("findings") or []),
                    "files_scanned": int(secrets.get("files_scanned") or 0),
                    "dependency_audit_success": bool(audit.get("success")),
                    "package_file_count": int(tree.get("file_count") or 0),
                    "package_total_bytes": int(tree.get("total_bytes") or 0),
                    "package_tree_fingerprint": tree.get("tree_fingerprint"),
                    "authenticode": report.get("authenticode") or {},
                    "release_channel": report.get("release_channel"),
                    "runtime_smoke": report.get("runtime_smoke") or {},
                }
            )
        return summary

    def _git_state(self) -> Dict[str, Any]:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8"
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.repo_root, capture_output=True, text=True, encoding="utf-8"
        )
        return {"revision": revision.stdout.strip(), "dirty": bool(status.stdout.strip())}

    @staticmethod
    def _fingerprint(value: Any) -> str:
        normalized = dict(value)
        normalized.pop("fingerprint", None)
        raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
