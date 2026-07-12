"""Markdown renderer for persisted longform benchmark runs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def render_report(
    manifest: Dict[str, Any],
    config: Dict[str, Any],
    metrics: Dict[str, Any],
    failures: List[Dict[str, Any]],
    *,
    calibration_analysis: Optional[Dict[str, Any]] = None,
) -> str:
    retrieval = metrics.get("retrieval") or {}
    memory = metrics.get("memory") or {}
    safety = metrics.get("safety") or {}
    judge = metrics.get("judge") or {}
    cost = metrics.get("cost") or {}
    state_probe = metrics.get("character_state_probe") or {}
    timeline_probe = metrics.get("timeline_foreshadow_probe") or {}
    no_context = metrics.get("no_context_probe") or {}
    counterfactual = metrics.get("counterfactual_adherence") or {}
    agreement = judge.get("judge_human_agreement") or {}
    calibration = calibration_analysis or {}
    pairwise = calibration.get("pairwise") or {}
    rubric = calibration.get("rubric_agreement") or {}
    human_by_variant = calibration.get("human_by_variant") or {}
    calibration_failure_counts = calibration.get("failure_counts") or {}
    return "\n".join(
        [
            "# Longform Benchmark Report",
            "",
            f"- benchmark_id: `{manifest.get('benchmark_id')}`",
            f"- corpus: `{manifest.get('corpus_name')}`",
            f"- suite: `{config.get('suite')}`",
            f"- strategy: `{config.get('strategy')}`",
            f"- license_status: `{manifest.get('license_status')}`",
            f"- allow_external_api: `{manifest.get('allow_external_api')}`",
            f"- pollution_probe_score: `{(manifest.get('pollution_probe') or {}).get('score')}`",
            "",
            "## Metrics",
            "",
            f"- requested retrieval strategy: `{retrieval.get('requested_strategy')}`",
            f"- executed retrieval strategy: `{retrieval.get('executed_strategy')}`",
            f"- retrieval strategy fidelity: `{retrieval.get('strategy_fidelity')}`",
            f"- retrieval recall: `{retrieval.get('recall')}` (top_k=`{retrieval.get('top_k')}`)",
            f"- retrieval MRR: `{retrieval.get('mrr')}`",
            f"- retrieval P95 latency ms: `{(retrieval.get('latency_ms') or {}).get('p95')}`",
            f"- selected context tokens/query: `{cost.get('selected_context_tokens_per_query')}`",
            f"- memory pollution rate: `{memory.get('pollution_rate')}`",
            f"- compact key retention: `{(metrics.get('compact_fresh') or {}).get('key_retention_rate')}`",
            f"- safety success: `{safety.get('success')}`",
            f"- character state accuracy: `{state_probe.get('accuracy')}`",
            f"- timeline/foreshadow accuracy: `{timeline_probe.get('accuracy')}`",
            f"- no-context pollution score: `{no_context.get('pollution_score')}`",
            f"- counterfactual adherence: `{counterfactual.get('adherence')}`",
            f"- estimated prompt tokens: `{cost.get('estimated_prompt_tokens')}`",
            f"- token saving vs full stuffing: `{cost.get('estimated_token_saving_vs_full_stuffing')}`",
            f"- judge available: `{judge.get('available')}`",
            f"- judge-human agreement: `{agreement.get('score')}`",
            f"- failures: `{len(failures)}`",
            "",
            "## Calibration",
            "",
            f"- analysis available: `{bool(calibration)}`",
            f"- human full-minus-low avg: `{human_by_variant.get('full_minus_low_avg')}`",
            f"- rubric agreement score: `{rubric.get('score')}`",
            f"- rubric gate passed: `{rubric.get('gate_passed')}`",
            f"- pairwise score: `{pairwise.get('score')}`",
            f"- pairwise pairs: `{pairwise.get('num_pairs')}` / `{pairwise.get('min_pairs')}`",
            f"- pairwise comparable rate: `{pairwise.get('comparable_rate')}`",
            f"- pairwise position consistency: `{pairwise.get('position_consistent_rate')}`",
            f"- pairwise gate passed: `{pairwise.get('gate_passed')}`",
            f"- calibration failures: `{calibration_failure_counts}`",
            "",
            "## Recommendation",
            "",
            "- Treat judge scores as gates only after judge-human agreement is calibrated above threshold.",
            "- Treat no-context score as contamination context, not WenShape capability.",
            "- Prefer a strategy only when quality is non-regressive and token/P95 latency regression stays under 20%.",
            "- Promote calibration failures before declaring a strategy stable; sample-size gates are hard gates, not advisory labels.",
            "",
            "## Failure Top",
            "",
            *[f"- {item.get('category')}: {item.get('id')}" for item in failures[:20]],
            "",
        ]
    )
