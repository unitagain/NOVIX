# -*- coding: utf-8 -*-
"""P6 evaluation utilities."""

from app.eval.golden_replay import run_golden_replay_suite
from app.eval.longform_benchmark import LongformBenchmarkHarness, ensure_benchmark_gitignore
from app.eval.retrieval_eval import evaluate_retrieval_recall
from app.eval.trace_replay import replay_trace_file, replay_trace_payload, summarize_trace
from app.eval.p12_context_eval import (
    P12_CONTEXT_COMPARISONS,
    P12_CONTEXT_VARIANTS,
    analyze_p12_pairwise,
    assemble_p12_context,
)
from app.eval.campaign_models import EvalCampaign
from app.eval.campaign_runner import CampaignRunner

__all__ = [
    "evaluate_retrieval_recall",
    "ensure_benchmark_gitignore",
    "LongformBenchmarkHarness",
    "replay_trace_file",
    "replay_trace_payload",
    "run_golden_replay_suite",
    "summarize_trace",
    "P12_CONTEXT_COMPARISONS",
    "P12_CONTEXT_VARIANTS",
    "analyze_p12_pairwise",
    "assemble_p12_context",
    "EvalCampaign",
    "CampaignRunner",
]
