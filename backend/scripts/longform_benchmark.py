#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run WenShape P9 longform benchmark harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    backend_dir = _backend_dir()
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def _print(payload) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _metric_summary(metrics: dict) -> dict:
    retrieval = metrics.get("retrieval") or {}
    memory = metrics.get("memory") or {}
    compact = metrics.get("compact_fresh") or {}
    safety = metrics.get("safety") or {}
    state_probe = metrics.get("character_state_probe") or {}
    timeline_probe = metrics.get("timeline_foreshadow_probe") or {}
    no_context = metrics.get("no_context_probe") or {}
    counterfactual = metrics.get("counterfactual_adherence") or {}
    judge = metrics.get("judge") or {}
    trace = metrics.get("trace_replay") or {}
    cost = metrics.get("cost") or {}
    return {
        "retrieval": {
            "available": retrieval.get("available"),
            "num_cases": retrieval.get("num_cases"),
            "recall": retrieval.get("recall"),
            "hit_rate": retrieval.get("hit_rate"),
            "mrr": retrieval.get("mrr"),
            "ndcg": retrieval.get("ndcg"),
            "requested_strategy": retrieval.get("requested_strategy"),
            "executed_strategy": retrieval.get("executed_strategy"),
            "strategy_fidelity": retrieval.get("strategy_fidelity"),
            "p95_latency_ms": (retrieval.get("latency_ms") or {}).get("p95"),
        },
        "memory": {
            "available": memory.get("available"),
            "success": memory.get("success"),
            "pollution_rate": memory.get("pollution_rate"),
        },
        "compact_fresh": {
            "available": compact.get("available"),
            "key_retention_rate": compact.get("key_retention_rate"),
            "fresh_context_recoverable": compact.get("fresh_context_recoverable"),
        },
        "safety": {
            "available": safety.get("available"),
            "success": safety.get("success"),
            "detection_rate": safety.get("detection_rate"),
        },
        "character_state_probe": {
            "available": state_probe.get("available"),
            "num_cases": state_probe.get("num_cases"),
            "accuracy": state_probe.get("accuracy"),
        },
        "timeline_foreshadow_probe": {
            "available": timeline_probe.get("available"),
            "num_cases": timeline_probe.get("num_cases"),
            "accuracy": timeline_probe.get("accuracy"),
        },
        "no_context_probe": {
            "available": no_context.get("available"),
            "success": no_context.get("success"),
            "pollution_score": no_context.get("pollution_score"),
            "reason": no_context.get("reason"),
            "skipped": no_context.get("skipped"),
        },
        "counterfactual_adherence": {
            "available": counterfactual.get("available"),
            "success": counterfactual.get("success"),
            "adherence": counterfactual.get("adherence"),
            "reason": counterfactual.get("reason"),
            "skipped": counterfactual.get("skipped"),
        },
        "judge": {
            "available": judge.get("available"),
            "success": judge.get("success"),
            "reason": judge.get("reason"),
        },
        "trace_replay": {
            "success": trace.get("success"),
            "num_cases": trace.get("num_cases"),
            "failures": trace.get("failures"),
        },
        "cost": {
            "strategy": cost.get("strategy"),
            "estimated_prompt_tokens": cost.get("estimated_prompt_tokens"),
            "estimated_corpus_tokens": cost.get("estimated_corpus_tokens"),
            "jit_reference_tokens": cost.get("jit_reference_tokens"),
            "full_stuffing_reference_tokens": cost.get("full_stuffing_reference_tokens"),
            "estimated_token_saving_vs_full_stuffing": cost.get("estimated_token_saving_vs_full_stuffing"),
        },
    }


