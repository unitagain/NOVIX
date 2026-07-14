"""SQLite WAL control plane for jobs, leases, idempotency and revisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


SCHEMA_VERSION = 3


class RevisionConflict(RuntimeError):
    pass


class SQLiteControlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        deadline = time.monotonic() + 10.0
        delay = 0.02
        while True:
            try:
                self._initialize_once()
                return
            except sqlite3.OperationalError as exc:
                reason = str(exc).lower()
                if not any(marker in reason for marker in ("locked", "busy")) or time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(0.25, delay * 2)

    def _initialize_once(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS migration_state (
                    name TEXT PRIMARY KEY,
                    completed_at REAL NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS idempotency (
                    key_hash TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL NOT NULL DEFAULT 0,
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    result_json TEXT
                    ,deadline_at REAL NOT NULL DEFAULT 0
                    ,side_effect_started INTEGER NOT NULL DEFAULT 0
                    ,side_effect_fingerprint TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON jobs(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_lease
                    ON jobs(status, lease_expires_at);

                CREATE TABLE IF NOT EXISTS revisions (
                    namespace TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(namespace, entity_key)
                );

                CREATE TABLE IF NOT EXISTS project_generations (
                    project_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL DEFAULT 0,
                    active_writers INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, time.time()),
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
            if "request_fingerprint" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''")
            if "deadline_at" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN deadline_at REAL NOT NULL DEFAULT 0")
            if "side_effect_started" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN side_effect_started INTEGER NOT NULL DEFAULT 0")
            if "side_effect_fingerprint" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN side_effect_fingerprint TEXT NOT NULL DEFAULT ''")

    def begin_project_write(self, project_id: str) -> int:
        now = time.time()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO project_generations(project_id, generation, active_writers, updated_at)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    generation = project_generations.generation + 1,
                    active_writers = project_generations.active_writers + 1,
                    updated_at = excluded.updated_at
                """,
                (str(project_id), now),
            )
            row = connection.execute(
                "SELECT generation FROM project_generations WHERE project_id = ?", (str(project_id),)
            ).fetchone()
            return int(row["generation"])

    def end_project_write(self, project_id: str) -> int:
        now = time.time()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT generation, active_writers FROM project_generations WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()
            if row is None or int(row["active_writers"]) <= 0:
                raise RuntimeError(f"project_generation_not_active:{project_id}")
            connection.execute(
                """
                UPDATE project_generations
                SET generation = generation + 1, active_writers = active_writers - 1, updated_at = ?
                WHERE project_id = ?
                """,
                (now, str(project_id)),
            )
            return int(row["generation"]) + 1

    def project_generation(self, project_id: str) -> Dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT generation, active_writers, updated_at FROM project_generations WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()
        if row is None:
            return {"project_id": str(project_id), "generation": 0, "active_writers": 0, "stable": True}
        generation = int(row["generation"])
        active = int(row["active_writers"])
        return {
            "project_id": str(project_id),
            "generation": generation,
            "active_writers": active,
            "stable": active == 0 and generation % 2 == 0,
            "updated_at": float(row["updated_at"]),
        }

    def recover_abandoned_project_writes(self) -> int:
        """Close write epochs left odd by the previous single sidecar process."""

        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT project_id, generation FROM project_generations WHERE active_writers > 0 OR generation % 2 = 1"
            ).fetchall()
            for row in rows:
                generation = int(row["generation"])
                connection.execute(
                    """
                    UPDATE project_generations
                    SET generation = ?, active_writers = 0, updated_at = ? WHERE project_id = ?
                    """,
                    (generation + 1 if generation % 2 else generation, time.time(), str(row["project_id"])),
                )
            return len(rows)

    def project_revisions(self, project_id: str) -> list[Dict[str, Any]]:
        prefix = f"{project_id}/%"
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT namespace, entity_key, revision, fingerprint, updated_at
                FROM revisions WHERE entity_key LIKE ? ORDER BY namespace, entity_key
                """,
                (prefix,),
            ).fetchall()
        return [dict(row) for row in rows]

    def import_project_checkpoint(
        self,
        source_project_id: str,
        target_project_id: str,
        *,
        generation: int,
        revisions: list[Dict[str, Any]],
    ) -> None:
        source_prefix = f"{source_project_id}/"
        target_prefix = f"{target_project_id}/"
        with self.transaction() as connection:
            connection.execute("DELETE FROM revisions WHERE entity_key LIKE ?", (f"{target_project_id}/%",))
            for row in revisions:
                entity_key = str(row.get("entity_key") or "")
                if not entity_key.startswith(source_prefix):
                    raise ValueError("control_checkpoint_revision_scope_mismatch")
                connection.execute(
                    """
                    INSERT INTO revisions(namespace, entity_key, revision, fingerprint, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("namespace") or ""),
                        target_prefix + entity_key[len(source_prefix) :],
                        int(row.get("revision") or 0),
                        str(row.get("fingerprint") or ""),
                        float(row.get("updated_at") or time.time()),
                    ),
                )
            stable_generation = max(0, int(generation))
            if stable_generation % 2:
                stable_generation += 1
            connection.execute(
                """
                INSERT INTO project_generations(project_id, generation, active_writers, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    generation = excluded.generation, active_writers = 0, updated_at = excluded.updated_at
                """,
                (str(target_project_id), stable_generation, time.time()),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def migration_completed(self, name: str) -> bool:
        with self.connection() as connection:
            return connection.execute("SELECT 1 FROM migration_state WHERE name = ?", (name,)).fetchone() is not None

    def mark_migration_completed(self, name: str, details: Dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO migration_state(name, completed_at, details_json) VALUES (?, ?, ?)",
                (name, time.time(), _json(details)),
            )

    def health(self) -> Dict[str, Any]:
        with self.connection() as connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            schema_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            return {
                "ok": quick_check == "ok",
                "quick_check": quick_check,
                "journal_mode": journal_mode,
                "schema_version": int(schema_version or 0),
            }

    def begin_idempotency(self, key: str, request_fingerprint: str) -> Dict[str, Any]:
        key_hash = _key_hash(key)
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM idempotency WHERE key_hash = ?", (key_hash,)).fetchone()
            if existing:
                record = _idempotency_row(existing)
                if record["request_fingerprint"] != request_fingerprint:
                    raise ValueError("idempotency_key_payload_mismatch")
                if record["status"] in {"running", "completed"}:
                    return {**record, "_existing": True}
            now = time.time()
            connection.execute(
                """
                INSERT INTO idempotency(key_hash, request_fingerprint, status, updated_at)
                VALUES (?, ?, 'running', ?)
                ON CONFLICT(key_hash) DO UPDATE SET
                    request_fingerprint=excluded.request_fingerprint,
                    status='running', provider=NULL, model=NULL, usage_json='{}', reason=NULL,
                    updated_at=excluded.updated_at
                """,
                (key_hash, request_fingerprint, now),
            )
            return {
                "key_hash": key_hash,
                "request_fingerprint": request_fingerprint,
                "status": "running",
                "updated_at": now,
                "_existing": False,
            }

    def complete_idempotency(self, key: str, request_fingerprint: str, response: Dict[str, Any]) -> None:
        self._set_idempotency(
            key,
            request_fingerprint,
            status="completed",
            provider=response.get("provider"),
            model=response.get("model"),
            usage=response.get("usage") or {},
            reason=None,
        )

    def fail_idempotency(self, key: str, request_fingerprint: str, reason: str) -> None:
        self._set_idempotency(
            key,
            request_fingerprint,
            status="failed",
            provider=None,
            model=None,
            usage={},
            reason=str(reason)[:300],
        )

    def _set_idempotency(
        self,
        key: str,
        request_fingerprint: str,
        *,
        status: str,
        provider: Any,
        model: Any,
        usage: Dict[str, Any],
        reason: Optional[str],
    ) -> None:
        key_hash = _key_hash(key)
        with self.transaction() as connection:
            existing = connection.execute("SELECT request_fingerprint FROM idempotency WHERE key_hash = ?", (key_hash,)).fetchone()
            if existing and existing["request_fingerprint"] != request_fingerprint:
                raise ValueError("idempotency_key_payload_mismatch")
            connection.execute(
                """
                INSERT INTO idempotency(
                    key_hash, request_fingerprint, status, provider, model, usage_json, reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_hash) DO UPDATE SET
                    request_fingerprint=excluded.request_fingerprint,
                    status=excluded.status, provider=excluded.provider, model=excluded.model,
                    usage_json=excluded.usage_json, reason=excluded.reason, updated_at=excluded.updated_at
                """,
                (key_hash, request_fingerprint, status, provider, model, _json(usage), reason, time.time()),
            )

    def import_legacy_idempotency(self, root: Path) -> int:
        marker = f"legacy-idempotency:{Path(root).resolve()}"
        if self.migration_completed(marker):
            return 0
        imported = 0
        for path in Path(root).glob("*.json") if Path(root).exists() else ():
            try:
                row = dict(json.loads(path.read_text(encoding="utf-8")) or {})
                key_hash = str(row.get("key_hash") or path.stem)
                fingerprint = str(row.get("request_fingerprint") or "")
                if not key_hash or not fingerprint:
                    continue
                with self.transaction() as connection:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO idempotency(
                            key_hash, request_fingerprint, status, provider, model, usage_json, reason, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            key_hash,
                            fingerprint,
                            str(row.get("status") or "failed"),
                            row.get("provider"),
                            row.get("model"),
                            _json(row.get("usage") or {}),
                            row.get("reason"),
                            float(row.get("updated_at") or time.time()),
                        ),
                    )
                    imported += int(cursor.rowcount > 0)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        self.mark_migration_completed(marker, {"imported": imported, "source": str(root)})
        return imported

    def enqueue_job(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        idempotency_key: str,
        max_attempts: int,
        request_fingerprint: str,
        deadline_at: float,
    ) -> Dict[str, Any]:
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if existing and str(existing["request_fingerprint"] or "") not in {"", str(request_fingerprint)}:
                raise ValueError("job_idempotency_key_payload_mismatch")
            if existing and existing["status"] not in {"failed", "dead_letter", "cancelled"}:
                return _job_row(existing)
            now = time.time()
            if existing:
                connection.execute(
                    """
                    UPDATE jobs SET kind=?, payload_json=?, request_fingerprint=?, status='queued', attempt=0, max_attempts=?,
                        updated_at=?, available_at=?, lease_owner='', lease_expires_at=0,
                        lease_generation=lease_generation+1, cancel_requested=0, last_error='', result_json=NULL, deadline_at=?
                    WHERE idempotency_key=?
                    """,
                    (str(kind), _json(payload), str(request_fingerprint), max(1, int(max_attempts)), now, now, float(deadline_at), idempotency_key),
                )
                job_id = existing["id"]
            else:
                job_id = f"job_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, kind, payload_json, idempotency_key, request_fingerprint, status, attempt, max_attempts,
                        created_at, updated_at, available_at, deadline_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)
                    """,
                    (job_id, str(kind), _json(payload), str(idempotency_key), str(request_fingerprint), max(1, int(max_attempts)), now, now, now, float(deadline_at)),
                )
            return _job_row(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def claim_job(self, worker_id: str, *, lease_seconds: float) -> Optional[Dict[str, Any]]:
        with self.transaction() as connection:
            self._recover_expired(connection)
            now = time.time()
            candidate = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status IN ('queued', 'retry') AND available_at <= ? AND cancel_requested = 0
                    AND (deadline_at = 0 OR deadline_at > ?)
                ORDER BY available_at, created_at LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if not candidate:
                return None
            connection.execute(
                """
                UPDATE jobs SET status='running', attempt=attempt+1, lease_owner=?,
                    lease_expires_at=?, lease_generation=lease_generation+1, updated_at=?
                WHERE id=?
                """,
                (worker_id, now + max(0.5, float(lease_seconds)), now, candidate["id"]),
            )
            return _job_row(connection.execute("SELECT * FROM jobs WHERE id = ?", (candidate["id"],)).fetchone())

    def heartbeat_job(self, job_id: str, worker_id: str, generation: int, *, lease_seconds: float) -> bool:
        with self.transaction() as connection:
            now = time.time()
            cursor = connection.execute(
                """
                UPDATE jobs SET lease_expires_at=?, updated_at=?
                WHERE id=? AND status='running' AND lease_owner=? AND lease_generation=? AND cancel_requested=0
                """,
                (now + max(0.5, float(lease_seconds)), now, job_id, worker_id, int(generation)),
            )
            return cursor.rowcount == 1

    def finish_job(
        self,
        job_id: str,
        worker_id: str,
        generation: int,
        *,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
        retry_delay: float = 0.0,
    ) -> bool:
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE id=? AND status='running' AND lease_owner=? AND lease_generation=?",
                (job_id, worker_id, int(generation)),
            ).fetchone()
            if not job:
                return False
            now = time.time()
            final_status = status
            if status == "failed":
                final_status = "dead_letter" if int(job["attempt"]) >= int(job["max_attempts"]) else "retry"
            connection.execute(
                """
                UPDATE jobs SET status=?, result_json=?, last_error=?, available_at=?, lease_owner='',
                    lease_expires_at=0, updated_at=? WHERE id=? AND lease_generation=?
                """,
                (
                    final_status,
                    _json(result) if result is not None else None,
                    str(error)[:1000],
                    now + max(0.0, float(retry_delay)) if final_status == "retry" else now,
                    now,
                    job_id,
                    int(generation),
                ),
            )
            return True

    def request_job_cancel(self, job_id: str) -> bool:
        with self.transaction() as connection:
            job = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] in {"completed", "dead_letter", "cancelled"}:
                return False
            now = time.time()
            status = "cancelled" if job["status"] in {"queued", "retry"} else job["status"]
            connection.execute(
                "UPDATE jobs SET cancel_requested=1, status=?, updated_at=? WHERE id=?",
                (status, now, job_id),
            )
            return True

    def job_cancel_requested(self, job_id: str, worker_id: str, generation: int) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE id=? AND lease_owner=? AND lease_generation=?",
                (job_id, worker_id, int(generation)),
            ).fetchone()
            return bool(row and row["cancel_requested"])

    def mark_job_side_effect(self, job_id: str, worker_id: str, generation: int, fingerprint: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET side_effect_started=1, side_effect_fingerprint=?, updated_at=?
                WHERE id=? AND status='running' AND lease_owner=? AND lease_generation=?
                """,
                (str(fingerprint), time.time(), job_id, worker_id, int(generation)),
            )
            return cursor.rowcount == 1

    def recover_expired_jobs(self) -> int:
        with self.transaction() as connection:
            return self._recover_expired(connection)

    @staticmethod
    def _recover_expired(connection: sqlite3.Connection) -> int:
        now = time.time()
        connection.execute(
            """
            UPDATE jobs SET status='dead_letter', last_error='job_deadline_exceeded', updated_at=?
            WHERE status IN ('queued', 'retry') AND deadline_at > 0 AND deadline_at <= ?
            """,
            (now, now),
        )
        rows = connection.execute(
            "SELECT id, attempt, max_attempts, cancel_requested, side_effect_started FROM jobs WHERE status='running' AND lease_expires_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            if row["cancel_requested"]:
                status = "cancelled_uncertain" if row["side_effect_started"] else "cancelled"
            else:
                status = "dead_letter" if int(row["attempt"]) >= int(row["max_attempts"]) else "retry"
            connection.execute(
                """
                UPDATE jobs SET status=?, lease_owner='', lease_expires_at=0, available_at=?,
                    lease_generation=lease_generation+1, last_error='lease_expired', updated_at=? WHERE id=?
                """,
                (status, now, now, row["id"]),
            )
        return len(rows)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return _job_row(row) if row else None

    def all_jobs(self) -> list[Dict[str, Any]]:
        with self.connection() as connection:
            return [_job_row(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created_at")]

    def job_stats(self) -> Dict[str, int]:
        with self.connection() as connection:
            return {str(row["status"]): int(row["count"]) for row in connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")}

    def job_health(self) -> Dict[str, Any]:
        now = time.time()
        with self.connection() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
            oldest = connection.execute(
                "SELECT MIN(created_at) AS oldest FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()["oldest"]
        result: Dict[str, Any] = {str(row["status"]): int(row["count"]) for row in rows}
        result["oldest_queued_age_seconds"] = max(0.0, now - float(oldest)) if oldest else 0.0
        return result

    def upsert_job(self, job: Dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id, kind, payload_json, idempotency_key, status, attempt, max_attempts,
                    created_at, updated_at, available_at, lease_owner, lease_expires_at,
                    lease_generation, cancel_requested, last_error, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind, payload_json=excluded.payload_json, idempotency_key=excluded.idempotency_key,
                    status=excluded.status, attempt=excluded.attempt, max_attempts=excluded.max_attempts,
                    updated_at=excluded.updated_at, available_at=excluded.available_at,
                    lease_owner=excluded.lease_owner, lease_expires_at=excluded.lease_expires_at,
                    lease_generation=excluded.lease_generation, cancel_requested=excluded.cancel_requested,
                    last_error=excluded.last_error, result_json=excluded.result_json
                """,
                _job_values(job),
            )

    def import_legacy_jobs(self, root: Path) -> int:
        marker = f"legacy-jobs:{Path(root).resolve()}"
        if self.migration_completed(marker):
            return 0
        imported = 0
        jobs_dir = Path(root) / "jobs"
        for path in jobs_dir.glob("job_*.json") if jobs_dir.exists() else ():
            try:
                job = dict(json.loads(path.read_text(encoding="utf-8")) or {})
                if not job.get("id") or not job.get("idempotency_key"):
                    continue
                with self.transaction() as connection:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO jobs(
                            id, kind, payload_json, idempotency_key, status, attempt, max_attempts,
                            created_at, updated_at, available_at, lease_owner, lease_expires_at,
                            lease_generation, cancel_requested, last_error, result_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        _job_values(job),
                    )
                    imported += int(cursor.rowcount > 0)
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.IntegrityError):
                continue
        self.mark_migration_completed(marker, {"imported": imported, "source": str(root)})
        return imported

    def get_revision(self, namespace: str, entity_key: str) -> Dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT revision, fingerprint, updated_at FROM revisions WHERE namespace=? AND entity_key=?",
                (namespace, entity_key),
            ).fetchone()
            if not row:
                return {"revision": 0, "fingerprint": "", "updated_at": 0.0}
            return dict(row)

    def bump_revision(
        self,
        namespace: str,
        entity_key: str,
        *,
        expected_revision: Optional[int],
        fingerprint: str,
    ) -> int:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT revision FROM revisions WHERE namespace=? AND entity_key=?",
                (namespace, entity_key),
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision is not None and current != int(expected_revision):
                raise RevisionConflict(f"revision_conflict:{current}!={int(expected_revision)}")
            next_revision = current + 1
            connection.execute(
                """
                INSERT INTO revisions(namespace, entity_key, revision, fingerprint, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, entity_key) DO UPDATE SET
                    revision=excluded.revision, fingerprint=excluded.fingerprint, updated_at=excluded.updated_at
                """,
                (namespace, entity_key, next_revision, fingerprint, time.time()),
            )
            return next_revision


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _key_hash(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()


def _idempotency_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "key_hash": row["key_hash"],
        "request_fingerprint": row["request_fingerprint"],
        "status": row["status"],
        "provider": row["provider"],
        "model": row["model"],
        "usage": json.loads(row["usage_json"] or "{}"),
        "reason": row["reason"],
        "updated_at": row["updated_at"],
    }


def _job_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "idempotency_key": row["idempotency_key"],
        "request_fingerprint": row["request_fingerprint"],
        "status": row["status"],
        "attempt": int(row["attempt"]),
        "max_attempts": int(row["max_attempts"]),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "available_at": float(row["available_at"]),
        "lease_owner": row["lease_owner"],
        "lease_expires_at": float(row["lease_expires_at"]),
        "lease_generation": int(row["lease_generation"]),
        "cancel_requested": bool(row["cancel_requested"]),
        "last_error": row["last_error"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "deadline_at": float(row["deadline_at"]),
        "side_effect_started": bool(row["side_effect_started"]),
        "side_effect_fingerprint": row["side_effect_fingerprint"],
    }


def _job_values(job: Dict[str, Any]) -> tuple[Any, ...]:
    now = time.time()
    return (
        str(job["id"]),
        str(job.get("kind") or ""),
        _json(job.get("payload") or {}),
        str(job.get("idempotency_key") or job["id"]),
        str(job.get("status") or "queued"),
        int(job.get("attempt") or 0),
        max(1, int(job.get("max_attempts") or 1)),
        float(job.get("created_at") or now),
        float(job.get("updated_at") or now),
        float(job.get("available_at") or now),
        str(job.get("lease_owner") or ""),
        float(job.get("lease_expires_at") or 0.0),
        int(job.get("lease_generation") or 0),
        int(bool(job.get("cancel_requested"))),
        str(job.get("last_error") or "")[:1000],
        _json(job.get("result")) if job.get("result") is not None else None,
    )
