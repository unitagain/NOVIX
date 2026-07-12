"""Statistical kernels for longform comparisons."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Dict, Iterable, List


def numeric_distribution(values: Iterable[float]) -> Dict[str, float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def nearest_rank(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
        return ordered[index]

    return {
        "mean": sum(ordered) / len(ordered),
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "max": ordered[-1],
    }


def cluster_bootstrap_mean_ci(
    rows: List[Dict[str, Any]], *, seed_material: str, samples: int = 10_000
) -> Dict[str, Any]:
    clusters: Dict[str, List[float]] = {}
    for row in rows or []:
        clusters.setdefault(str(row.get("scene_id") or "unknown"), []).append(float(row.get("score_b") or 0.0))
    cluster_ids = sorted(clusters)
    if not cluster_ids:
        return {"lower": 0.0, "upper": 0.0, "method": "scene_cluster_bootstrap", "clusters": 0}
    digest = hashlib.sha256((seed_material or "strategy-ab").encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    estimates: List[float] = []
    for _ in range(max(1, int(samples or 1))):
        sampled_values: List[float] = []
        for _ in cluster_ids:
            sampled_values.extend(clusters[rng.choice(cluster_ids)])
        estimates.append(sum(sampled_values) / len(sampled_values))
    estimates.sort()

    def percentile(fraction: float) -> float:
        index = max(0, min(len(estimates) - 1, math.ceil(len(estimates) * fraction) - 1))
        return estimates[index]

    return {
        "lower": percentile(0.025),
        "upper": percentile(0.975),
        "method": "scene_cluster_bootstrap",
        "clusters": len(cluster_ids),
        "samples": len(estimates),
    }
