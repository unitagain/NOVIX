"""Single-host durable task queue backed by the SQLite control plane."""

from __future__ import annotations

import asyncio
import logging
import hashlib
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from app.control_plane.store import SQLiteControlStore
from app.error_contract import normalize_error_code, safe_error_code


logger = logging.getLogger(__name__)


@dataclass
class TaskExecutionContext:
    job_id: str
    idempotency_key: str
    attempt: int
    lease_generation: int
    llm_call_index: int = field(default=0)
    queue: Any = field(default=None, repr=False)
    worker_id: str = ""
    deadline_at: float = 0.0

    def next_llm_idempotency_key(self, request_fingerprint: str) -> str:
        self.llm_call_index += 1
        return f"durable-job:{self.job_id}:{request_fingerprint}"

    async def mark_external_side_effect(self, request_fingerprint: str) -> None:
        if self.queue is None:
            return
        ok = await self.queue.mark_side_effect(
            self.job_id,
            self.worker_id,
            generation=self.lease_generation,
            fingerprint=request_fingerprint,
        )
        if not ok:
            raise RuntimeError("stale_job_lease")

    def bounded_timeout(self, requested_seconds: float) -> float:
        if self.deadline_at <= 0:
            return max(0.001, float(requested_seconds))
        remaining = self.deadline_at - time.time()
        if remaining <= 0:
            raise TimeoutError("job_deadline_exceeded")
        return min(max(0.001, float(requested_seconds)), remaining)


_current_execution: ContextVar[Optional[TaskExecutionContext]] = ContextVar(
    "wenshape_durable_task_execution",
    default=None,
)


def current_task_execution() -> Optional[TaskExecutionContext]:
    return _current_execution.get()


