"""Low-cardinality runtime metrics and OpenTelemetry-compatible export."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable


class RuntimeMetrics:
    def __init__(self, *, max_series: int = 256, max_events: int = 100_000, max_histogram_samples: int = 10_000):
        self.max_series = int(max_series)
        self.max_events = int(max_events)
        self.max_histogram_samples = int(max_histogram_samples)
        self._counters: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._events: deque[tuple[float, str, str, float]] = deque(maxlen=self.max_events)

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += float(value)
            self._events.append((time.time(), "counter", name, float(value)))

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            rows = self._histograms[name]
            rows.append(float(value))
            self._events.append((time.time(), "histogram", name, float(value)))
            if len(rows) > self.max_histogram_samples:
                del rows[: len(rows) - self.max_histogram_samples]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            histograms = {name: self._distribution(rows) for name, rows in self._histograms.items()}
        return {
            "counters": counters,
            "histograms": histograms,
            "windows": {
                "5m": self.window_snapshot(300),
                "1h": self.window_snapshot(3600),
            },
        }

    def window_snapshot(self, window_seconds: float) -> Dict[str, Any]:
        cutoff = time.time() - max(1.0, float(window_seconds))
        counters: Dict[str, float] = defaultdict(float)
        histograms: Dict[str, list[float]] = defaultdict(list)
        with self._lock:
            events = list(self._events)
        for timestamp, kind, name, value in events:
            if timestamp < cutoff:
                continue
            if kind == "counter":
                counters[name] += value
            else:
                histograms[name].append(value)
        return {
            "window_seconds": float(window_seconds),
            "counters": dict(counters),
            "histograms": {name: self._distribution(values) for name, values in histograms.items()},
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._events.clear()

    def budget_report(self) -> Dict[str, Any]:
        with self._lock:
            series = sorted(set(self._counters) | set(self._histograms))
            event_count = len(self._events)
            histogram_samples = sum(len(rows) for rows in self._histograms.values())
        checks = {
            "series_cardinality": len(series) <= self.max_series,
            "event_buffer": event_count <= self.max_events,
            "histogram_samples": histogram_samples <= self.max_histogram_samples * max(1, len(self._histograms)),
        }
        return {
            "healthy": all(checks.values()),
            "checks": checks,
            "series": len(series),
            "series_limit": self.max_series,
            "event_count": event_count,
            "event_limit": self.max_events,
            "histogram_samples": histogram_samples,
            "histogram_sample_limit_per_series": self.max_histogram_samples,
        }

    @staticmethod
    def _distribution(values: list[float]) -> Dict[str, float]:
        ordered = sorted(values)
        if not ordered:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

        def at(fraction: float) -> float:
            return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))]

        return {
            "count": len(ordered),
            "p50": at(0.50),
            "p95": at(0.95),
            "p99": at(0.99),
            "max": ordered[-1],
        }


runtime_metrics = RuntimeMetrics()


def trace_to_otel_spans(events: Iterable[Any]) -> list[Dict[str, Any]]:
    spans = []
    for event in events:
        row = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        timestamp_ns = int(float(row.get("timestamp") or 0.0) * 1_000_000_000)
        duration_ns = int(row.get("duration_ms") or 0) * 1_000_000
        spans.append(
            {
                "traceId": str(row.get("trace_id") or "").replace("trace_", "")[:32].ljust(32, "0"),
                "spanId": str(row.get("span_id") or "")[:16].ljust(16, "0"),
                "parentSpanId": str(row.get("parent_span_id") or "")[:16] or None,
                "name": f"wenshape.{row.get('type')}",
                "kind": 1,
                "startTimeUnixNano": timestamp_ns,
                "endTimeUnixNano": timestamp_ns + duration_ns,
                "attributes": {
                    "wenshape.agent": row.get("agent_name"),
                    "wenshape.event_id": row.get("id"),
                    "wenshape.event_type": row.get("type"),
                    **_safe_attributes(row.get("data") or {}),
                },
                "status": {"code": 2 if row.get("type") == "agent_error" else 1},
            }
        )
    return spans


def export_otel_json(path: Path, events: Iterable[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"resourceSpans": [{"resource": {"attributes": {"service.name": "wenshape-backend"}}, "spans": trace_to_otel_spans(events)}]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _safe_attributes(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "provider",
        "model",
        "latency_ms",
        "request_id",
        "trace_id",
        "turn_id",
        "job_id",
        "plan_id",
        "request_fingerprint",
        "route_path",
        "status",
        "reason",
        "selected",
        "tokens",
    }
    return {f"wenshape.{key}": value for key, value in data.items() if key in allowed and not isinstance(value, (dict, list))}
