from __future__ import annotations

import asyncio

import pytest

from app.jobs.durable_queue import DurableTaskQueue, DurableTaskWorker


def test_job_idempotency_key_is_bound_to_request_fingerprint(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")
    asyncio.run(queue.enqueue("work", {"value": 1}, idempotency_key="same"))
    with pytest.raises(ValueError, match="job_idempotency_key_payload_mismatch"):
        asyncio.run(queue.enqueue("work", {"value": 2}, idempotency_key="same"))


@pytest.mark.asyncio
async def test_job_deadline_is_total_across_handler_execution(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")

    async def slow(_payload):
        await asyncio.sleep(1)
        return {"unexpected": True}

    job = await queue.enqueue("slow", {}, idempotency_key="deadline", timeout_seconds=0.05, max_attempts=1)
    worker = DurableTaskWorker(queue, {"slow": slow}, lease_seconds=0.5)
    claimed = await queue.claim(worker.worker_id, lease_seconds=0.5)
    await worker._execute(claimed)
    result = queue.get(job["id"])
    assert result["status"] == "dead_letter"
    assert result["last_error"] == "job_deadline_exceeded"


@pytest.mark.asyncio
async def test_recovery_increments_fence_and_rejects_stale_completion(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")
    job = await queue.enqueue("work", {}, idempotency_key="fence")
    first = await queue.claim("worker-a", lease_seconds=0.5)
    first["lease_expires_at"] = 0
    queue._write(first)
    assert await queue.recover_expired() == 1
    recovered = queue.get(job["id"])
    assert recovered["lease_generation"] > first["lease_generation"]
    assert not await queue.complete(job["id"], "worker-a", {"stale": True}, generation=first["lease_generation"])


@pytest.mark.asyncio
async def test_cancel_after_external_side_effect_is_uncertain(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")
    started = asyncio.Event()

    async def external(_payload):
        from app.jobs.durable_queue import current_task_execution

        execution = current_task_execution()
        await execution.mark_external_side_effect("request-fingerprint")
        started.set()
        await asyncio.Event().wait()

    job = await queue.enqueue("external", {}, idempotency_key="external-cancel")
    worker = DurableTaskWorker(queue, {"external": external}, poll_seconds=0.01, lease_seconds=0.5)
    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=2)
    assert await queue.request_cancel(job["id"])
    for _ in range(100):
        state = queue.get(job["id"])
        if state["status"] == "cancelled_uncertain":
            break
        await asyncio.sleep(0.02)
    await worker.stop()
    assert queue.get(job["id"])["status"] == "cancelled_uncertain"


@pytest.mark.asyncio
async def test_local_queue_soak_closes_worker_tasks_and_connections(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue")

    async def echo(payload):
        return payload

    for index in range(100):
        await queue.enqueue("echo", {"index": index}, idempotency_key=f"soak:{index}")
    worker = DurableTaskWorker(queue, {"echo": echo}, poll_seconds=0.01, lease_seconds=0.5)
    await worker.start()
    for _ in range(500):
        if queue.stats().get("completed") == 100:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    assert queue.stats().get("completed") == 100
    assert worker.snapshot()["running"] is False
    assert worker._handler_task is None
