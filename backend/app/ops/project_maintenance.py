"""Project backup, verified restore, schema migration and corruption scanning."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from app.control_plane.store import SCHEMA_VERSION as CONTROL_SCHEMA_VERSION
from app.control_plane.store import SQLiteControlStore
from app.error_contract import safe_error_code


PROJECT_SCHEMA_VERSION = 1
BACKUP_SCHEMA_VERSION = 2


class ProjectMaintenanceService:
    def __init__(self, data_dir: str | Path, *, failpoint: Optional[Callable[[str], None]] = None):
        self.data_dir = Path(data_dir).resolve()
        self.failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self.failpoint is not None:
            self.failpoint(name)

    def backup(self, project_id: str, destination: str | Path) -> Dict[str, Any]:
        project = self._project(project_id)
        if not project.exists():
            raise FileNotFoundError("project_not_found")
        destination = Path(destination).resolve()
        if destination == project or project in destination.parents:
            raise ValueError("backup_destination_inside_project")
        destination.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteControlStore(self.data_dir / "_system" / "control.sqlite3")
        staging_root = self.data_dir / "_backup_staging" / f"backup_{uuid.uuid4().hex}"
        temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
        try:
            for attempt in range(5):
                if staging_root.exists():
                    shutil.rmtree(staging_root, ignore_errors=True)
                staging_project = staging_root / "project"
                before = store.project_generation(project_id)
                if not before["stable"]:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                try:
                    shutil.copytree(project, staging_project, copy_function=shutil.copy2)
                    checkpoint = staging_root / "control" / "control.sqlite3"
                    self._create_control_checkpoint(store, project_id, before["generation"], checkpoint)
                except (FileNotFoundError, PermissionError, OSError):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                after = store.project_generation(project_id)
                if not after["stable"] or after["generation"] != before["generation"]:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                files = self._file_manifest(staging_project)
                control_sha256 = self._sha256_file(checkpoint)
                manifest = {
                    "backup_schema_version": BACKUP_SCHEMA_VERSION,
                    "schema_version": PROJECT_SCHEMA_VERSION,
                    "control_schema_version": CONTROL_SCHEMA_VERSION,
                    "project_id": project_id,
                    "generation": before["generation"],
                    "created_at": time.time(),
                    "files": files,
                    "project_fingerprint": self._manifest_fingerprint(files),
                    "control_checkpoint": {
                        "path": "control/control.sqlite3",
                        "sha256": control_sha256,
                    },
                }
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    for row in files:
                        archive.write(staging_project / row["path"], arcname=f"project/{row['path']}")
                    archive.write(checkpoint, arcname="control/control.sqlite3")
                    archive.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                os.replace(temporary, destination)
                return {"success": True, "path": str(destination), **manifest}
            raise RuntimeError("consistent_backup_generation_unavailable")
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def restore(self, backup_path: str | Path, *, project_id: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
        backup = Path(backup_path).resolve()
        if not backup.exists():
            raise FileNotFoundError("backup_not_found")
        staging_root = self.data_dir / "_restore_staging" / f"restore_{uuid.uuid4().hex}"
        staging_project = staging_root / "project"
        rollback = None
        try:
            with zipfile.ZipFile(backup, "r") as archive:
                manifest = json.loads(archive.read("backup_manifest.json"))
                self._safe_extract(archive, staging_root)
            if int(manifest.get("backup_schema_version") or 0) != BACKUP_SCHEMA_VERSION:
                raise ValueError("unsupported_backup_schema")
            target_id = str(project_id or manifest.get("project_id") or "").strip()
            if not target_id:
                raise ValueError("missing_project_id")
            target = self._project(target_id)
            verification = self.verify_manifest(staging_project, manifest)
            if not verification["valid"]:
                raise ValueError("backup_verification_failed")
            checkpoint_path = staging_root / str((manifest.get("control_checkpoint") or {}).get("path") or "")
            checkpoint = self._read_control_checkpoint(checkpoint_path, manifest)
            smoke = self._scan_project(staging_project, target_id)
            if not smoke["valid"]:
                raise ValueError("backup_restore_smoke_failed")
            if target.exists() and not overwrite:
                raise FileExistsError("restore_target_exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                rollback = self.data_dir / "_restore_staging" / f"rollback_{target_id}_{uuid.uuid4().hex}"
                os.replace(target, rollback)
            journal = staging_root / "operation.json"
            self._write_operation_journal(
                journal,
                {"operation": "restore", "target": str(target), "rollback": str(rollback or ""), "stage": "prepared"},
            )
            try:
                self._hit("restore_before_switch")
                os.replace(staging_project, target)
                self._write_operation_journal(
                    journal,
                    {"operation": "restore", "target": str(target), "rollback": str(rollback or ""), "stage": "switched"},
                )
                self._hit("restore_after_switch")
                SQLiteControlStore(self.data_dir / "_system" / "control.sqlite3").import_project_checkpoint(
                    str(manifest.get("project_id") or ""),
                    target_id,
                    generation=int(checkpoint["generation"]),
                    revisions=list(checkpoint["revisions"]),
                )
                self._hit("restore_after_control_commit")
            except Exception:
                if target.exists() and (rollback is None or rollback.exists()):
                    shutil.rmtree(target, ignore_errors=True)
                if rollback and rollback.exists() and not target.exists():
                    os.replace(rollback, target)
                raise
            if rollback and rollback.exists():
                shutil.rmtree(rollback)
            return {
                "success": True,
                "project_id": target_id,
                "project_fingerprint": verification["project_fingerprint"],
                "files": verification["files"],
                "generation": int(checkpoint["generation"]),
                "control_checkpoint_sha256": self._sha256_file(checkpoint_path),
                "smoke": smoke,
            }
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

    def scan(self, project_id: str) -> Dict[str, Any]:
        project = self._project(project_id)
        return self._scan_project(project, project_id)

    def _scan_project(self, project: Path, project_id: str) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []
        if not project.exists():
            return {"valid": False, "issues": [{"type": "project_missing"}], "files": 0}
        files = 0
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            files += 1
            relative = path.relative_to(project).as_posix()
            try:
                if path.suffix == ".json":
                    json.loads(path.read_text(encoding="utf-8-sig"))
                elif path.suffix == ".jsonl":
                    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                        if line.strip():
                            json.loads(line)
                elif path.suffix in {".yaml", ".yml"}:
                    yaml.safe_load(path.read_text(encoding="utf-8-sig"))
                elif path.suffix in {".md", ".txt"}:
                    path.read_text(encoding="utf-8-sig")
            except Exception as exc:
                issues.append({"type": "parse_error", "path": relative, "error": safe_error_code(exc)})
        issues.extend(self._scan_compact_references(project))
        return {"valid": not issues, "issues": issues, "files": files, "project_id": project_id}

    def migrate(self, project_id: str, *, target_version: int = PROJECT_SCHEMA_VERSION) -> Dict[str, Any]:
        project = self._project(project_id)
        if not project.exists():
            raise FileNotFoundError("project_not_found")
        store = SQLiteControlStore(self.data_dir / "_system" / "control.sqlite3")
        store.begin_project_write(project_id)
        staging_root = self.data_dir / "_migration_staging" / f"migration_{project_id}_{uuid.uuid4().hex}"
        staging_project = staging_root / "project"
        rollback = staging_root / "rollback"
        try:
            shutil.copytree(project, staging_project, copy_function=shutil.copy2)
            schema_path = staging_project / ".wenshape_schema.json"
            current = 0
            if schema_path.exists():
                current = int(json.loads(schema_path.read_text(encoding="utf-8")).get("version") or 0)
            if target_version < current or target_version > PROJECT_SCHEMA_VERSION:
                raise ValueError("unsupported_schema_target")
            if target_version == current:
                return {"success": True, "project_id": project_id, "version": current, "applied": []}
            migrations: Dict[int, Callable[[Path], None]] = {1: self._migrate_v1}
            applied = []
            while current < target_version:
                next_version = current + 1
                migrations[next_version](staging_project)
                current = next_version
                applied.append(current)
            temporary = schema_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"version": current, "updated_at": time.time()}, indent=2), encoding="utf-8"
            )
            os.replace(temporary, schema_path)
            smoke = self._scan_project(staging_project, project_id)
            if not smoke["valid"]:
                raise ValueError("migration_smoke_failed")
            journal = staging_root / "operation.json"
            self._write_operation_journal(
                journal,
                {"operation": "migration", "target": str(project), "rollback": str(rollback), "stage": "prepared"},
            )
            self._hit("migration_before_switch")
            os.replace(project, rollback)
            os.replace(staging_project, project)
            self._write_operation_journal(
                journal,
                {"operation": "migration", "target": str(project), "rollback": str(rollback), "stage": "switched"},
            )
            self._hit("migration_after_switch")
            shutil.rmtree(rollback, ignore_errors=True)
            return {"success": True, "project_id": project_id, "version": current, "applied": applied}
        except Exception:
            if rollback.exists():
                if project.exists():
                    shutil.rmtree(project, ignore_errors=True)
                os.replace(rollback, project)
            raise
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
            store.end_project_write(project_id)

    def recover_interrupted_operations(self) -> Dict[str, Any]:
        recovered: List[Dict[str, str]] = []
        for root_name in ("_restore_staging", "_migration_staging"):
            root = self.data_dir / root_name
            if not root.exists():
                continue
            for journal in root.glob("*/operation.json"):
                try:
                    row = json.loads(journal.read_text(encoding="utf-8"))
                    target = Path(str(row.get("target") or ""))
                    rollback = Path(str(row.get("rollback") or "")) if row.get("rollback") else None
                    stage = str(row.get("stage") or "")
                    if stage == "switched" and rollback and rollback.exists():
                        if target.exists():
                            shutil.rmtree(target, ignore_errors=True)
                        os.replace(rollback, target)
                        recovered.append({"operation": str(row.get("operation") or ""), "action": "rollback"})
                    elif stage == "prepared" and rollback and rollback.exists() and not target.exists():
                        os.replace(rollback, target)
                        recovered.append({"operation": str(row.get("operation") or ""), "action": "restore_target"})
                finally:
                    shutil.rmtree(journal.parent, ignore_errors=True)
        repaired_generations = SQLiteControlStore(
            self.data_dir / "_system" / "control.sqlite3"
        ).recover_abandoned_project_writes()
        return {"success": True, "recovered": recovered, "repaired_generations": repaired_generations}

    @staticmethod
    def _write_operation_journal(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def verify_manifest(self, project: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
        if int(manifest.get("backup_schema_version") or 0) != BACKUP_SCHEMA_VERSION:
            return {"valid": False, "files": 0, "project_fingerprint": "", "reason": "schema_mismatch"}
        actual = self._file_manifest(project)
        expected = list(manifest.get("files") or [])
        valid = actual == expected and self._manifest_fingerprint(actual) == manifest.get("project_fingerprint")
        return {
            "valid": valid,
            "files": len(actual),
            "project_fingerprint": self._manifest_fingerprint(actual),
        }

    def _project(self, project_id: str) -> Path:
        safe = str(project_id or "").strip()
        if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
            raise ValueError("invalid_project_id")
        return self.data_dir / safe

    @staticmethod
    def _file_manifest(project: Path) -> List[Dict[str, Any]]:
        rows = []
        for path in sorted(item for item in project.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise ValueError("project_symlink_not_supported")
            data = path.read_bytes()
            rows.append(
                {
                    "path": path.relative_to(project).as_posix(),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return rows

    @staticmethod
    def _manifest_fingerprint(files: List[Dict[str, Any]]) -> str:
        payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _create_control_checkpoint(
        store: SQLiteControlStore,
        project_id: str,
        generation: int,
        path: Path,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE revisions(
                    namespace TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(namespace, entity_key)
                );
                """
            )
            metadata = {
                "project_id": str(project_id),
                "generation": str(int(generation)),
                "control_schema_version": str(CONTROL_SCHEMA_VERSION),
            }
            connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
            connection.executemany(
                "INSERT INTO revisions(namespace, entity_key, revision, fingerprint, updated_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["namespace"],
                        row["entity_key"],
                        int(row["revision"]),
                        row["fingerprint"],
                        float(row["updated_at"]),
                    )
                    for row in store.project_revisions(project_id)
                ],
            )
            connection.commit()
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise RuntimeError("control_checkpoint_quick_check_failed")
        finally:
            connection.close()

    def _read_control_checkpoint(self, path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
        expected = str((manifest.get("control_checkpoint") or {}).get("sha256") or "")
        if not path.is_file() or not expected or self._sha256_file(path) != expected:
            raise ValueError("control_checkpoint_hash_mismatch")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise ValueError("control_checkpoint_corrupt")
            metadata = {str(row["key"]): str(row["value"]) for row in connection.execute("SELECT key, value FROM metadata")}
            revisions = [dict(row) for row in connection.execute("SELECT * FROM revisions ORDER BY namespace, entity_key")]
        finally:
            connection.close()
        if metadata.get("project_id") != str(manifest.get("project_id") or ""):
            raise ValueError("control_checkpoint_project_mismatch")
        if int(metadata["generation"]) != int(manifest.get("generation", -2)):
            raise ValueError("control_checkpoint_generation_mismatch")
        return {"generation": int(metadata["generation"]), "revisions": revisions}

    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        for member in archive.infolist():
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("backup_symlink_not_supported")
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError("unsafe_backup_path")
            archive.extract(member, destination)

    @staticmethod
    def _migrate_v1(project: Path) -> None:
        (project / "sessions" / "compact").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _scan_compact_references(project: Path) -> List[Dict[str, Any]]:
        issues = []
        event_path = project / "sessions" / "conversation.events.jsonl"
        event_ids = set()
        if event_path.exists():
            for line in event_path.read_text(encoding="utf-8-sig").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event_id"):
                    event_ids.add(str(row["event_id"]))
        compact_dir = project / "sessions" / "compact"
        if compact_dir.exists():
            for path in compact_dir.glob("compact_epoch_*.json"):
                try:
                    artifact = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                missing = [str(ref) for ref in artifact.get("recovery_refs") or [] if str(ref) not in event_ids]
                if missing:
                    issues.append(
                        {"type": "missing_compact_recovery_refs", "path": path.name, "count": len(missing)}
                    )
        return issues