def _stdout_payload(command: str, result: dict) -> dict:
    if command == "generate":
        generation = result.get("generation") or {}
        return {
            "success": result.get("success"),
            "path": result.get("path"),
            "generation": {
                "mode": generation.get("mode"),
                "llm": generation.get("llm"),
                "counts": generation.get("counts"),
                "scene_windows": generation.get("scene_windows"),
            },
            "note": "stdout omits corpus excerpts and raw LLM payloads; private artifacts remain under the benchmark directory.",
        }
    if command != "run":
        return result
    metrics = result.get("metrics") or {}
    return {
        "success": result.get("success"),
        "run_id": result.get("run_id"),
        "run_dir": result.get("run_dir"),
        "benchmark_id": metrics.get("benchmark_id"),
        "suite": metrics.get("suite"),
        "strategy": metrics.get("strategy"),
        "case_counts": metrics.get("case_counts"),
        "metrics_summary": _metric_summary(metrics),
        "failures": len(result.get("failures") or []),
        "note": "stdout is summarized to avoid leaking corpus text; full metrics are written under the run directory.",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WenShape P9 longform benchmark harness")
    parser.add_argument("--root", default="benchmarks", help="Benchmark root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Import txt/md/epub corpus")
    p_import.add_argument("--source", required=True)
    p_import.add_argument("--benchmark-id", required=True)
    p_import.add_argument("--corpus-name", default="")
    p_import.add_argument("--license-status", default="private")
    p_import.add_argument(
        "--data-classification",
        default="private",
        choices=["public", "user_authorized", "private", "local_only"],
    )
    p_import.add_argument("--allow-external-api", action="store_true")
    p_import.add_argument("--split-mode", default="auto", choices=["auto", "headings", "files"])

    p_generate = sub.add_parser("generate", help="Generate silver benchmark candidates")
    p_generate.add_argument("--benchmark-id", required=True)
    p_generate.add_argument("--use-llm", action="store_true")
    p_generate.add_argument("--provider", default=None)
    p_generate.add_argument(
        "--force-external",
        action="store_true",
        help="Use the external provider for this run after explicit corpus-level approval",
    )
    p_generate.add_argument("--max-chapters", type=int, default=0)
    p_generate.add_argument(
        "--scene-windows",
        type=int,
        default=1,
        help="Number of scene windows to generate per chapter for calibration capacity",
    )

    p_generate_calibration = sub.add_parser(
        "generate-calibration",
        help="Generate real LLM continuation candidates for human/judge calibration",
    )
    p_generate_calibration.add_argument("--benchmark-id", required=True)
    p_generate_calibration.add_argument("--provider", default=None)
    p_generate_calibration.add_argument("--limit", type=int, default=5)
    p_generate_calibration.add_argument("--variants", default="full_context,low_context")
    p_generate_calibration.add_argument("--force-external", action="store_true")
    p_generate_calibration.add_argument("--require-available", action="store_true")
    p_generate_calibration.add_argument(
        "--scene-id",
        action="append",
        default=[],
        help="Only generate calibration candidates for the given scene id; may be repeated",
    )
    p_generate_calibration.add_argument(
        "--append",
        action="store_true",
        help="Append generated rows to the current calibration_candidates.jsonl instead of replacing it",
    )
    p_generate_calibration.add_argument(
        "--skip-scored",
        action="store_true",
        help="Skip chapters that already have scored full_context and low_context gold rows",
    )

    p_generate_strategy_ab = sub.add_parser(
        "generate-strategy-ab",
        help="Generate paired writer outputs using two real retrieval strategies",
    )
    p_generate_strategy_ab.add_argument("--benchmark-id", required=True)
    p_generate_strategy_ab.add_argument("--strategy-a", default="bm25")
    p_generate_strategy_ab.add_argument("--strategy-b", default="jit_hybrid")
    p_generate_strategy_ab.add_argument("--provider", default=None)
    p_generate_strategy_ab.add_argument("--limit", type=int, default=5)
    p_generate_strategy_ab.add_argument("--trials", type=int, default=1)
    p_generate_strategy_ab.add_argument("--temperature", type=float, default=0.7)
    p_generate_strategy_ab.add_argument("--max-tokens", type=int, default=2200)
    p_generate_strategy_ab.add_argument("--top-k", type=int, default=10)
    p_generate_strategy_ab.add_argument("--force-external", action="store_true")
    p_generate_strategy_ab.add_argument("--require-available", action="store_true")
    p_generate_strategy_ab.add_argument("--scene-id", action="append", default=[])
    p_generate_strategy_ab.add_argument("--append", action="store_true")

    p_preflight_strategy_ab = sub.add_parser(
        "preflight-strategy-ab",
        help="Check strategy fidelity and context distinctness without API calls",
    )
    p_preflight_strategy_ab.add_argument("--benchmark-id", required=True)
    p_preflight_strategy_ab.add_argument("--strategy-a", default="bm25")
    p_preflight_strategy_ab.add_argument("--strategy-b", default="jit_hybrid")
    p_preflight_strategy_ab.add_argument("--top-k", type=int, default=10)
    p_preflight_strategy_ab.add_argument("--scene-id", action="append", default=[])

    p_refresh_strategy_refs = sub.add_parser(
        "refresh-strategy-references",
        help="Attach held-out scene continuations to existing strategy candidates without API calls",
    )
    p_refresh_strategy_refs.add_argument("--benchmark-id", required=True)

    p_strategy_review = sub.add_parser(
        "strategy-review-pack",
        help="Create a blinded full-candidate human calibration pack",
    )
    p_strategy_review.add_argument("--benchmark-id", required=True)
    p_strategy_review.add_argument("--size", type=int, default=20)

    p_apply_strategy_review = sub.add_parser(
        "apply-strategy-review",
        help="Validate a completed strategy review pack and persist content-free gold labels",
    )
    p_apply_strategy_review.add_argument("--benchmark-id", required=True)
    p_apply_strategy_review.add_argument("--review-path", required=True)
    p_apply_strategy_review.add_argument("--key-path", required=True)
    p_apply_strategy_review.add_argument("--reviewer", required=True)

    p_record_strategy_review = sub.add_parser(
        "record-strategy-review",
        help="Record one blinded strategy review decision without reading the mapping key",
    )
    p_record_strategy_review.add_argument("--review-path", required=True)
    p_record_strategy_review.add_argument("--review-id", required=True)
    p_record_strategy_review.add_argument(
        "--winner",
        required=True,
        choices=["left", "right", "tie", "incomparable"],
    )
    p_record_strategy_review.add_argument("--reason-code", action="append", default=[])
    p_record_strategy_review.add_argument("--reviewer", required=True)

    p_show_strategy_review = sub.add_parser(
        "show-strategy-review",
        help="Show one blinded review row by one-based index",
    )
    p_show_strategy_review.add_argument("--review-path", required=True)
    p_show_strategy_review.add_argument("--index", type=int, required=True)

    p_strategy_review_status = sub.add_parser(
        "strategy-review-status",
        help="Show review progress and candidate completeness signals without the full prose",
    )
    p_strategy_review_status.add_argument("--review-path", required=True)

    p_review = sub.add_parser("review-pack", help="Create human review pack")
    p_review.add_argument("--benchmark-id", required=True)
    p_review.add_argument("--size", type=int, default=50)

    p_apply_review = sub.add_parser("apply-review", help="Promote accepted review-pack rows into gold files")
    p_apply_review.add_argument("--benchmark-id", required=True)
    p_apply_review.add_argument("--review-file", required=True)
    p_apply_review.add_argument("--accept-all", action="store_true", help="Promote all rows; intended only for smoke data")

    p_score_calibration = sub.add_parser("score-calibration", help="Run real judge scoring for human-scored calibration rows")
    p_score_calibration.add_argument("--benchmark-id", required=True)
    p_score_calibration.add_argument("--provider", default=None)
    p_score_calibration.add_argument("--limit", type=int, default=0)
    p_score_calibration.add_argument("--require-judge", action="store_true")
    p_score_calibration.add_argument("--pairwise", action="store_true", help="Also run full_context vs low_context pairwise calibration")
    p_score_calibration.add_argument("--pairwise-only", action="store_true", help="Skip rubric scoring and only run pairwise calibration")
    p_score_calibration.add_argument(
        "--pairwise-retries",
        type=int,
        default=0,
        help="Retry position-inconsistent pairwise judge cases this many times",
    )
    p_score_calibration.add_argument(
        "--scene-id",
        action="append",
        default=[],
        help="Only run pairwise calibration for the given scene id; may be repeated",
    )
    p_score_calibration.add_argument(
        "--append-pairwise",
        action="store_true",
        help="Merge targeted pairwise results into the latest pairwise judge file",
    )

    p_analyze_calibration = sub.add_parser("analyze-calibration", help="Analyze calibration scores and pairwise failures without API calls")
    p_analyze_calibration.add_argument("--benchmark-id", required=True)

    p_score_strategy_ab = sub.add_parser(
        "score-strategy-ab",
        help="Run order-free v4 pointwise judge scoring for retrieval strategy output pairs",
    )
    p_score_strategy_ab.add_argument("--benchmark-id", required=True)
    p_score_strategy_ab.add_argument("--provider", default=None)
    p_score_strategy_ab.add_argument("--require-judge", action="store_true")
    p_score_strategy_ab.add_argument("--pairwise-retries", type=int, default=0)
    p_score_strategy_ab.add_argument("--scene-id", action="append", default=[])
    p_score_strategy_ab.add_argument("--pair-id", action="append", default=[])
    p_score_strategy_ab.add_argument("--append-pairwise", action="store_true")
    p_score_strategy_ab.add_argument("--force-external", action="store_true")

    p_analyze_strategy_ab = sub.add_parser(
        "analyze-strategy-ab",
        help="Analyze current retrieval strategy output A/B evidence without API calls",
    )
    p_analyze_strategy_ab.add_argument("--benchmark-id", required=True)

    p_analyze_p12 = sub.add_parser(
        "analyze-p12-context-ab",
        help="Analyze memory/compact output A/B evidence and promote hard failures",
    )
    p_analyze_p12.add_argument("--benchmark-id", required=True)

    p_generate_p12 = sub.add_parser("generate-p12-context-ab", help="Generate real writer pairs from P12 context cases")
    p_generate_p12.add_argument("--benchmark-id", required=True)
    p_generate_p12.add_argument("--provider", default=None)
    p_generate_p12.add_argument("--force-external", action="store_true")
    p_generate_p12.add_argument("--require-available", action="store_true")

    p_score_p12 = sub.add_parser("score-p12-context-ab", help="Run order-free v4 pointwise judge for P12 pairs")
    p_score_p12.add_argument("--benchmark-id", required=True)
    p_score_p12.add_argument("--provider", default=None)
    p_score_p12.add_argument("--force-external", action="store_true")
    p_score_p12.add_argument("--require-judge", action="store_true")
    p_score_p12.add_argument("--pairwise-retries", type=int, default=0)

    p_compare_strategy_ab = sub.add_parser(
        "compare-strategy-ab",
        help="Aggregate current strategy output A/B evidence across corpora",
    )
    p_compare_strategy_ab.add_argument(
        "--benchmark-id",
        action="append",
        required=True,
        help="Benchmark id to include; repeat for each corpus",
    )

    p_run = sub.add_parser("run", help="Run benchmark suite")
    p_run.add_argument("--benchmark-id", required=True)
    p_run.add_argument("--suite", default="smoke", choices=["smoke", "baseline", "full"])
    p_run.add_argument(
        "--strategy",
        default="jit_hybrid",
        help="Executable retrieval policy: full_stuffing, bm25, lexical, minimal, hybrid, hybrid_rerank, jit_hybrid",
    )
    p_run.add_argument("--provider", default=None)
    p_run.add_argument("--judge", action="store_true", help="Run real LLM judge when available")
    p_run.add_argument("--require-judge", action="store_true")
    p_run.add_argument("--no-context-probe", action="store_true", help="Run real no-context contamination probe")
    p_run.add_argument("--counterfactual", action="store_true", help="Run real counterfactual adherence probe")
    p_run.add_argument("--run-id", default=None)

    p_compare = sub.add_parser("compare", help="Compare two runs or strategies")
    p_compare.add_argument("--benchmark-id", required=True)
    p_compare.add_argument("--run-a", default=None)
    p_compare.add_argument("--run-b", default=None)
    p_compare.add_argument("--strategy-a", default=None)
    p_compare.add_argument("--strategy-b", default=None)

    p_promote = sub.add_parser("promote-failures", help="Promote failures to replay cases")
    p_promote.add_argument("--benchmark-id", required=True)
    p_promote.add_argument("--run", required=True)
    p_promote.add_argument("--limit", type=int, default=10)
    p_promote.add_argument(
        "--no-calibration",
        action="store_true",
        help="Only promote run failures; by default calibration and strategy A/B failures are promoted too",
    )

    p_report = sub.add_parser("report", help="Render benchmark report")
    p_report.add_argument("--benchmark-id", required=True)
    p_report.add_argument("--run", required=True)

    p_gitignore = sub.add_parser("ensure-gitignore", help="Ensure benchmark corpus folders are gitignored")
    p_gitignore.add_argument("--repo-root", default=str(_backend_dir().parents[0]))

    return parser


async def _main() -> int:
    _ensure_sys_path()
    from app.eval.longform_benchmark import LongformBenchmarkHarness, ensure_benchmark_gitignore

    args = _parser().parse_args()
    harness = LongformBenchmarkHarness(args.root)
    command = args.command

    if command == "import":
        result = harness.import_corpus(
            source=args.source,
            benchmark_id=args.benchmark_id,
            corpus_name=args.corpus_name,
            license_status=args.license_status,
            data_classification=args.data_classification,
            allow_external_api=args.allow_external_api,
            split_mode=args.split_mode,
        )
    elif command == "generate":
        result = await harness.generate_candidates(
            benchmark_id=args.benchmark_id,
            use_llm=args.use_llm,
            provider=args.provider,
            max_chapters=args.max_chapters,
            scene_windows=args.scene_windows,
            force_external=args.force_external,
        )
    elif command == "generate-calibration":
        result = await harness.generate_writing_calibration(
            benchmark_id=args.benchmark_id,
            provider=args.provider,
            limit=args.limit,
            variants=[item.strip() for item in str(args.variants or "").split(",") if item.strip()],
            force_external=args.force_external,
            require_available=args.require_available,
            skip_scored=args.skip_scored,
            scene_ids=args.scene_id,
            append=args.append,
        )
    elif command == "preflight-strategy-ab":
        result = await harness.preflight_strategy_ab(
            benchmark_id=args.benchmark_id,
            strategy_a=args.strategy_a,
            strategy_b=args.strategy_b,
            top_k=args.top_k,
            scene_ids=args.scene_id,
        )
    elif command == "generate-strategy-ab":
        result = await harness.generate_strategy_ab(
            benchmark_id=args.benchmark_id,
            strategy_a=args.strategy_a,
            strategy_b=args.strategy_b,
            provider=args.provider,
            limit=args.limit,
            trials=args.trials,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            top_k=args.top_k,
            force_external=args.force_external,
            require_available=args.require_available,
            scene_ids=args.scene_id,
            append=args.append,
        )
    elif command == "refresh-strategy-references":
        result = harness.refresh_strategy_references(benchmark_id=args.benchmark_id)
    elif command == "strategy-review-pack":
        result = harness.build_strategy_review_pack(benchmark_id=args.benchmark_id, size=args.size)
    elif command == "apply-strategy-review":
        result = harness.apply_strategy_review_pack(
            benchmark_id=args.benchmark_id,
            review_path=args.review_path,
            key_path=args.key_path,
            reviewer=args.reviewer,
        )
    elif command == "record-strategy-review":
        result = harness.record_strategy_review(
            review_path=args.review_path,
            review_id=args.review_id,
            winner=args.winner,
            reason_codes=args.reason_code,
            reviewer=args.reviewer,
        )
    elif command == "show-strategy-review":
        rows = [json.loads(line) for line in Path(args.review_path).read_text(encoding="utf-8").splitlines() if line]
        if args.index < 1 or args.index > len(rows):
            raise ValueError("strategy_review_index_out_of_range")
        result = rows[args.index - 1]
    elif command == "strategy-review-status":
        rows = [json.loads(line) for line in Path(args.review_path).read_text(encoding="utf-8").splitlines() if line]
        result = {
            "pairs": len(rows),
            "reviewed": sum(bool(row.get("human_winner")) for row in rows),
            "rows": [
                {
                    "index": index,
                    "review_id": row.get("review_id"),
                    "left_chars": len(str(row.get("candidate_left") or "")),
                    "right_chars": len(str(row.get("candidate_right") or "")),
                    "left_tail": str(row.get("candidate_left") or "")[-40:],
                    "right_tail": str(row.get("candidate_right") or "")[-40:],
                    "reference_chars": len(str(row.get("reference_excerpt") or "")),
                    "human_winner": row.get("human_winner"),
                }
                for index, row in enumerate(rows, 1)
            ],
        }
    elif command == "review-pack":
        result = harness.build_review_pack(benchmark_id=args.benchmark_id, size=args.size)
    elif command == "apply-review":
        result = harness.apply_review_pack(
            benchmark_id=args.benchmark_id,
            review_file=args.review_file,
            accept_all=args.accept_all,
        )
    elif command == "score-calibration":
        result = await harness.score_calibration(
            benchmark_id=args.benchmark_id,
            provider=args.provider,
            limit=args.limit,
            require_judge=args.require_judge,
            pairwise=args.pairwise,
            pairwise_only=args.pairwise_only,
            pairwise_retries=args.pairwise_retries,
            pairwise_scene_ids=args.scene_id,
            append_pairwise=args.append_pairwise,
        )
    elif command == "analyze-calibration":
        result = harness.analyze_calibration(benchmark_id=args.benchmark_id)
    elif command == "score-strategy-ab":
        result = await harness.score_strategy_ab(
            benchmark_id=args.benchmark_id,
            provider=args.provider,
            require_judge=args.require_judge,
            pairwise_retries=args.pairwise_retries,
            scene_ids=args.scene_id,
            pair_ids=args.pair_id,
            append_latest=args.append_pairwise,
            force_external=args.force_external,
        )
    elif command == "analyze-strategy-ab":
        result = harness.analyze_strategy_ab(benchmark_id=args.benchmark_id)
    elif command == "analyze-p12-context-ab":
        result = harness.analyze_p12_context_ab(benchmark_id=args.benchmark_id)
    elif command == "generate-p12-context-ab":
        result = await harness.generate_p12_context_ab(
            benchmark_id=args.benchmark_id,
            provider=args.provider,
            force_external=args.force_external,
            require_available=args.require_available,
        )
    elif command == "score-p12-context-ab":
        result = await harness.score_p12_context_ab(
            benchmark_id=args.benchmark_id,
            provider=args.provider,
            force_external=args.force_external,
            require_judge=args.require_judge,
            pairwise_retries=args.pairwise_retries,
        )
    elif command == "compare-strategy-ab":
        result = harness.compare_strategy_ab_corpora(benchmark_ids=args.benchmark_id)
    elif command == "run":
        result = await harness.run_suite(
            benchmark_id=args.benchmark_id,
            suite=args.suite,
            strategy=args.strategy,
            provider=args.provider,
            judge=args.judge,
            require_judge=args.require_judge,
            no_context_probe=args.no_context_probe,
            counterfactual=args.counterfactual,
            run_id=args.run_id,
        )
    elif command == "compare":
        result = harness.compare_runs(
            benchmark_id=args.benchmark_id,
            run_a=args.run_a,
            run_b=args.run_b,
            strategy_a=args.strategy_a,
            strategy_b=args.strategy_b,
        )
    elif command == "promote-failures":
        result = harness.promote_failures(
            benchmark_id=args.benchmark_id,
            run_id=args.run,
            limit=args.limit,
            include_calibration=not args.no_calibration,
        )
    elif command == "report":
        result = harness.report(benchmark_id=args.benchmark_id, run_id=args.run)
    elif command == "ensure-gitignore":
        result = {"success": True, "changed": ensure_benchmark_gitignore(args.repo_root)}
    else:
        raise ValueError(f"unknown command: {command}")

    _print(_stdout_payload(command, result))
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
