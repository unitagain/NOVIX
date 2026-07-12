"""Windowed service-level indicators and error-budget evaluation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.observability.runtime_metrics import RuntimeMetrics, runtime_metrics


DEFAULT_SLO_TARGETS = {
    "writer_turn_success_rate": 0.99,
    "writer_turn_p95_ms": 120_000.0,
    "queue_oldest_age_seconds": 300.0,
    "provider_retry_exhaustion_rate": 0.01,
    "compact_reject_rate": 0.02,
    "commit_conflict_rate": 0.01,
    "dead_letter_count": 0.0,
}


class SLOEvaluator:
    def __init__(self, metrics: RuntimeMetrics = runtime_metrics, *, targets: Optional[Dict[str, float]] = None):
        self.metrics = metrics
        self.targets = {**DEFAULT_SLO_TARGETS, **dict(targets or {})}

    def evaluate(self, *, window_seconds: float = 300.0, queue: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        window = self.metrics.window_snapshot(window_seconds)
        counters = window["counters"]
        histograms = window["histograms"]
        writer_success = float(counters.get("writer.turn.success", 0.0))
        writer_failure = float(counters.get("writer.turn.failure", 0.0))
        writer_total = writer_success + writer_failure
        provider_success = float(counters.get("gateway.success", 0.0))
        provider_exhausted = float(counters.get("gateway.retry_exhausted", 0.0))
        compact_success = float(counters.get("compact.accepted", 0.0))
        compact_rejected = float(counters.get("compact.rejected", 0.0))
        commit_success = float(counters.get("commit.success", 0.0))
        commit_conflict = float(counters.get("commit.conflict", 0.0))
        queue = dict(queue or {})
        indicators = {
            "writer_turn_success_rate": _ratio(writer_success, writer_total),
            "writer_turn_p95_ms": float((histograms.get("writer.turn.latency_ms") or {}).get("p95") or 0.0),
            "queue_oldest_age_seconds": float(queue.get("oldest_queued_age_seconds") or 0.0),
            "provider_retry_exhaustion_rate": _ratio(provider_exhausted, provider_success + provider_exhausted),
            "compact_reject_rate": _ratio(compact_rejected, compact_success + compact_rejected),
            "commit_conflict_rate": _ratio(commit_conflict, commit_success + commit_conflict),
            "dead_letter_count": float(queue.get("dead_letter") or counters.get("jobs.dead_letter", 0.0)),
        }
        checks = {
            "writer_turn_success_rate": indicators["writer_turn_success_rate"] >= self.targets["writer_turn_success_rate"]
            if writer_total
            else True,
            "writer_turn_p95_ms": indicators["writer_turn_p95_ms"] <= self.targets["writer_turn_p95_ms"],
            "queue_oldest_age_seconds": indicators["queue_oldest_age_seconds"]
            <= self.targets["queue_oldest_age_seconds"],
            "provider_retry_exhaustion_rate": indicators["provider_retry_exhaustion_rate"]
            <= self.targets["provider_retry_exhaustion_rate"],
            "compact_reject_rate": indicators["compact_reject_rate"] <= self.targets["compact_reject_rate"],
            "commit_conflict_rate": indicators["commit_conflict_rate"] <= self.targets["commit_conflict_rate"],
            "dead_letter_count": indicators["dead_letter_count"] <= self.targets["dead_letter_count"],
        }
        error_budget = {
            "writer_turn_remaining": _remaining_budget(
                indicators["writer_turn_success_rate"], self.targets["writer_turn_success_rate"], higher_is_better=True
            ),
            "provider_remaining": _remaining_budget(
                indicators["provider_retry_exhaustion_rate"],
                self.targets["provider_retry_exhaustion_rate"],
                higher_is_better=False,
            ),
        }
        return {
            "healthy": all(checks.values()),
            "window_seconds": float(window_seconds),
            "indicators": indicators,
            "targets": dict(self.targets),
            "checks": checks,
            "error_budget": error_budget,
            "alerts": [name for name, passed in checks.items() if not passed],
        }


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _remaining_budget(actual: float, target: float, *, higher_is_better: bool) -> float:
    allowed = (1.0 - target) if higher_is_better else target
    consumed = (1.0 - actual) if higher_is_better else actual
    if allowed <= 0:
        return 1.0 if consumed <= 0 else 0.0
    return max(0.0, 1.0 - consumed / allowed)


slo_evaluator = SLOEvaluator()

