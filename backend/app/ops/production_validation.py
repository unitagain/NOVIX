"""W8 crash, migration, resource and supply-chain validation primitives."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.control_plane.store import SQLiteControlStore
from app.jobs.durable_queue import DurableTaskQueue
from app.llm_gateway.reliability import PersistentIdempotencyRegistry
from app.ops.project_maintenance import ProjectMaintenanceService


SECRET_PATTERNS = {
    "openai_key": re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),
    "anthropic_key": re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "generic_secret": re.compile(rb"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
}


def _crash_file_worker(target: str, *, replace: bool) -> None:
    path = Path(target)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("new", encoding="utf-8")
    if replace:
        os.replace(temporary, path)
    os._exit(86)


def _crash_generation_worker(data_dir: str) -> None:
    SQLiteControlStore(Path(data_dir) / "_system" / "control.sqlite3").begin_project_write("crash-generation")
    os._exit(86)


def _crash_sqlite_worker(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS evidence(value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence(value) VALUES ('committed')")
    connection.commit()
    os._exit(86)


def _crash_provider_worker(data_dir: str) -> None:
    async def scenario() -> None:
        registry = PersistentIdempotencyRegistry(Path(data_dir) / "idempotency")
        await registry.begin("request", "fingerprint")
        await registry.complete("request", "fingerprint", {"provider": "synthetic", "request_id": "one"})

    asyncio.run(scenario())
    os._exit(86)


def _crash_job_worker(data_dir: str) -> None:
    async def scenario() -> None:
        queue = DurableTaskQueue(Path(data_dir) / "queue")
        job = await queue.enqueue("evidence", {"value": 1}, idempotency_key="job")
        claimed = await queue.claim("worker")
        await queue.complete(job["id"], "worker", {"ok": True}, generation=claimed["lease_generation"])

    asyncio.run(scenario())
    os._exit(86)


def _crash_restore_worker(data_dir: str, backup: str) -> None:
    def terminate(name: str) -> None:
        if name == "restore_after_switch":
            os._exit(86)

    ProjectMaintenanceService(data_dir, failpoint=terminate).restore(backup, project_id="restore-target", overwrite=True)


def resource_growth_report(samples: List[Dict[str, float]], *, max_growth_ratio: float = 0.25) -> Dict[str, Any]:
    metrics = sorted({key for row in samples for key in row if key != "timestamp"})
    growth: Dict[str, Dict[str, float]] = {}
    checks: Dict[str, bool] = {}
    for metric in metrics:
        values = [float(row.get(metric) or 0.0) for row in samples]
        baseline = max(1.0, values[0] if values else 0.0)
        ratio = ((values[-1] - values[0]) / baseline) if len(values) > 1 else 0.0
        growth[metric] = {"start": values[0] if values else 0.0, "end": values[-1] if values else 0.0, "ratio": ratio}
        checks[metric] = ratio <= max_growth_ratio
    return {"success": bool(samples) and all(checks.values()), "checks": checks, "growth": growth, "samples": len(samples)}


class ProductionValidationSuite:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()

    def run_crash_matrix(self) -> Dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cases = [
            self._file_staging_case(),
            self._file_replace_case(),
            self._generation_case(),
            self._sqlite_commit_case(),
            asyncio.run(self._provider_completion_case()),
            asyncio.run(self._job_completion_case()),
            self._restore_switch_case(),
        ]
        return {"success": all(row["success"] for row in cases), "cases": cases, "boundaries": len(cases)}

    def run_migration_matrix(self) -> Dict[str, Any]:
        project = self.data_dir / "migration"
        project.mkdir(parents=True, exist_ok=True)
        original = project / "value.txt"
        original.write_text("stable", encoding="utf-8")
        service = ProjectMaintenanceService(self.data_dir)
        first = service.migrate("migration")
        repeated = service.migrate("migration")
        failure_project = self.data_dir / "migration-failure"
        failure_project.mkdir(parents=True, exist_ok=True)
        (failure_project / "value.txt").write_text("stable", encoding="utf-8")
        before = self._tree_fingerprint(failure_project)

        def failpoint(name: str) -> None:
            if name == "migration_before_switch":
                raise RuntimeError("injected_process_termination")

        failure_isolated = False
        try:
            ProjectMaintenanceService(self.data_dir, failpoint=failpoint).migrate("migration-failure")
        except RuntimeError:
            failure_isolated = self._tree_fingerprint(failure_project) == before
        cases = {
            "legacy_to_current": first.get("version") == 1,
            "idempotent_reapply": repeated.get("applied") == [],
            "failed_upgrade_preserves_source": failure_isolated,
            "post_failure_scan": service.scan("migration-failure").get("valid") is True,
        }
        return {"success": all(cases.values()), "checks": cases, "source_fingerprint": before}

    def _file_staging_case(self) -> Dict[str, Any]:
        target = self.data_dir / "crash-file.txt"
        target.write_text("old", encoding="utf-8")
        exitcode = self._run_crash_process(_crash_file_worker, str(target), replace=False)
        target.with_suffix(".tmp").unlink(missing_ok=True)
        return {"boundary": "file_staging", "success": exitcode == 86 and target.read_text(encoding="utf-8") == "old"}

    def _file_replace_case(self) -> Dict[str, Any]:
        target = self.data_dir / "replace.txt"
        target.write_text("old", encoding="utf-8")
        exitcode = self._run_crash_process(_crash_file_worker, str(target), replace=True)
        return {"boundary": "file_replace", "success": exitcode == 86 and target.read_text(encoding="utf-8") == "new"}

    def _generation_case(self) -> Dict[str, Any]:
        exitcode = self._run_crash_process(_crash_generation_worker, str(self.data_dir))
        store = SQLiteControlStore(self.data_dir / "_system" / "control.sqlite3")
        repaired = store.recover_abandoned_project_writes()
        state = store.project_generation("crash-generation")
        return {"boundary": "generation_begin_end", "success": exitcode == 86 and repaired == 1 and state["stable"]}

    def _sqlite_commit_case(self) -> Dict[str, Any]:
        path = self.data_dir / "commit.sqlite3"
        exitcode = self._run_crash_process(_crash_sqlite_worker, str(path))
        check = sqlite3.connect(path)
        try:
            value = check.execute("SELECT value FROM evidence").fetchone()[0]
            integrity = check.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            check.close()
        return {"boundary": "sqlite_commit", "success": exitcode == 86 and value == "committed" and integrity == "ok"}

    async def _provider_completion_case(self) -> Dict[str, Any]:
        exitcode = self._run_crash_process(_crash_provider_worker, str(self.data_dir))
        restarted = PersistentIdempotencyRegistry(self.data_dir / "idempotency")
        row = await restarted.begin("request", "fingerprint")
        return {
            "boundary": "provider_completion",
            "success": exitcode == 86 and row.get("status") == "completed" and row.get("_existing") is True,
        }

    async def _job_completion_case(self) -> Dict[str, Any]:
        exitcode = self._run_crash_process(_crash_job_worker, str(self.data_dir))
        restarted = DurableTaskQueue(self.data_dir / "queue")
        return {
            "boundary": "job_completion",
            "success": exitcode == 86 and restarted.stats().get("completed") == 1,
        }

    def _restore_switch_case(self) -> Dict[str, Any]:
        source = self.data_dir / "restore-source"
        target = self.data_dir / "restore-target"
        source.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        (source / "value.txt").write_text("source", encoding="utf-8")
        (target / "value.txt").write_text("target", encoding="utf-8")
        backup = self.data_dir / "restore.zip"
        ProjectMaintenanceService(self.data_dir).backup("restore-source", backup)

        exitcode = self._run_crash_process(_crash_restore_worker, str(self.data_dir), str(backup))
        recovery = ProjectMaintenanceService(self.data_dir).recover_interrupted_operations()
        success = (target / "value.txt").read_text(encoding="utf-8") == "target"
        return {
            "boundary": "restore_switch",
            "success": exitcode == 86 and success and bool(recovery.get("recovered")),
        }

    @staticmethod
    def _run_crash_process(target: Any, *args: Any, **kwargs: Any) -> int:
        process = multiprocessing.get_context("spawn").Process(target=target, args=args, kwargs=kwargs)
        process.start()
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join(5)
            return -1
        return int(process.exitcode or 0)

    @staticmethod
    def _tree_fingerprint(root: Path) -> str:
        rows = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            rows.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
        return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()


class SupplyChainScanner:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def generate_sbom(self, output: str | Path) -> Dict[str, Any]:
        components: List[Dict[str, str]] = []
        requirements = self.repo_root / "backend" / "requirements.lock"
        for line in requirements.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
            if match:
                components.append({"type": "library", "ecosystem": "PyPI", "name": match.group(1), "version": match.group(2)})
        for relative in ("frontend/package-lock.json", "desktop/package-lock.json"):
            lock = json.loads((self.repo_root / relative).read_text(encoding="utf-8-sig"))
            for path, value in (lock.get("packages") or {}).items():
                if not path or not isinstance(value, dict) or not value.get("version"):
                    continue
                name = str(value.get("name") or path.rsplit("node_modules/", 1)[-1])
                components.append({"type": "library", "ecosystem": "npm", "name": name, "version": str(value["version"])})
        unique = {(row["ecosystem"], row["name"], row["version"]): row for row in components}
        payload = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "components": sorted(unique.values(), key=lambda row: (row["ecosystem"], row["name"], row["version"])),
        }
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"success": bool(payload["components"]), "components": len(payload["components"]), "path": str(path)}

    def scan_secrets(self, roots: Iterable[str | Path]) -> Dict[str, Any]:
        findings = []
        for value in roots:
            root = Path(value)
            paths = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
            for path in paths:
                if path.stat().st_size > 32 * 1024 * 1024:
                    continue
                data = path.read_bytes()
                for name, pattern in SECRET_PATTERNS.items():
                    if pattern.search(data):
                        findings.append({"path": str(path), "type": name})
        return {"success": not findings, "findings": findings, "files_scanned": sum(1 for value in roots for _ in self._files(Path(value)))}

    @staticmethod
    def package_tree_manifest(root: str | Path, output: str | Path) -> Dict[str, Any]:
        package_root = Path(root).resolve()
        rows = []
        total_bytes = 0
        for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
            size = path.stat().st_size
            total_bytes += size
            rows.append(
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "size": size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        fingerprint = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        payload = {
            "schema_version": 1,
            "root": str(package_root),
            "files": rows,
            "file_count": len(rows),
            "total_bytes": total_bytes,
            "tree_fingerprint": fingerprint,
        }
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"success": bool(rows), **payload, "manifest_path": str(path)}

    @staticmethod
    def verify_package_tree(manifest_path: str | Path) -> Dict[str, Any]:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
        root = Path(manifest["root"])
        failures = []
        for row in manifest.get("files") or []:
            path = root / row["path"]
            if not path.is_file() or path.stat().st_size != int(row["size"]):
                failures.append({"path": row["path"], "reason": "missing_or_size"})
            elif hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                failures.append({"path": row["path"], "reason": "hash"})
        return {"success": not failures and bool(manifest.get("files")), "failures": failures}

    @staticmethod
    def audit_report(*reports: Dict[str, Any]) -> Dict[str, Any]:
        high = critical = 0
        for report in reports:
            metadata = report.get("metadata") or {}
            vulnerabilities = metadata.get("vulnerabilities") or report.get("vulnerabilities") or {}
            high += int(vulnerabilities.get("high") or 0)
            critical += int(vulnerabilities.get("critical") or 0)
        return {"success": high == 0 and critical == 0, "high": high, "critical": critical}

    @staticmethod
    def authenticode_report(
        root: str | Path,
        *,
        require_signed: bool,
        runner: Optional[Callable[..., subprocess.CompletedProcess[str]]] = None,
    ) -> Dict[str, Any]:
        package_root = Path(root).resolve()
        expected_name = re.sub(r"-win32-(?:x64|ia32|arm64)$", "", package_root.name) + ".exe"
        expected = package_root / expected_name
        launchers = [expected] if expected.is_file() else [
            path
            for path in sorted(package_root.glob("*.exe"))
            if path.name.lower() != "chrome_crashpad_handler.exe"
        ]
        if sys.platform != "win32":
            return {
                "success": False,
                "policy": "required" if require_signed else "record_only",
                "reason": "authenticode_requires_windows",
                "launchers": [str(path) for path in launchers],
            }
        if not launchers:
            return {
                "success": False,
                "policy": "required" if require_signed else "record_only",
                "reason": "launcher_not_found",
                "launchers": [],
            }

        execute = runner or subprocess.run
        rows = []
        status_names = {
            0: "UnknownError",
            1: "Valid",
            2: "NotSigned",
            3: "HashMismatch",
            4: "NotTrusted",
            5: "NotSupportedFileFormat",
            6: "Incompatible",
        }
        for launcher in launchers:
            literal_path = str(launcher).replace("'", "''")
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    'Import-Module "$env:WINDIR\\System32\\WindowsPowerShell\\v1.0\\Modules\\'
                    'Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1"; '
                    f"Get-AuthenticodeSignature -LiteralPath '{literal_path}' | ConvertTo-Json -Compress -Depth 3"
                ),
            ]
            completed = execute(command, capture_output=True, text=True, encoding="utf-8", check=False)
            try:
                payload = json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                payload = {}
            raw_status = payload.get("Status")
            status = status_names.get(raw_status, str(raw_status or "UnknownError"))
            signer = payload.get("SignerCertificate") or {}
            rows.append(
                {
                    "path": str(launcher),
                    "status": status,
                    "signer_subject": str(signer.get("Subject") or payload.get("SignerSubject") or ""),
                    "signer_thumbprint": str(signer.get("Thumbprint") or payload.get("SignerThumbprint") or ""),
                    "probe_success": completed.returncode == 0 and bool(payload),
                    "probe_error": "" if completed.returncode == 0 else completed.stderr[-500:],
                }
            )

        accepted = {"Valid"} if require_signed else {"Valid", "NotSigned"}
        return {
            "success": bool(rows) and all(row["probe_success"] and row["status"] in accepted for row in rows),
            "policy": "required" if require_signed else "record_only",
            "launchers": rows,
        }

    @staticmethod
    def artifact_integrity(metadata_path: str | Path) -> Dict[str, Any]:
        metadata_file = Path(metadata_path)
        payload = json.loads(metadata_file.read_text(encoding="utf-8-sig"))
        failures = []
        for row in payload.get("artifacts") or []:
            path = Path(str(row.get("path") or row.get("absolutePath") or ""))
            if not path.is_absolute():
                path = metadata_file.parent / str(row.get("relativePath") or row.get("file") or "")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != str(row.get("sha256") or ""):
                failures.append(str(path))
        return {"success": not failures and bool(payload.get("artifacts")), "failures": failures}

    @staticmethod
    def _files(root: Path):
        if root.is_file():
            yield root
        elif root.exists():
            yield from (path for path in root.rglob("*") if path.is_file())
