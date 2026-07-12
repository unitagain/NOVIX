#!/usr/bin/env python3
"""Run a real-provider synthetic W8 soak without exporting project or private corpus content."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm_gateway import get_gateway
from app.llm_gateway.reliability import PersistentIdempotencyRegistry
from app.ops.project_maintenance import ProjectMaintenanceService
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.drafts import DraftStorage
from app.storage.session_history import SessionHistoryStorage


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


async def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    deadline = started + args.duration_seconds
    samples = []
    failures = []
    requests = 0
    tokens = 0
    compacts = 0
    backups = 0
    cycles = 0
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="wenshape-real-soak-") as temporary:
        data_dir = Path(temporary)
        gateway = get_gateway()
        gateway.idempotency_registry = PersistentIdempotencyRegistry(data_dir / "idempotency")
        drafts = DraftStorage(str(data_dir))
        sessions = SessionHistoryStorage(str(data_dir))
        memories = CreativeMemoryStorage(str(data_dir))
        maintenance = ProjectMaintenanceService(data_dir)

        async def summarize(rows):
            nonlocal requests, tokens
            response = await gateway.chat(
                [{"role": "user", "content": "Summarize these synthetic test events in one short sentence."}],
                provider=args.profile,
                temperature=0.0,
                max_tokens=80,
                retry=True,
                timeout_seconds=60,
                data_classification="synthetic",
                idempotency_key=f"w8-soak-summary:{cycles}:{len(rows)}",
            )
            requests += 1
            tokens += int((response.get("usage") or {}).get("total_tokens") or 0)
            return str(response.get("content") or "synthetic summary")

        while time.time() < deadline or cycles < args.min_cycles:
            cycles += 1
            cycle_started = time.perf_counter()
            for session_index in range(args.sessions):
                project_id = f"synthetic-{session_index}"
                chapter = f"V1C{cycles:03d}"
                try:
                    response = await gateway.chat(
                        [{"role": "user", "content": f"Write one harmless synthetic test sentence. cycle={cycles}"}],
                        provider=args.profile,
                        temperature=0.0,
                        max_tokens=80,
                        retry=True,
                        timeout_seconds=60,
                        data_classification="synthetic",
                        idempotency_key=f"w8-soak:{session_index}:{cycles}",
                    )
                    requests += 1
                    tokens += int((response.get("usage") or {}).get("total_tokens") or 0)
                    text = str(response.get("content") or "synthetic")
                    await drafts.save_current_draft(project_id, chapter, text)
                    await sessions.append(project_id, {"role": "user", "content": f"synthetic request {cycles}"})
                    await sessions.append(project_id, {"role": "assistant", "content": text})
                    await memories.write_candidate_memory(
                        project_id,
                        f"soak-{cycles:04d}",
                        "Synthetic soak preference",
                        "Synthetic test data only.",
                        "preference",
                        source="w8_soak",
                        confidence=0.8,
                    )
                    if cycles % args.compact_every == 0:
                        compact = await sessions.compact(
                            project_id,
                            summarize,
                            keep_recent=2,
                            trigger_at=3,
                            trigger_tokens=0,
                            provenance={"provider": args.profile, "classification": "synthetic"},
                        )
                        if compact.get("compacted"):
                            recovered = await sessions.recover_compact_sources(project_id, compact["compact_artifact_id"])
                            if not recovered:
                                failures.append({"type": "compact_source_chain_broken", "cycle": cycles})
                            compacts += 1
                except Exception as exc:
                    failures.append({"type": type(exc).__name__, "cycle": cycles, "session": session_index})
            if cycles % args.backup_every == 0:
                for session_index in range(args.sessions):
                    result = await asyncio.to_thread(
                        maintenance.backup,
                        f"synthetic-{session_index}",
                        data_dir / f"backup-{session_index}.zip",
                    )
                    backups += 1 if result.get("success") else 0
            current, peak = tracemalloc.get_traced_memory()
            samples.append(
                {
                    "timestamp": time.time(),
                    "python_bytes": current,
                    "python_peak_bytes": peak,
                    "threads": threading.active_count(),
                    "data_bytes": _directory_bytes(data_dir),
                    "cycle_latency_ms": (time.perf_counter() - cycle_started) * 1000.0,
                }
            )
            if time.time() < deadline:
                await asyncio.sleep(max(0.0, args.interval_seconds))

        elapsed = time.time() - started
        first = samples[0] if samples else {}
        last = samples[-1] if samples else {}
        memory_growth = int(last.get("python_bytes") or 0) - int(first.get("python_bytes") or 0)
        checks = {
            "target_duration": elapsed >= args.duration_seconds,
            "minimum_cycles": cycles >= args.min_cycles,
            "provider_failures": not failures,
            "compact_recovery": compacts > 0,
            "backup_completed": backups > 0,
            "memory_growth": memory_growth <= args.max_memory_growth_bytes,
            "thread_growth": int(last.get("threads") or 0) - int(first.get("threads") or 0) <= 4,
        }
        ordered_latencies = sorted(float(row.get("cycle_latency_ms") or 0.0) for row in samples)
        p95_index = min(len(ordered_latencies) - 1, int(max(0, len(ordered_latencies) - 1) * 0.95)) if ordered_latencies else 0
        p95_latency = ordered_latencies[p95_index] if ordered_latencies else 0.0
        return {
            "success": all(checks.values()),
            "classification": "synthetic",
            "profile": args.profile,
            "started_at": started,
            "elapsed_seconds": elapsed,
            "cycles": cycles,
            "sessions": args.sessions,
            "requests": requests,
            "tokens": tokens,
            "compacts": compacts,
            "backups": backups,
            "memory_growth_bytes": memory_growth,
            "checks": checks,
            "failures": failures,
            "slo_calibration": {
                "observed_cycle_p95_ms": p95_latency,
                "observed_request_failure_rate": len(failures) / max(1, requests),
                "recommended_writer_turn_p95_ms": max(1_000.0, p95_latency * 1.5),
                "recommended_error_rate_budget": max(0.01, (len(failures) / max(1, requests)) * 2.0),
            },
            "samples": samples,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--duration-seconds", type=float, default=1800.0)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--min-cycles", type=int, default=20)
    parser.add_argument("--compact-every", type=int, default=4)
    parser.add_argument("--backup-every", type=int, default=5)
    parser.add_argument("--max-memory-growth-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / ".runtime" / "w8" / "soak.json")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
