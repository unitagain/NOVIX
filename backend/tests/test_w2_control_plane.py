"""W2 SQLite control plane, durable execution and gateway deadline regressions."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
import time
from pathlib import Path

import pytest

from app.control_plane.store import SCHEMA_VERSION, RevisionConflict, SQLiteControlStore
from app.jobs.durable_queue import DurableTaskQueue, DurableTaskWorker
from app.llm_gateway.gateway import LLMGateway
from app.llm_gateway.reliability import (
    GatewayReliabilityController,
    PersistentIdempotencyRegistry,
    RequestDeadline,
)
from app.security.egress_ledger import EgressLedger
from app.storage.drafts import DraftStorage


class SlowProvider:
    def __init__(self, *, delay: float = 0.0, failures: int = 0):
        self.delay = delay
        self.failures = failures
        self.calls = 0
        self.model = "w2-model"

    def get_provider_name(self):
        return "deepseek"

    async def chat(self, messages, **options):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.failures:
            raise TimeoutError("injected_failure")
        return {
            "content": "ok",
            "model": self.model,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class StreamProvider(SlowProvider):
    def __init__(self, delays: list[float]):
        super().__init__()
        self.delays = delays

    async def stream_chat(self, messages, temperature, max_tokens, on_thinking=None):
        self.calls += 1
        for index, delay in enumerate(self.delays):
            await asyncio.sleep(delay)
            yield f"chunk-{index}"


class CloseTrackedStream:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(10)
        return "never"

    async def aclose(self):
        self.closed = True


class CloseTrackedProvider(SlowProvider):
    def __init__(self):
        super().__init__()
        self.stream = CloseTrackedStream()

    def stream_chat(self, messages, temperature, max_tokens, on_thinking=None):
        self.calls += 1
        return self.stream


def _gateway(provider, tmp_path: Path) -> LLMGateway:
    gateway = LLMGateway.__new__(LLMGateway)
    gateway.providers = {"profile": provider}
    gateway.max_retries = 2
    gateway.retry_delays = [0.2, 0.2]
    gateway.max_retry_delay = 0.2
    gateway.request_timeout = 1.0
    gateway.reliability = GatewayReliabilityController(failure_threshold=1, cooldown_seconds=30)
    gateway.idempotency_registry = PersistentIdempotencyRegistry(tmp_path / "idempotency")
    gateway.total_tokens = 0
    gateway.total_requests = 0
    return gateway


def test_control_store_uses_wal_and_tracks_schema(tmp_path: Path):
    store = SQLiteControlStore(tmp_path / "control.sqlite3")
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION


def test_control_plane_write_failure_is_never_silently_ignored(monkeypatch, tmp_path: Path):
    queue = DurableTaskQueue(tmp_path / "queue")

    def disk_full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(queue.store, "enqueue_job", disk_full)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        asyncio.run(queue.enqueue("work", {}, idempotency_key="disk-full"))


def test_legacy_job_and_idempotency_import_is_non_destructive(tmp_path: Path):
    queue_root = tmp_path / "legacy-queue"
    (queue_root / "jobs").mkdir(parents=True)
    legacy_job = {
        "id": "job_legacy",
        "kind": "work",
        "payload": {"value": 1},
        "idempotency_key": "legacy-job",
        "status": "queued",
        "attempt": 0,
        "max_attempts": 2,
        "created_at": time.time(),
        "updated_at": time.time(),
        "available_at": time.time(),
    }
    job_path = queue_root / "jobs" / "job_legacy.json"
    job_path.write_text(json.dumps(legacy_job), encoding="utf-8")
    queue = DurableTaskQueue(queue_root)
    assert queue.get("job_legacy")["payload"] == {"value": 1}
    assert job_path.exists()

    idem_root = tmp_path / "legacy-idempotency"
    idem_root.mkdir()
    key = "legacy-request"
    import hashlib

    key_hash = hashlib.sha256(key.encode()).hexdigest()
    idem_path = idem_root / f"{key_hash}.json"
    idem_path.write_text(
        json.dumps(
            {
                "key_hash": key_hash,
                "request_fingerprint": "fp",
                "status": "completed",
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    registry = PersistentIdempotencyRegistry(idem_root)
    imported = asyncio.run(registry.begin(key, "fp"))
    assert imported["_existing"] is True
    assert imported["status"] == "completed"
    assert idem_path.exists()


def test_idempotency_begin_is_atomic_across_store_instances(tmp_path: Path):
    database = tmp_path / "control.sqlite3"
    first = PersistentIdempotencyRegistry(tmp_path / "legacy-a", store=SQLiteControlStore(database))
    second = PersistentIdempotencyRegistry(tmp_path / "legacy-b", store=SQLiteControlStore(database))

    async def scenario():
        return await asyncio.gather(first.begin("same", "fingerprint"), second.begin("same", "fingerprint"))

    rows = asyncio.run(scenario())
    assert sorted(row["_existing"] for row in rows) == [False, True]


def test_lease_generation_fences_expired_worker(tmp_path: Path):
    queue = DurableTaskQueue(tmp_path / "queue")
    job = asyncio.run(queue.enqueue("work", {}, idempotency_key="fenced", max_attempts=3))
    first = asyncio.run(queue.claim("worker-a", lease_seconds=0.5))
    first["lease_expires_at"] = 0
    queue._write(first)
    assert asyncio.run(queue.recover_expired()) == 1
    second = asyncio.run(queue.claim("worker-b", lease_seconds=0.5))
    assert second["lease_generation"] > first["lease_generation"]
    assert asyncio.run(
        queue.complete(job["id"], "worker-a", {"stale": True}, generation=first["lease_generation"])
    ) is False
    assert asyncio.run(
        queue.complete(job["id"], "worker-b", {"ok": True}, generation=second["lease_generation"])
    ) is True


def test_worker_heartbeats_long_job_and_prevents_duplicate_claim(tmp_path: Path):
    queue = DurableTaskQueue(tmp_path / "queue")
    calls = 0

    async def handler(payload):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1.1)
        return {"ok": True}

    async def scenario():
        job = await queue.enqueue("work", {}, idempotency_key="long", max_attempts=2)
        worker_a = DurableTaskWorker(queue, {"work": handler}, poll_seconds=0.02, lease_seconds=0.5)
        worker_b = DurableTaskWorker(queue, {"work": handler}, poll_seconds=0.02, lease_seconds=0.5)
        await worker_a.start()
        await worker_b.start()
        for _ in range(150):
            if (queue.get(job["id"]) or {}).get("status") == "completed":
                break
            await asyncio.sleep(0.02)
        await worker_a.stop()
        await worker_b.stop()
        return queue.get(job["id"])

    completed = asyncio.run(scenario())
    assert completed["status"] == "completed"
    assert calls == 1


def test_running_job_cancellation_reaches_handler(tmp_path: Path):
    queue = DurableTaskQueue(tmp_path / "queue")
    cancelled = asyncio.Event()

    async def handler(payload):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def scenario():
        job = await queue.enqueue("work", {}, idempotency_key="cancel", max_attempts=2)
        worker = DurableTaskWorker(queue, {"work": handler}, poll_seconds=0.01, lease_seconds=0.5)
        await worker.start()
        for _ in range(100):
            if (queue.get(job["id"]) or {}).get("status") == "running":
                break
            await asyncio.sleep(0.01)
        assert await queue.request_cancel(job["id"])
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        for _ in range(100):
            if (queue.get(job["id"]) or {}).get("status") == "cancelled":
                break
            await asyncio.sleep(0.01)
        await worker.stop()
        return queue.get(job["id"])

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"


def test_worker_clears_active_job_when_handler_task_creation_fails(monkeypatch, tmp_path: Path):
    queue = DurableTaskQueue(tmp_path / "queue")

    async def handler(payload):
        return {"ok": True}

    async def scenario():
        job = await queue.enqueue("work", {}, idempotency_key="task-creation-failure")
        claimed = await queue.claim("worker", lease_seconds=1)
        worker = DurableTaskWorker(queue, {"work": handler}, worker_id="worker")

        def fail_create_task(coro):
            coro.close()
            raise RuntimeError("task_creation_failed")

        monkeypatch.setattr(asyncio, "create_task", fail_create_task)
        with pytest.raises(RuntimeError, match="task_creation_failed"):
            await worker._execute(claimed)
        return worker.snapshot(), queue.get(job["id"])

    snapshot, job = asyncio.run(scenario())
    assert snapshot["active_job"] == ""
    assert job["status"] == "running"


def test_draft_revision_is_idempotent_and_enforces_cas(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("project", "V1C001", "first"))
    first = storage.get_draft_revision("project", "V1C001")
    asyncio.run(storage.save_current_draft("project", "V1C001", "first"))
    assert storage.get_draft_revision("project", "V1C001")["revision"] == first["revision"]

    asyncio.run(
        storage.save_current_draft(
            "project",
            "V1C001",
            "second",
            expected_revision=first["revision"],
        )
    )
    with pytest.raises(RevisionConflict):
        asyncio.run(
            storage.save_current_draft(
                "project",
                "V1C001",
                "stale",
                expected_revision=first["revision"],
            )
        )


def test_gateway_total_deadline_opens_circuit_only_after_provider_started(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = SlowProvider(delay=0.2)
    gateway = _gateway(provider, tmp_path)
    with pytest.raises(Exception):
        asyncio.run(
            gateway.chat(
                [{"role": "user", "content": "x"}],
                provider="profile",
                timeout_seconds=0.08,
                retry=False,
            )
        )
    assert provider.calls == 1
    with pytest.raises(RuntimeError, match="provider_circuit_open"):
        asyncio.run(gateway.chat([{"role": "user", "content": "x"}], provider="profile", retry=False))


def test_gateway_retry_backoff_respects_total_deadline(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = SlowProvider(failures=10)
    gateway = _gateway(provider, tmp_path)
    gateway.reliability.failure_threshold = 10
    started = time.monotonic()
    with pytest.raises(Exception):
        asyncio.run(
            gateway.chat(
                [{"role": "user", "content": "x"}],
                provider="profile",
                timeout_seconds=0.08,
                retry=True,
            )
        )
    assert time.monotonic() - started < 0.2
    assert provider.calls == 1


def test_gateway_cancellation_releases_capacity_and_preserves_uncertain_idempotency(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = SlowProvider(delay=10)
    gateway = _gateway(provider, tmp_path)

    async def scenario():
        task = asyncio.create_task(
            gateway.chat(
                [{"role": "user", "content": "cancel me"}],
                provider="profile",
                idempotency_key="cancelled-request",
                timeout_seconds=5,
                retry=False,
            )
        )
        for _ in range(100):
            if provider.calls:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        return result[0]

    result = asyncio.run(scenario())
    assert isinstance(result, asyncio.CancelledError)
    control = gateway.reliability.control("deepseek:w2-model")
    assert control.semaphore._value == gateway.reliability.max_concurrency
    with closing(sqlite3.connect(gateway.idempotency_registry.store.path)) as connection:
        assert connection.execute("SELECT status FROM idempotency").fetchone()[0] == "running"


def test_reliability_cancellation_releases_half_open_and_semaphore():
    async def scenario():
        controller = GatewayReliabilityController(max_concurrency=1, failure_threshold=1, cooldown_seconds=1)
        control = controller.control("provider")
        control.circuit.opened_at = time.monotonic() - 2
        await control.semaphore.acquire()
        task = asyncio.create_task(
            controller.before_request("provider", deadline=RequestDeadline(1.0))
        )
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert control.circuit.half_open_inflight is False
        control.semaphore.release()
        lease = await controller.before_request("provider", deadline=RequestDeadline(0.2))
        controller.cancelled(lease)
        assert control.semaphore._value == 1

    asyncio.run(scenario())


def test_rate_limiter_serializes_provider_start_times():
    async def scenario():
        controller = GatewayReliabilityController(max_concurrency=2, min_interval_ms=80)
        starts = []

        async def acquire_once():
            lease = await controller.before_request("provider", deadline=RequestDeadline(1.0))
            starts.append(time.monotonic())
            controller.success(lease)

        await asyncio.gather(acquire_once(), acquire_once())
        return sorted(starts)

    starts = asyncio.run(scenario())
    assert starts[1] - starts[0] >= 0.07


def test_durable_job_restart_does_not_repeat_completed_provider_call(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    store = SQLiteControlStore(tmp_path / "control.sqlite3")
    queue = DurableTaskQueue(tmp_path / "queue", store=store)
    registry = PersistentIdempotencyRegistry(tmp_path / "legacy-idempotency", store=store)
    provider = SlowProvider()
    handler_attempts = 0

    def make_gateway():
        gateway = _gateway(provider, tmp_path)
        gateway.idempotency_registry = registry
        return gateway

    async def handler(payload):
        nonlocal handler_attempts
        handler_attempts += 1
        response = await make_gateway().chat(
            [{"role": "user", "content": "stable durable request"}],
            provider="profile",
            retry=False,
        )
        if handler_attempts == 1:
            raise RuntimeError("crash_after_provider_completion")
        return response

    async def scenario():
        job = await queue.enqueue("work", {}, idempotency_key="provider-job", max_attempts=2)
        worker = DurableTaskWorker(
            queue,
            {"work": handler},
            poll_seconds=0.01,
            lease_seconds=0.5,
            retry_delay=0.01,
        )
        await worker.start()
        for _ in range(300):
            if (queue.get(job["id"]) or {}).get("status") == "dead_letter":
                break
            await asyncio.sleep(0.01)
        await worker.stop()
        return queue.get(job["id"])

    result = asyncio.run(scenario())
    assert result["status"] == "dead_letter"
    assert handler_attempts == 2
    assert provider.calls == 1
    assert "idempotency_result_not_replayable:completed" in result["last_error"]


def test_stream_total_deadline_stops_after_partial_output_without_retry(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = StreamProvider([0.03, 0.2])
    gateway = _gateway(provider, tmp_path)

    async def scenario():
        chunks = []
        with pytest.raises(Exception):
            async for chunk in gateway.stream_chat(
                [{"role": "user", "content": "x"}],
                provider="profile",
                timeout_seconds=0.1,
                chunk_timeout_seconds=1.0,
            ):
                chunks.append(chunk)
        return chunks

    assert asyncio.run(scenario()) == ["chunk-0"]
    assert provider.calls == 1


def test_stream_timeout_explicitly_closes_provider_iterator(monkeypatch, tmp_path: Path):
    import app.llm_gateway.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "egress_ledger", EgressLedger(str(tmp_path)))
    provider = CloseTrackedProvider()
    gateway = _gateway(provider, tmp_path)

    async def scenario():
        with pytest.raises(Exception):
            async for _ in gateway.stream_chat(
                [{"role": "user", "content": "x"}],
                provider="profile",
                timeout_seconds=0.05,
                chunk_timeout_seconds=1.0,
            ):
                pass

    asyncio.run(scenario())
    assert provider.stream.closed is True


def test_expired_deadline_closes_unstarted_awaitable():
    class ClosableAwaitable:
        def __init__(self):
            self.closed = False

        def __await__(self):
            return iter(())

        def close(self):
            self.closed = True

    async def scenario():
        awaitable = ClosableAwaitable()
        deadline = RequestDeadline(0.001, started_at=time.monotonic() - 1)
        with pytest.raises(TimeoutError, match="request_deadline_exceeded"):
            await deadline.wait_for(awaitable)
        return awaitable.closed

    assert asyncio.run(scenario()) is True
