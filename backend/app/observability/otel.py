"""OpenTelemetry SDK runtime with privacy-safe file and OTLP exporters."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode


SAFE_ATTRIBUTE_KEYS = {
    "http.request.method",
    "http.response.status_code",
    "url.path",
    "error.type",
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "server.address",
    "wenshape.agent",
    "wenshape.event_id",
    "wenshape.event_type",
    "wenshape.request_id",
    "wenshape.trace_id",
    "wenshape.turn_id",
    "wenshape.job_id",
    "wenshape.plan_id",
    "wenshape.request_fingerprint",
    "wenshape.route_path",
    "wenshape.status",
    "wenshape.reason",
    "wenshape.selected",
    "wenshape.tokens",
}


class JsonlSpanExporter(SpanExporter):
    """Append SDK span envelopes without recording prompts, outputs, or secrets."""

    def __init__(self, path: Path, *, max_file_bytes: int = 256 * 1024 * 1024, retained_files: int = 3):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.exported_spans = 0
        self.failed_exports = 0
        self.exported_bytes = 0
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.retained_files = max(1, int(retained_files))
        self.rotations = 0

    def export(self, spans: Iterable[Any]) -> SpanExportResult:
        rows = []
        for span in spans:
            context = span.get_span_context()
            parent = getattr(span, "parent", None)
            rows.append(
                {
                    "trace_id": f"{context.trace_id:032x}",
                    "span_id": f"{context.span_id:016x}",
                    "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                    "name": span.name,
                    "kind": int(span.kind.value),
                    "start_time_unix_nano": span.start_time,
                    "end_time_unix_nano": span.end_time,
                    "status": span.status.status_code.name,
                    "attributes": _safe_attributes(dict(span.attributes or {})),
                    "resource": _safe_resource(dict(span.resource.attributes or {})),
                }
            )
        try:
            with self._lock:
                incoming_bytes = sum(
                    len((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                    for row in rows
                )
                if self.path.exists() and self.path.stat().st_size + incoming_bytes > self.max_file_bytes:
                    self._rotate()
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    for row in rows:
                        payload = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                        handle.write(payload)
                        self.exported_bytes += len(payload.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                self.exported_spans += len(rows)
            return SpanExportResult.SUCCESS
        except OSError:
            self.failed_exports += 1
            return SpanExportResult.FAILURE

    def _rotate(self) -> None:
        oldest = self.path.with_name(f"{self.path.name}.{self.retained_files}")
        oldest.unlink(missing_ok=True)
        for index in range(self.retained_files - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                os.replace(source, self.path.with_name(f"{self.path.name}.{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        self.rotations += 1

    def budget_report(self, *, max_file_bytes: Optional[int] = None) -> Dict[str, Any]:
        limit = int(max_file_bytes or self.max_file_bytes)
        file_bytes = self.path.stat().st_size if self.path.exists() else 0
        checks = {"export_backpressure": self.failed_exports == 0, "file_size": file_bytes <= limit}
        return {
            "healthy": all(checks.values()),
            "checks": checks,
            "exported_spans": self.exported_spans,
            "failed_exports": self.failed_exports,
            "exported_bytes": self.exported_bytes,
            "file_bytes": file_bytes,
            "file_limit_bytes": limit,
            "retained_files": self.retained_files,
            "rotations": self.rotations,
        }


class TelemetryRuntime:
    def __init__(self):
        self.provider: Optional[TracerProvider] = None
        self.tracer = trace.get_tracer("wenshape.unconfigured")
        self.exporter_mode = "none"
        self.export_path: Optional[Path] = None
        self.exporter: Optional[JsonlSpanExporter] = None

    def configure(self, data_dir: str | Path, *, exporter_mode: Optional[str] = None) -> Dict[str, Any]:
        if self.provider is not None:
            return self.snapshot()
        mode = str(exporter_mode or os.getenv("WENSHAPE_OTEL_EXPORTER", "file")).strip().lower()
        resource = Resource.create(
            {
                "service.name": "wenshape-backend",
                "service.version": "0.1.0",
                "deployment.environment.name": os.getenv("WENSHAPE_ENVIRONMENT", "desktop"),
            }
        )
        provider = TracerProvider(resource=resource)
        if mode == "file":
            root = Path(data_dir)
            if not root.is_absolute():
                root = (Path(__file__).resolve().parents[2] / root).resolve()
            self.export_path = root / "_system" / "telemetry" / "spans.jsonl"
            self.exporter = JsonlSpanExporter(self.export_path)
            provider.add_span_processor(BatchSpanProcessor(self.exporter))
        elif mode == "otlp":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        elif mode != "none":
            raise ValueError(f"unsupported_otel_exporter:{mode}")
        self.provider = provider
        self.tracer = provider.get_tracer("wenshape.backend", "0.1.0")
        self.exporter_mode = mode
        return self.snapshot()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Optional[Dict[str, Any]] = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Optional[Context] = None,
    ) -> Iterator[Any]:
        with self.tracer.start_as_current_span(
            name,
            context=context,
            kind=kind,
            attributes=_safe_attributes({**_correlation_attributes(), **(attributes or {})}),
        ) as span:
            try:
                yield span
            except BaseException as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("error.type", type(exc).__name__)
                raise

    def record_event(self, event: Any) -> Dict[str, str]:
        row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        attributes = {
            "wenshape.agent": row.get("agent_name"),
            "wenshape.event_id": row.get("id"),
            "wenshape.event_type": row.get("type"),
            "wenshape.trace_id": row.get("trace_id"),
            **_event_attributes(row.get("data") or {}),
        }
        start_ns = int(float(row.get("timestamp") or 0.0) * 1_000_000_000)
        span = self.tracer.start_span(
            f"wenshape.{row.get('type')}",
            start_time=start_ns or None,
            attributes=_safe_attributes(attributes),
        )
        if row.get("type") in {"agent_error", "provider_failure"}:
            span.set_status(Status(StatusCode.ERROR))
        duration_ns = max(0, int(row.get("duration_ms") or 0)) * 1_000_000
        span.end(end_time=(start_ns + duration_ns) if start_ns else None)
        context = span.get_span_context()
        return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        return bool(self.provider is None or self.provider.force_flush(timeout_millis=timeout_millis))

    def shutdown(self) -> None:
        if self.provider is not None:
            self.provider.shutdown()
            self.provider = None
            self.tracer = trace.get_tracer("wenshape.unconfigured")
            self.exporter_mode = "none"
            self.export_path = None
            self.exporter = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "configured": self.provider is not None,
            "exporter": self.exporter_mode,
            "export_path": str(self.export_path) if self.export_path else "",
            "budget": self.exporter.budget_report() if self.exporter else {"healthy": True, "checks": {}},
        }


telemetry = TelemetryRuntime()


class OpenTelemetryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        parent = propagate.extract(headers)
        status_code = 0

        async def send_with_status(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)
            await send(message)

        attributes = {
            "http.request.method": scope.get("method") or scope.get("type"),
            "url.path": scope.get("path") or "",
            "server.address": (scope.get("server") or ["", 0])[0],
        }
        with telemetry.span(
            f"{scope.get('type')} {scope.get('path') or '/'}",
            attributes=attributes,
            kind=SpanKind.SERVER,
            context=parent,
        ) as span:
            await self.app(scope, receive, send_with_status)
            if status_code:
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))


def _event_attributes(data: Dict[str, Any]) -> Dict[str, Any]:
    mapping = {
        "provider": "gen_ai.provider.name",
        "model": "gen_ai.response.model",
        "request_id": "wenshape.request_id",
        "trace_id": "wenshape.trace_id",
        "turn_id": "wenshape.turn_id",
        "job_id": "wenshape.job_id",
        "plan_id": "wenshape.plan_id",
        "request_fingerprint": "wenshape.request_fingerprint",
        "route_path": "wenshape.route_path",
        "status": "wenshape.status",
        "reason": "wenshape.reason",
        "selected": "wenshape.selected",
        "tokens": "wenshape.tokens",
    }
    return {
        target: data[source]
        for source, target in mapping.items()
        if source in data and isinstance(data[source], (str, int, float, bool))
    }


def _safe_attributes(attributes: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in attributes.items()
        if key in SAFE_ATTRIBUTE_KEYS and isinstance(value, (str, int, float, bool))
    }


def _correlation_attributes() -> Dict[str, Any]:
    attributes: Dict[str, Any] = {}
    try:
        from app.context_engine.turn_scope import current_turn_scope

        scope = current_turn_scope()
        if scope is not None:
            attributes.update({"wenshape.trace_id": scope.trace_id, "wenshape.turn_id": scope.turn_id})
    except (ImportError, RuntimeError):
        pass
    try:
        from app.jobs.durable_queue import current_task_execution

        execution = current_task_execution()
        if execution is not None:
            attributes["wenshape.job_id"] = execution.job_id
    except (ImportError, RuntimeError):
        pass
    return attributes


def _safe_resource(attributes: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"service.name", "service.version", "deployment.environment.name"}
    return {str(key): value for key, value in attributes.items() if key in allowed}
