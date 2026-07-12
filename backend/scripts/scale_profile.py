#!/usr/bin/env python3
"""Profile core single-host paths with synthetic, privacy-safe workloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.context_engine.select_engine import ContextSelectEngine
from app.eval.campaign_store import CampaignStore
from app.jobs.durable_queue import DurableTaskQueue
from app.ops.project_maintenance import ProjectMaintenanceService
from app.schemas.canon import Fact


def _latency_ms(operation: Callable[[], Any], repeats: int = 7) -> dict[str, float]:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
    }


class _FactStorage:
    def __init__(self, facts: list[Fact]):
        self.facts = facts

    async def get_all_facts(self, project_id: str) -> list[Fact]:
        return self.facts


def _profile_retrieval(sizes: list[int]) -> list[dict[str, Any]]:
    rows = []
    engine = ContextSelectEngine()
    for size in sizes:
        facts = [
            Fact(
                id=f"F{index}",
                statement=f"角色{index % 37}在章节{index % 101}记录线索{index}",
                source=f"V1C{index % 101:03d}",
                introduced_in=f"V1C{index % 101:03d}",
            )
            for index in range(size)
        ]
        storage = _FactStorage(facts)
        asyncio.run(engine.retrieval_select(project_id="profile", query="角色7 线索", item_types=["fact"], storage=storage))
        latency = _latency_ms(
            lambda: asyncio.run(
                engine.retrieval_select(
                    project_id="profile", query="角色7 线索", item_types=["fact"], storage=storage, top_k=10
                )
            )
        )
        rows.append({"candidates": size, **latency})
    return rows


def _profile_queue(root: Path, sizes: list[int]) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        queue = DurableTaskQueue(root / f"queue-{size}")

        async def populate() -> None:
            for index in range(size):
                await queue.enqueue("profile", {"index": index}, idempotency_key=f"profile-{index}")

        started = time.perf_counter()
        asyncio.run(populate())
        enqueue_ms = (time.perf_counter() - started) * 1000.0
        stats_latency = _latency_ms(queue.stats)
        claim_latency = _latency_ms(lambda: asyncio.run(queue.claim("profile-worker", lease_seconds=30)), repeats=5)
        rows.append(
            {
                "jobs": size,
                "enqueue_total_ms": round(enqueue_ms, 3),
                "enqueue_per_job_ms": round(enqueue_ms / max(1, size), 3),
                "stats": stats_latency,
                "claim": claim_latency,
            }
        )
    return rows


def _profile_backup(root: Path, sizes: list[int], bytes_per_file: int) -> list[dict[str, Any]]:
    rows = []
    data_root = root / "backup-data"
    service = ProjectMaintenanceService(data_root)
    payload = ("WenShape profile data\n".encode("utf-8") * ((bytes_per_file // 22) + 1))[:bytes_per_file]
    for size in sizes:
        project_id = f"profile-{size}"
        project = data_root / project_id / "drafts"
        project.mkdir(parents=True)
        for index in range(size):
            (project / f"chunk-{index:06d}.txt").write_bytes(payload)
        destination = root / f"backup-{size}.zip"
        started = time.perf_counter()
        result = service.backup(project_id, destination)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        rows.append(
            {
                "files": size,
                "source_bytes": size * bytes_per_file,
                "archive_bytes": destination.stat().st_size,
                "backup_ms": round(elapsed_ms, 3),
                "throughput_mib_s": round((size * bytes_per_file) / max(elapsed_ms / 1000.0, 1e-9) / (1024**2), 3),
                "manifest_files": len(result["files"]),
            }
        )
    return rows


def _profile_campaign(root: Path, sizes: list[int]) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        store = CampaignStore(root, f"profile-{size}")
        started = time.perf_counter()
        for index in range(size):
            store.append_jsonl(store.jobs_path, {"job_id": f"job-{index}", "status": "completed"})
        append_ms = (time.perf_counter() - started) * 1000.0
        read_latency = _latency_ms(store.latest_job_statuses)
        rows.append(
            {
                "ledger_rows": size,
                "append_total_ms": round(append_ms, 3),
                "append_per_row_ms": round(append_ms / max(1, size), 3),
                "latest_statuses": read_latency,
                "ledger_bytes": store.jobs_path.stat().st_size,
            }
        )
    return rows


def _parse_sizes(value: str) -> list[int]:
    sizes = sorted({int(item) for item in value.split(",") if int(item) > 0})
    if not sizes:
        raise argparse.ArgumentTypeError("at least one positive size is required")
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-sizes", type=_parse_sizes, default=_parse_sizes("100,1000,5000"))
    parser.add_argument("--queue-sizes", type=_parse_sizes, default=_parse_sizes("100,1000"))
    parser.add_argument("--backup-sizes", type=_parse_sizes, default=_parse_sizes("10,100,500"))
    parser.add_argument("--campaign-sizes", type=_parse_sizes, default=_parse_sizes("100,1000"))
    parser.add_argument("--backup-bytes-per-file", type=int, default=2048)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="wenshape-scale-profile-") as temporary:
        root = Path(temporary)
        profile = {
            "schema_version": 1,
            "workload": "synthetic_privacy_safe",
            "platform": {"python": platform.python_version(), "system": platform.platform()},
            "retrieval": _profile_retrieval(args.retrieval_sizes),
            "queue": _profile_queue(root, args.queue_sizes),
            "backup": _profile_backup(root, args.backup_sizes, max(1, args.backup_bytes_per_file)),
            "campaign": _profile_campaign(root, args.campaign_sizes),
            "elapsed_seconds": round(time.time() - started, 3),
        }
    payload = json.dumps(profile, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
