"""Real-provider smoke for P14 reliability, egress, queue and recovery controls."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import app.llm_gateway.gateway as gateway_module
from app.context_engine.trace_collector import TraceEvent, TraceEventType
from app.jobs.durable_queue import DurableTaskQueue, DurableTaskWorker
from app.llm_gateway import get_gateway
from app.llm_gateway.reliability import PersistentIdempotencyRegistry
from app.observability.otel import telemetry
from app.ops.project_maintenance import ProjectMaintenanceService
from app.security.egress_ledger import EgressLedger


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wenshape-p14-") as directory:
        root = Path(directory)
        telemetry.configure(root / "data", exporter_mode="file")
        ledger = EgressLedger(str(root / "data"))
        gateway_module.egress_ledger = ledger
        gateway = get_gateway()
        gateway.idempotency_registry = PersistentIdempotencyRegistry(root / "idempotency")
        provider = gateway.get_provider_for_agent("writer")
        before = gateway.total_requests
        first = await gateway.chat(
            [{"role": "user", "content": "只回复一个简短确认。"}],
            provider=provider,
            max_tokens=100,
            idempotency_key="p14-real-smoke-idempotency",
            data_classification="synthetic",
        )
        second = await gateway.chat(
            [{"role": "user", "content": "只回复一个简短确认。"}],
            provider=provider,
            max_tokens=100,
            idempotency_key="p14-real-smoke-idempotency",
            data_classification="synthetic",
        )

        queue = DurableTaskQueue(root / "queue")

        async def handler(payload):
            return {"processed": payload["value"]}

        job = await queue.enqueue("smoke", {"value": 1}, idempotency_key="p14-job")
        worker = DurableTaskWorker(queue, {"smoke": handler}, poll_seconds=0.01)
        await worker.start()
        for _ in range(100):
            if (queue.get(job["id"]) or {}).get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        await worker.stop()

        data = root / "projects"
        project = data / "smoke"
        project.mkdir(parents=True)
        (project / "state.json").write_text('{"revision":1}\n', encoding="utf-8")
        maintenance = ProjectMaintenanceService(data)
        backup = maintenance.backup("smoke", root / "smoke.zip")
        restored = maintenance.restore(root / "smoke.zip", project_id="restored")

        event = TraceEvent(
            id="evt_smoke",
            type=TraceEventType.LLM_RESPONSE,
            agent_name="writer",
            timestamp=1.0,
            trace_id="trace_p14",
            span_id="1",
            data={"provider": first.get("provider"), "latency_ms": int(float(first.get("elapsed_time") or 0) * 1000)},
        )
        from app.observability.otel import telemetry as active_telemetry

        active_telemetry.record_event(event)
        active_telemetry.force_flush()
        otel_path = active_telemetry.export_path
        ledger_text = ledger.path.read_text(encoding="utf-8")
        output = {
            "success": True,
            "provider": first.get("provider"),
            "model": first.get("model"),
            "real_requests": gateway.total_requests - before,
            "idempotency_replayed": second.get("idempotency_replayed") is True,
            "egress_entries": len(ledger_text.splitlines()),
            "egress_content_free": "只回复" not in ledger_text,
            "queue_status": (queue.get(job["id"]) or {}).get("status"),
            "backup_restore_match": backup["project_fingerprint"] == restored["project_fingerprint"],
            "otel_exported": bool(otel_path and otel_path.exists()),
            "otel_content_free": "只回复" not in (otel_path.read_text(encoding="utf-8") if otel_path else ""),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not all(
            [
                output["real_requests"] == 1,
                output["idempotency_replayed"],
                output["egress_content_free"],
                output["queue_status"] == "completed",
                output["backup_restore_match"],
                output["otel_exported"],
                output["otel_content_free"],
            ]
        ):
            raise SystemExit(1)
        active_telemetry.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
