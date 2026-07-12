"""Unified release gate for deterministic, campaign and recovery evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


class ReleaseGate:
    def __init__(
        self,
        repo_root: Path,
        *,
        runner: Optional[Callable[[List[str], Path], Dict[str, Any]]] = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.backend = self.repo_root / "backend"
        self.runner = runner or self._run_command

    def run(
        self,
        *,
        campaign_manifests: Iterable[str | Path] = (),
        production_manifests: Iterable[str | Path] = (),
        require_global_campaign: bool = False,
        require_campaign_evidence: bool = True,
        require_production_evidence: bool = False,
        require_clean_worktree: bool = True,
        max_evidence_age_seconds: float = 30 * 86400,
        output: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        checks = [
            ("ruff", ["python", "-m", "ruff", "check", "app", "tests", "scripts"], self.backend),
            ("pytest", ["python", "-m", "pytest", "-W", "error"], self.backend),
            ("error_contract", ["python", "scripts/error_contract_audit.py"], self.backend),
            ("architecture", ["python", "scripts/architecture_profile.py", "--check"], self.backend),
            ("pip_check", ["python", "-m", "pip", "check"], self.backend),
            (
                "requirements_sync",
                ["python", "scripts/check_requirements_sync.py"],
                self.repo_root,
            ),
            ("sidecar_syntax", ["node", "--check", "desktop/scripts/build-sidecar.mjs"], self.repo_root),
            ("diff_check", ["git", "diff", "--check"], self.repo_root),
            (
                "fault_injection",
                ["python", "-m", "pytest", "tests/test_p14_production.py"],
                self.backend,
            ),
        ]
        results = []
        for name, command, cwd in checks:
            result = dict(self.runner(command, cwd))
            result.update({"name": name, "command": command, "cwd": str(cwd)})
            results.append(result)
        git_state = self._git_state()
        campaign_paths = [Path(path) for path in campaign_manifests]
        campaigns = [
            self._campaign_check(
                path,
                required_scope="global" if require_global_campaign else "provider",
                current_revision=git_state["revision"],
                max_age_seconds=max_evidence_age_seconds,
            )
            for path in campaign_paths
        ]
        production = [
            self._production_check(
                Path(path),
                current_revision=git_state["revision"],
                max_age_seconds=max_evidence_age_seconds,
            )
            for path in production_manifests
        ]
        repository_checks = {
            "clean_worktree": not git_state["dirty"] if require_clean_worktree else True,
            "campaign_evidence_present": bool(campaigns) if require_campaign_evidence else True,
            "production_evidence_present": bool(production) if require_production_evidence else True,
        }
        passed = (
            all(item.get("success") for item in results)
            and all(item.get("success") for item in campaigns)
            and all(item.get("success") for item in production)
            and all(repository_checks.values())
        )
        report = {
            "schema_version": 2,
            "success": passed,
            "created_at": time.time(),
            "code_revision": git_state["revision"],
            "dirty_worktree": git_state["dirty"],
            "repository_checks": repository_checks,
            "checks": results,
            "campaigns": campaigns,
            "production": production,
        }
        report["fingerprint"] = hashlib.sha256(
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _production_check(
        path: Path,
        *,
        current_revision: str,
        max_age_seconds: float,
    ) -> Dict[str, Any]:
        if not path.is_file():
            return {"success": False, "path": str(path), "reason": "production_manifest_missing"}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"success": False, "path": str(path), "reason": "production_manifest_invalid"}
        required_checks = set(payload.get("required_checks") or ())
        checks = payload.get("checks") or {}
        now = time.time()
        freshness_ok = (
            float(payload.get("created_at") or 0.0) > 0
            and now - float(payload.get("created_at") or 0.0) <= float(max_age_seconds)
            and float(payload.get("expires_at") or 0.0) >= now
        )
        revision_ok = bool(current_revision) and payload.get("code_revision") == current_revision
        clean_ok = payload.get("dirty_worktree") is False
        checks_ok = bool(required_checks) and required_checks <= set(checks) and all(
            bool((checks.get(name) or {}).get("success")) for name in required_checks
        )
        artifacts = ReleaseGate._verify_artifacts(path.parent, payload.get("artifacts"))
        fingerprint_payload = dict(payload)
        fingerprint = str(fingerprint_payload.pop("fingerprint", ""))
        fingerprint_ok = fingerprint == ReleaseGate._stable_fingerprint(fingerprint_payload)
        passed = all((int(payload.get("schema_version") or 0) == 1, freshness_ok, revision_ok, clean_ok, checks_ok, artifacts["valid"], fingerprint_ok))
        return {
            "success": passed,
            "path": str(path),
            "checks": {
                "schema": int(payload.get("schema_version") or 0) == 1,
                "fresh": freshness_ok,
                "revision_bound": revision_ok,
                "clean_evidence": clean_ok,
                "required_checks": checks_ok,
                "artifact_hashes": artifacts["valid"],
                "fingerprint": fingerprint_ok,
            },
            "artifact_failures": artifacts["failures"],
        }

    @staticmethod
    def _run_command(command: List[str], cwd: Path) -> Dict[str, Any]:
        completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, encoding="utf-8")
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }

    @staticmethod
    def _campaign_check(
        path: Path,
        *,
        required_scope: str,
        current_revision: str,
        max_age_seconds: float,
    ) -> Dict[str, Any]:
        if not path.exists():
            return {"success": False, "path": str(path), "reason": "manifest_missing"}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"success": False, "path": str(path), "reason": "manifest_invalid_json"}
        required = {
            "schema_version",
            "campaign_id",
            "campaign_fingerprint",
            "code_revision",
            "dirty_worktree",
            "evidence_scope",
            "quality_gates",
            "artifacts",
            "created_at",
            "expires_at",
        }
        missing = sorted(required - set(payload))
        if missing or int(payload.get("schema_version") or 0) != 2:
            return {
                "success": False,
                "path": str(path),
                "reason": "manifest_schema_invalid",
                "missing": missing,
            }
        now = time.time()
        created_at = float(payload.get("created_at") or 0.0)
        expires_at = float(payload.get("expires_at") or 0.0)
        freshness_ok = created_at > 0 and now - created_at <= float(max_age_seconds) and expires_at >= now
        revision_ok = bool(current_revision) and payload.get("code_revision") == current_revision
        clean_ok = payload.get("dirty_worktree") is False
        scope_rank = {"corpus": 1, "provider": 2, "global": 3}
        scope_ok = scope_rank.get(str(payload.get("evidence_scope") or ""), 0) >= scope_rank.get(required_scope, 99)
        gates = payload.get("quality_gates") or {}
        quality_ok = (
            bool(gates.get("global_scope_gate_passed"))
            if required_scope == "global"
            else bool(gates.get("provider_scope_gate_passed") or gates.get("global_scope_gate_passed"))
        )
        artifacts = ReleaseGate._verify_artifacts(path.parent, payload.get("artifacts"))
        config_path = path.parent / "config.json"
        fingerprint_ok = False
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                fingerprint_ok = ReleaseGate._stable_fingerprint(config) == payload.get("campaign_fingerprint")
            except (OSError, ValueError, json.JSONDecodeError):
                fingerprint_ok = False
        passed = all((freshness_ok, revision_ok, clean_ok, scope_ok, quality_ok, artifacts["valid"], fingerprint_ok))
        return {
            "success": passed,
            "path": str(path),
            "campaign_id": payload.get("campaign_id"),
            "global_scope_gate_passed": bool(gates.get("global_scope_gate_passed")),
            "fingerprint": payload.get("campaign_fingerprint"),
            "checks": {
                "fresh": freshness_ok,
                "revision_bound": revision_ok,
                "clean_evidence": clean_ok,
                "scope": scope_ok,
                "quality_gate": quality_ok,
                "artifact_hashes": artifacts["valid"],
                "campaign_fingerprint": fingerprint_ok,
            },
            "artifact_failures": artifacts["failures"],
        }

    def _git_state(self) -> Dict[str, Any]:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return {"revision": revision.stdout.strip(), "dirty": bool(status.stdout.strip())}

    def _git_revision(self) -> str:
        return str(self._git_state()["revision"])

    @staticmethod
    def _verify_artifacts(root: Path, artifacts: Any) -> Dict[str, Any]:
        if not isinstance(artifacts, dict) or not artifacts:
            return {"valid": False, "failures": [{"reason": "artifacts_missing"}]}
        failures = []
        root = root.resolve()
        for relative, expected in artifacts.items():
            path = (root / str(relative)).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                failures.append({"path": str(relative), "reason": "artifact_path_outside_manifest"})
                continue
            if not path.is_file():
                failures.append({"path": str(relative), "reason": "artifact_missing"})
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != str(expected or ""):
                failures.append({"path": str(relative), "reason": "artifact_hash_mismatch"})
        return {"valid": not failures, "failures": failures}

    @staticmethod
    def _stable_fingerprint(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