class DurableTaskQueue:
    def __init__(self, root: Path, *, store: Optional[SQLiteControlStore] = None):
        self.root = Path(root)
        self.store = store or SQLiteControlStore(self.root / "control.sqlite3")
        self.store.import_legacy_jobs(self.root)

    async def enqueue(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        idempotency_key: str,
        max_attempts: int = 3,
        timeout_seconds: float = 300.0,
    ) -> Dict[str, Any]:
        request_fingerprint = hashlib.sha256(
            json.dumps({"kind": str(kind), "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        deadline_at = time.time() + max(0.001, float(timeout_seconds))
        job = await asyncio.to_thread(
            self.store.enqueue_job,
            kind,
            payload,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            request_fingerprint=request_fingerprint,
            deadline_at=deadline_at,
        )
        self._metric("queued")
        return job

    async def claim(self, worker_id: str, *, lease_seconds: float = 60.0) -> Optional[Dict[str, Any]]:
        job = await asyncio.to_thread(self.store.claim_job, worker_id, lease_seconds=lease_seconds)
        if job:
            self._metric("claimed")
        return job

    async def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        generation: Optional[int] = None,
        lease_seconds: float = 60.0,
    ) -> bool:
        resolved = self._generation(job_id, generation)
        if resolved is None:
            return False
        ok = await asyncio.to_thread(
            self.store.heartbeat_job,
            job_id,
            worker_id,
            resolved,
            lease_seconds=lease_seconds,
        )
        if ok:
            self._metric("heartbeat")
        return ok

    async def complete(
        self,
        job_id: str,
        worker_id: str,
        result: Dict[str, Any],
        *,
        generation: Optional[int] = None,
    ) -> bool:
        resolved = self._generation(job_id, generation)
        if resolved is None:
            return False
        ok = await asyncio.to_thread(
            self.store.finish_job,
            job_id,
            worker_id,
            resolved,
            status="completed",
            result=result,
        )
        if ok:
            self._metric("completed")
        return ok

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        error: BaseException | str,
        *,
        generation: Optional[int] = None,
        retry_delay: float = 5.0,
    ) -> bool:
        resolved = self._generation(job_id, generation)
        if resolved is None:
            return False
        public_error = safe_error_code(error) if isinstance(error, BaseException) else normalize_error_code(error)
        ok = await asyncio.to_thread(
            self.store.finish_job,
            job_id,
            worker_id,
            resolved,
            status="failed",
            error=public_error,
            retry_delay=retry_delay,
        )
        if ok:
            job = self.get(job_id) or {}
            self._metric(str(job.get("status") or "failed"))
        return ok

    async def cancel_running(
        self,
        job_id: str,
        worker_id: str,
        *,
        generation: int,
    ) -> bool:
        ok = await asyncio.to_thread(
            self.store.finish_job,
            job_id,
            worker_id,
            generation,
            status="cancelled",
            error="cancelled",
        )
        if ok:
            self._metric("cancelled")
        return ok

    async def request_cancel(self, job_id: str) -> bool:
        return await asyncio.to_thread(self.store.request_job_cancel, job_id)

    async def cancellation_requested(self, job_id: str, worker_id: str, generation: int) -> bool:
        return await asyncio.to_thread(self.store.job_cancel_requested, job_id, worker_id, generation)

    async def mark_side_effect(self, job_id: str, worker_id: str, *, generation: int, fingerprint: str) -> bool:
        return await asyncio.to_thread(
            self.store.mark_job_side_effect,
            job_id,
            worker_id,
            generation,
            fingerprint,
        )

    async def recover_expired(self) -> int:
        recovered = await asyncio.to_thread(self.store.recover_expired_jobs)
        if recovered:
            self._metric("lease_recovered", recovered)
        return recovered

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_job(job_id)

    def stats(self) -> Dict[str, int]:
        return self.store.job_stats()

    def health(self) -> Dict[str, Any]:
        return self.store.job_health()

    def _generation(self, job_id: str, generation: Optional[int]) -> Optional[int]:
        if generation is not None:
            return int(generation)
        job = self.get(job_id)
        return int(job["lease_generation"]) if job else None

    # Compatibility helpers retained for existing fault-injection tests and legacy import tooling.
    def _all(self) -> list[Dict[str, Any]]:
        return self.store.all_jobs()

    def _write(self, job: Dict[str, Any]) -> None:
        self.store.upsert_job(job)

    def _recover_expired_unlocked(self) -> int:
        return self.store.recover_expired_jobs()

    @staticmethod
    def _metric(status: str, value: float = 1.0) -> None:
        try:
            from app.observability.runtime_metrics import runtime_metrics

            runtime_metrics.increment(f"jobs.{status}", value)
        except (ImportError, RuntimeError) as exc:
            # Metrics are best-effort; the queue operation itself must remain authoritative.
            logger.debug("Queue metric dropped: %s", safe_error_code(exc))


class DurableTaskWorker:
    def __init__(
        self,
        queue: DurableTaskQueue,
        handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]],
        *,
        worker_id: Optional[str] = None,
        poll_seconds: float = 0.5,
        lease_seconds: float = 60.0,
        shutdown_timeout: float = 10.0,
        retry_delay: float = 5.0,
    ):
        self.queue = queue
        self.handlers = handlers
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex}"
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.lease_seconds = max(0.5, float(lease_seconds))
        self.shutdown_timeout = max(1.0, float(shutdown_timeout))
        self.retry_delay = max(0.0, float(retry_delay))
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._handler_task: Optional[asyncio.Future[Dict[str, Any]]] = None
        self._active_job: Optional[Dict[str, Any]] = None
        self.last_error = ""

    async def start(self) -> None:
        await self.queue.recover_expired()
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=self.shutdown_timeout)
        except asyncio.TimeoutError:
            if self._handler_task and not self._handler_task.done():
                self._handler_task.cancel()
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds)
                if job:
                    await self._execute(job)
                    self.last_error = ""
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = safe_error_code(exc)
                self.queue._metric("worker_error")
            if not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    continue

    def snapshot(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "running": bool(self._task and not self._task.done()),
            "active_job": str((self._active_job or {}).get("id") or ""),
            "last_error": self.last_error,
        }

    async def _execute(self, job: Dict[str, Any]) -> None:
        self._active_job = job
        try:
            await self._execute_active(job)
        finally:
            self._handler_task = None
            self._active_job = None

    async def _execute_active(self, job: Dict[str, Any]) -> None:
        generation = int(job["lease_generation"])
        handler = self.handlers.get(str(job.get("kind") or ""))
        if handler is None:
            await self.queue.fail(
                job["id"],
                self.worker_id,
                "handler_not_registered",
                generation=generation,
                retry_delay=0,
            )
            return

        execution = TaskExecutionContext(
            job_id=str(job["id"]),
            idempotency_key=str(job["idempotency_key"]),
            attempt=int(job["attempt"]),
            lease_generation=generation,
            queue=self.queue,
            worker_id=self.worker_id,
            deadline_at=float(job.get("deadline_at") or 0),
        )
        token = _current_execution.set(execution)
        try:
            self._handler_task = asyncio.create_task(self._run_handler(handler, dict(job.get("payload") or {})))
        finally:
            _current_execution.reset(token)
        heartbeat_interval = max(0.1, self.lease_seconds / 3.0)
        while True:
            remaining = float(job.get("deadline_at") or 0) - time.time()
            if float(job.get("deadline_at") or 0) > 0 and remaining <= 0:
                self._handler_task.cancel()
                await asyncio.gather(self._handler_task, return_exceptions=True)
                await self.queue.fail(job["id"], self.worker_id, "job_deadline_exceeded", generation=generation, retry_delay=0)
                return
            wait_timeout = min(heartbeat_interval, remaining) if remaining > 0 else heartbeat_interval
            done, _ = await asyncio.wait({self._handler_task}, timeout=max(0.001, wait_timeout))
            if done:
                break
            if await self.queue.cancellation_requested(job["id"], self.worker_id, generation):
                self._handler_task.cancel()
                await asyncio.gather(self._handler_task, return_exceptions=True)
                latest = self.queue.get(job["id"]) or {}
                if latest.get("side_effect_started"):
                    await asyncio.to_thread(
                        self.queue.store.finish_job,
                        job["id"], self.worker_id, generation,
                        status="cancelled_uncertain", error="cancelled_after_side_effect_started",
                    )
                else:
                    await self.queue.cancel_running(job["id"], self.worker_id, generation=generation)
                return
            renewed = await self.queue.heartbeat(
                job["id"],
                self.worker_id,
                generation=generation,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                self._handler_task.cancel()
                await asyncio.gather(self._handler_task, return_exceptions=True)
                return

        try:
            result = self._handler_task.result()
        except asyncio.CancelledError:
            await self.queue.cancel_running(job["id"], self.worker_id, generation=generation)
        except Exception as exc:
            await self.queue.fail(
                job["id"],
                self.worker_id,
                exc,
                generation=generation,
                retry_delay=self.retry_delay,
            )
        else:
            await self.queue.complete(job["id"], self.worker_id, result, generation=generation)

    @staticmethod
    async def _run_handler(
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await handler(payload)
