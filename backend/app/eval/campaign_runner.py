"""Resumable, budgeted real-API campaign orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.eval.campaign_models import CampaignUsage, EvalCampaign, campaign_job_id, stable_fingerprint
from app.eval.campaign_store import CampaignStore
from app.eval.longform_artifacts import read_json, read_jsonl
from app.eval.longform_benchmark import LongformBenchmarkHarness
from app.eval.p12_context_eval import p12_pair_fingerprint
from app.eval.writing_judge import POINTWISE_PAIR_JUDGE_PROMPT_VERSION
from app.storage.file_lock import get_file_lock
from app.security.egress_context import EgressPolicy, bind_egress_policy
from app.services.llm_config_service import llm_config_service
from app.error_contract import benchmark_failure, classify_benchmark_failure_record


class CampaignRunner:
    def __init__(self, root: Path, campaign: EvalCampaign, *, harness: Optional[LongformBenchmarkHarness] = None):
        self.root = Path(root)
        self.campaign = campaign
        self.harness = harness or LongformBenchmarkHarness(self.root)
        self.store = CampaignStore(self.root, campaign.id)
        self._write_lock = asyncio.Lock()

    async def run(self) -> Dict[str, Any]:
        async with get_file_lock().lock(self.store.state_path, timeout=1.0):
            return await self._run_locked()

    async def _run_locked(self) -> Dict[str, Any]:
        self._initialize_state()
        semaphore = asyncio.Semaphore(max(1, self.campaign.budget.max_concurrency))

        async def run_corpus(corpus: Any) -> None:
            async with semaphore:
                await self._run_corpus(corpus)

        await asyncio.gather(*(run_corpus(corpus) for corpus in self.campaign.corpora))
        summary = self._aggregate()
        state = self.store.load_state()
        reasons = [str(item) for item in state.get("stop_reasons") or []]
        attention = any(
            reason.startswith("uncertain_job_requires_manual_resolution") or "budget_exhausted" in reason
            for reason in reasons
        )
        state.update(
            {"status": "attention_required" if attention else "completed", "completed_at": time.time(), "summary": summary}
        )
        self.store.save_json(self.store.state_path, state)
        manifest = self._release_manifest(summary)
        self.store.save_json(self.store.release_manifest_path, manifest)
        return {
            "success": not attention,
            "status": state["status"],
            "campaign_id": self.campaign.id,
            "summary": summary,
            "manifest": manifest,
        }

    def _initialize_state(self) -> Dict[str, Any]:
        existing = self.store.load_state()
        if existing:
            if existing.get("campaign_fingerprint") != self.campaign.fingerprint:
                raise ValueError("campaign_config_changed; create a new campaign id")
            return existing
        state = {
            "campaign_id": self.campaign.id,
            "campaign_fingerprint": self.campaign.fingerprint,
            "status": "running",
            "started_at": time.time(),
            "usage": CampaignUsage().to_dict(),
            "stop_reasons": [],
        }
        self.store.save_json(self.store.config_path, self.campaign.to_dict())
        self.store.save_json(self.store.state_path, state)
        return state

    async def _run_corpus(self, corpus: Any) -> None:
        self._enforce_privacy(corpus)
        if "suite" in corpus.enabled_experiments:
            await self._run_suite_job(corpus)
        scene_ids = self._scene_ids(corpus)
        if "retrieval_ab" in corpus.enabled_experiments:
            for writer in self.campaign.writer_providers:
                await self._run_retrieval_batches(corpus, scene_ids, writer, self.campaign.judge_providers)
        if "p12_context_ab" in corpus.enabled_experiments:
            for writer in self.campaign.writer_providers:
                await self._run_p12_jobs(corpus, writer, self.campaign.judge_providers)

    async def _run_suite_job(self, corpus: Any) -> None:
        job_id = campaign_job_id(self.campaign.id, corpus.benchmark_id, "suite", self.campaign.suite)
        if await self._should_skip(job_id) or self._budget_exhausted(0, 0):
            return
        await self._begin_job(job_id, corpus.benchmark_id, "suite")
        started = time.time()
        result = await self.harness.pipeline.statistics.run_suite(
            benchmark_id=corpus.benchmark_id,
            suite=self.campaign.suite,
            strategy=self.campaign.retrieval_strategy_b,
            judge=False,
            run_id=f"campaign_{self.campaign.id}_suite",
        )
        await self._finish_job(
            job_id,
            corpus.benchmark_id,
            "suite",
            result,
            usage={"elapsed_seconds": time.time() - started},
        )

    async def _run_retrieval_batches(
        self,
        corpus: Any,
        scene_ids: List[str],
        writer: str,
        judges: List[str],
    ) -> None:
        size = max(1, self.campaign.budget.batch_scenes)
        for offset in range(0, len(scene_ids), size):
            batch = scene_ids[offset : offset + size]
            generation_job_id = campaign_job_id(
                self.campaign.id, corpus.benchmark_id, "retrieval_ab_generation", writer, ",".join(batch)
            )
            generation_result = self._completed_job_result(generation_job_id)
            if generation_result is None and await self._should_skip(generation_job_id):
                continue
            estimated_requests = len(batch) * self.campaign.trials * 2
            if generation_result is None and self._budget_exhausted(estimated_requests, estimated_requests * 4000):
                await self._record_stop("campaign_budget_exhausted")
                return
            if generation_result is None:
                await self._begin_job(generation_job_id, corpus.benchmark_id, "retrieval_ab_generation")
                started = time.time()
                try:
                    with bind_egress_policy(self._egress_policy(corpus)):
                        generated = await self.harness.pipeline.generation.strategy_ab(
                            benchmark_id=corpus.benchmark_id,
                            strategy_a=self.campaign.retrieval_strategy_a,
                            strategy_b=self.campaign.retrieval_strategy_b,
                            provider=writer,
                            limit=len(batch),
                            trials=self.campaign.trials,
                            scene_ids=batch,
                            append=True,
                            force_external=True,
                            require_available=False,
                        )
                except Exception as exc:
                    await self._finish_job(
                        generation_job_id,
                        corpus.benchmark_id,
                        "retrieval_ab_generation",
                        {**benchmark_failure(exc), "writer": writer},
                        usage={"requests": 0, "elapsed_seconds": time.time() - started},
                        status="failed",
                    )
                    return
                generated_usage = dict(generated.get("usage") or {})
                generation_result = {"generated": generated, "writer": writer}
                await self._finish_job(
                    generation_job_id,
                    corpus.benchmark_id,
                    "retrieval_ab_generation",
                    generation_result,
                    usage={
                        "requests": int(generated.get("requests_attempted") or 0),
                        "prompt_tokens": int(generated_usage.get("prompt_tokens") or 0),
                        "completion_tokens": int(generated_usage.get("completion_tokens") or 0),
                        "total_tokens": int(generated_usage.get("total_tokens") or 0),
                        "elapsed_seconds": time.time() - started,
                    },
                )
            generated = dict(generation_result.get("generated") or {})
            pair_ids = [str(item) for item in (generated.get("pair_ids") or []) if str(item)]
            if not pair_ids:
                continue

            for judge in judges:
                stop_prefix = f"{corpus.benchmark_id}:{writer}:{judge}:"
                if any(
                    str(reason).startswith(stop_prefix)
                    for reason in self.store.load_state().get("stop_reasons") or []
                ):
                    continue
                job_id = campaign_job_id(
                    self.campaign.id, corpus.benchmark_id, "retrieval_ab", writer, judge, ",".join(pair_ids)
                )
                if await self._should_skip(job_id):
                    continue
                estimated_requests = len(pair_ids) * 2 * (self.campaign.pairwise_retries + 1)
                if self._budget_exhausted(estimated_requests, estimated_requests * 4000):
                    await self._record_stop("campaign_budget_exhausted")
                    return
                await self._begin_job(job_id, corpus.benchmark_id, "retrieval_ab")
                started = time.time()
                try:
                    with bind_egress_policy(self._egress_policy(corpus)):
                        scored = await self.harness.pipeline.judge.strategy_ab(
                            benchmark_id=corpus.benchmark_id,
                            provider=judge,
                            require_judge=False,
                            pairwise_retries=self.campaign.pairwise_retries,
                            pair_ids=pair_ids,
                            append_latest=False,
                            force_external=True,
                        )
                except Exception as exc:
                    await self._finish_job(
                        job_id,
                        corpus.benchmark_id,
                        "retrieval_ab",
                        {**benchmark_failure(exc), "writer": writer, "judge": judge, "pair_ids": pair_ids},
                        usage={"requests": 0, "elapsed_seconds": time.time() - started},
                        status="failed",
                    )
                    continue
                analysis = dict(scored.get("analysis") or {})
                result = {
                    "generated_job_id": generation_job_id,
                    "pair_ids": pair_ids,
                    "scored": scored,
                    "analysis": analysis,
                    "writer": writer,
                    "judge": judge,
                }
                judge_usage = dict(scored.get("usage") or {})
                await self._finish_job(
                    job_id,
                    corpus.benchmark_id,
                    "retrieval_ab",
                    result,
                    usage={
                        "requests": int(scored.get("requests_attempted") or 0),
                        "prompt_tokens": int(judge_usage.get("prompt_tokens") or 0),
                        "completion_tokens": int(judge_usage.get("completion_tokens") or 0),
                        "total_tokens": int(judge_usage.get("total_tokens") or 0),
                        "elapsed_seconds": time.time() - started,
                    },
                )
                reason = self._sequential_stop(analysis)
                if reason:
                    await self._record_stop(f"{corpus.benchmark_id}:{writer}:{judge}:{reason}")

    def _completed_job_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        for row in reversed(self.store.jobs()):
            if (
                str(row.get("job_id") or "") == job_id
                and row.get("status") == "completed"
                and self._completed_job_is_current(row)
            ):
                return read_json(Path(str(row.get("result_path") or "")), {}) or {}
        return None

    @staticmethod
    def _egress_policy(corpus: Any) -> EgressPolicy:
        return EgressPolicy(
            corpus_id=corpus.benchmark_id,
            data_classification=corpus.data_classification,
            authorized=corpus.allow_external_api,
        )

    async def _run_p12_jobs(self, corpus: Any, writer: str, judges: List[str]) -> None:
        paths = self.harness.pipeline.corpus.paths(corpus.benchmark_id)
        cases = read_jsonl(paths.generated_dir / "p12_context_cases.jsonl")
        if not cases:
            job_id = campaign_job_id(self.campaign.id, corpus.benchmark_id, "p12_context_generation", writer)
            if await self._should_skip(job_id):
                return
            await self._begin_job(job_id, corpus.benchmark_id, "p12_context_generation")
            await self._finish_job(
                job_id,
                corpus.benchmark_id,
                "p12_context_generation",
                {"success": False, "reason": "missing_p12_context_cases", "writer": writer},
                usage={},
                status="skipped",
            )
            return
        generation_job_id = campaign_job_id(
            self.campaign.id, corpus.benchmark_id, "p12_context_generation", writer
        )
        generation_result = self._completed_job_result(generation_job_id)
        if generation_result is None and await self._should_skip(generation_job_id):
            return
        estimated_requests = len(cases) * 4
        if generation_result is None and self._budget_exhausted(estimated_requests, estimated_requests * 4000):
            await self._record_stop("campaign_budget_exhausted")
            return
        if generation_result is None:
            await self._begin_job(generation_job_id, corpus.benchmark_id, "p12_context_generation")
            started = time.time()
            try:
                with bind_egress_policy(self._egress_policy(corpus)):
                    generated = await self.harness.pipeline.generation.p12_context_ab(
                        benchmark_id=corpus.benchmark_id, provider=writer, force_external=True
                    )
            except Exception as exc:
                await self._finish_job(
                    generation_job_id,
                    corpus.benchmark_id,
                    "p12_context_generation",
                    {**benchmark_failure(exc), "writer": writer},
                    usage={"requests": 0, "elapsed_seconds": time.time() - started},
                    status="failed",
                )
                return
            generated_usage = dict(generated.get("usage") or {})
            generation_result = {"generated": generated, "writer": writer}
            await self._finish_job(
                generation_job_id,
                corpus.benchmark_id,
                "p12_context_generation",
                generation_result,
                usage={
                    "requests": int(generated.get("requests_attempted") or 0),
                    "prompt_tokens": int(generated_usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(generated_usage.get("completion_tokens") or 0),
                    "total_tokens": int(generated_usage.get("total_tokens") or 0),
                    "elapsed_seconds": time.time() - started,
                },
            )
        generated = dict(generation_result.get("generated") or {})
        candidate_path = str(generated.get("candidate_path") or "")
        if not candidate_path:
            return

        for judge in judges:
            job_id = campaign_job_id(self.campaign.id, corpus.benchmark_id, "p12_context_ab", writer, judge)
            if await self._should_skip(job_id):
                continue
            estimated_requests = len(cases) * 2 * (self.campaign.pairwise_retries + 1)
            if self._budget_exhausted(estimated_requests, estimated_requests * 4000):
                await self._record_stop("campaign_budget_exhausted")
                return
            await self._begin_job(job_id, corpus.benchmark_id, "p12_context_ab")
            started = time.time()
            try:
                with bind_egress_policy(self._egress_policy(corpus)):
                    scored = await self.harness.pipeline.judge.p12_context_ab(
                        benchmark_id=corpus.benchmark_id,
                        provider=judge,
                        candidate_path=candidate_path,
                        pairwise_retries=self.campaign.pairwise_retries,
                        force_external=True,
                    )
            except Exception as exc:
                await self._finish_job(
                    job_id,
                    corpus.benchmark_id,
                    "p12_context_ab",
                    {**benchmark_failure(exc), "writer": writer, "judge": judge},
                    usage={"requests": 0, "elapsed_seconds": time.time() - started},
                    status="failed",
                )
                continue
            analysis = dict(scored.get("analysis") or {})
            scored_usage = dict(scored.get("usage") or {})
            await self._finish_job(
                job_id,
                corpus.benchmark_id,
                "p12_context_ab",
                {
                    "generated_job_id": generation_job_id,
                    "candidate_path": candidate_path,
                    "scored": scored,
                    "analysis": analysis,
                    "writer": writer,
                    "judge": judge,
                },
                usage={
                    "requests": int(scored.get("requests_attempted") or 0),
                    "prompt_tokens": int(scored_usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(scored_usage.get("completion_tokens") or 0),
                    "total_tokens": int(scored_usage.get("total_tokens") or 0),
                    "elapsed_seconds": time.time() - started,
                },
            )

    async def _finish_job(
        self,
        job_id: str,
        benchmark_id: str,
        experiment: str,
        result: Dict[str, Any],
        *,
        usage: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        async with self._write_lock:
            result_path = self.store.save_job_result(job_id, result)
            row = {
                "job_id": job_id,
                "benchmark_id": benchmark_id,
                "experiment": experiment,
                "status": status,
                "result_path": str(result_path),
                "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
                "usage": usage,
                "fingerprints": self._job_fingerprints(benchmark_id, experiment, result=result),
                "completed_at": time.time(),
            }
            self.store.append_jsonl(self.store.jobs_path, row)
            state = self.store.load_state()
            total = CampaignUsage(**dict(state.get("usage") or {}))
            total.add(usage)
            state["usage"] = total.to_dict()
            self.store.save_json(self.store.state_path, state)
            self._collect_failures(result, benchmark_id, experiment, job_id)

    async def _begin_job(self, job_id: str, benchmark_id: str, experiment: str) -> None:
        async with self._write_lock:
            self.store.append_jsonl(
                self.store.jobs_path,
                {
                    "job_id": job_id,
                    "benchmark_id": benchmark_id,
                    "experiment": experiment,
                    "status": "running",
                    "started_at": time.time(),
                },
            )

    def _collect_failures(self, result: Dict[str, Any], benchmark_id: str, experiment: str, job_id: str) -> None:
        candidates: List[Dict[str, Any]] = []
        for container in (result, result.get("analysis") or {}, result.get("generated") or {}):
            failures = container.get("failures") if isinstance(container, dict) else None
            if isinstance(failures, list):
                candidates.extend(item for item in failures if isinstance(item, dict))
        paths = self.harness.pipeline.corpus.paths(benchmark_id)
        artifact_name = {
            "retrieval_ab": "strategy_ab_failures.jsonl",
            "p12_context_ab": "p12_context_failures.jsonl",
        }.get(experiment)
        if artifact_name:
            candidates.extend(read_jsonl(paths.generated_dir / artifact_name))
        existing = {str(row.get("id")) for row in read_jsonl(self.store.failures_path)}
        for failure in candidates:
            failure = classify_benchmark_failure_record(failure)
            failure_id = str(failure.get("id") or stable_fingerprint([job_id, failure])[:20])
            if failure_id in existing:
                continue
            existing.add(failure_id)
            self.store.append_jsonl(
                self.store.failures_path,
                {
                    "id": failure_id,
                    "benchmark_id": benchmark_id,
                    "experiment": experiment,
                    "job_id": job_id,
                    "failure": self._without_content(failure),
                    "contains_corpus_text": False,
                },
            )
            self.store.append_jsonl(
                self.store.replay_cases_path,
                {
                    "id": f"REPLAY-CAMPAIGN-{failure_id}",
                    "campaign_id": self.campaign.id,
                    "benchmark_id": benchmark_id,
                    "experiment": experiment,
                    "source": "campaign_failure",
                    "failure_id": failure_id,
                    "replay_command": [
                        "python",
                        "scripts/eval_campaign.py",
                        "run",
                        "--config",
                        str(self.store.config_path),
                    ],
                    "contains_corpus_text": False,
                },
            )

    def _aggregate(self) -> Dict[str, Any]:
        latest: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
        component_reports: Dict[str, Dict[str, Any]] = {}
        judge_decisions: Dict[tuple[str, str, str], Dict[str, Dict[str, str]]] = {}
        retrieval_groups: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
        for job in self._latest_jobs():
            if job.get("status") != "completed":
                continue
            result = read_json(Path(str(job.get("result_path"))), {}) or {}
            if job.get("experiment") == "suite":
                metrics = result.get("metrics") or {}
                component_reports[str(job.get("benchmark_id"))] = {
                    key: metrics.get(key)
                    for key in (
                        "retrieval",
                        "memory",
                        "compact_fresh",
                        "safety",
                        "timeline_foreshadow_probe",
                        "counterfactual_adherence",
                    )
                    if key in metrics
                }
                continue
            if job.get("experiment") not in {"retrieval_ab", "p12_context_ab"}:
                continue
            if job.get("experiment") == "retrieval_ab":
                key = (
                    "retrieval_ab",
                    str(job.get("benchmark_id") or ""),
                    str(result.get("writer") or ""),
                    str(result.get("judge") or ""),
                )
                group = retrieval_groups.setdefault(
                    key,
                    {"pair_ids": set(), "pairwise_rows": [], "completed_at": 0.0},
                )
                group["pair_ids"].update(str(item) for item in (result.get("pair_ids") or []) if str(item))
                group["completed_at"] = max(
                    float(group.get("completed_at") or 0.0),
                    float(job.get("completed_at") or 0.0),
                )
                score_path_value = str(((result.get("scored") or {}).get("path") or ""))
                score_path = Path(score_path_value) if score_path_value else None
                if score_path is not None and not score_path.is_absolute():
                    score_path = Path(__file__).resolve().parents[2] / score_path
                score_rows = read_jsonl(score_path) if score_path is not None and score_path.is_file() else []
                group["pairwise_rows"].extend(score_rows)
                decisions = {
                    str(item.get("pair_id")): str(item.get("judge_winner"))
                    for item in score_rows
                    if item.get("position_consistent") is True
                    and item.get("judge_winner") in {"A", "B", "tie"}
                    and item.get("pair_id")
                }
                decision_group = judge_decisions.setdefault(
                    ("retrieval_ab", str(job.get("benchmark_id") or ""), str(result.get("writer") or "")),
                    {},
                )
                decision_group.setdefault(str(result.get("judge") or ""), {}).update(decisions)
                continue
            analysis = result.get("analysis") or {}
            pairwise_path_value = str(((result.get("scored") or {}).get("pairwise_path") or ""))
            pairwise_path = Path(pairwise_path_value) if pairwise_path_value else None
            pairwise_rows = read_jsonl(pairwise_path) if pairwise_path is not None and pairwise_path.is_file() else []
            decisions = {
                str(item.get("pair_id")): str(item.get("judge_winner"))
                for item in pairwise_rows
                if item.get("position_consistent") is True
                and item.get("judge_winner") in {"A", "B", "tie"}
                and item.get("pair_id")
            }
            decision_group = judge_decisions.setdefault(
                ("p12_context_ab", str(job.get("benchmark_id") or ""), str(result.get("writer") or "")),
                {},
            )
            decision_group.setdefault(str(result.get("judge") or ""), {}).update(decisions)
            row = {
                "benchmark_id": job.get("benchmark_id"),
                "experiment": job.get("experiment"),
                "writer": result.get("writer"),
                "judge": result.get("judge"),
                "pairs": analysis.get("comparable_pairs") or analysis.get("pairs") or 0,
                "ci": analysis.get("strategy_b_preference_ci95") or {},
                "gate": bool(analysis.get("corpus_gate_passed") or analysis.get("adoption_gate_passed")),
                "comparable_rate": float(analysis.get("comparable_rate") or 0.0),
                "position_consistency": float(analysis.get("position_consistency") or 0.0),
                "first_attempt_comparable_rate": float(analysis.get("first_attempt_comparable_rate") or 0.0),
                "first_attempt_position_consistency": float(
                    analysis.get("first_attempt_position_consistency") or 0.0
                ),
                "judge_human_agreement": analysis.get("judge_human_agreement") or {},
                "completed_at": job.get("completed_at"),
            }
            key = tuple(str(row.get(field) or "") for field in ("experiment", "benchmark_id", "writer", "judge"))
            latest[key] = row
        for key, group in retrieval_groups.items():
            _, benchmark_id, writer, judge = key
            pair_ids = set(group.get("pair_ids") or set())
            paths = self.harness.pipeline.corpus.paths(benchmark_id)
            candidates = [
                row
                for row in read_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl")
                if str(row.get("pair_id") or "") in pair_ids
            ]
            analysis = self.harness.pipeline.statistics.analyze_strategy_ab(
                benchmark_id=benchmark_id,
                candidates=candidates,
                pairwise_rows=list(group.get("pairwise_rows") or []),
            )
            latest[key] = {
                "benchmark_id": benchmark_id,
                "experiment": "retrieval_ab",
                "writer": writer,
                "judge": judge,
                "pairs": analysis.get("comparable_pairs") or 0,
                "ci": analysis.get("strategy_b_preference_ci95") or {},
                "gate": bool(analysis.get("corpus_gate_passed") or analysis.get("adoption_gate_passed")),
                "comparable_rate": float(analysis.get("comparable_rate") or 0.0),
                "position_consistency": float(analysis.get("position_consistency") or 0.0),
                "first_attempt_comparable_rate": float(analysis.get("first_attempt_comparable_rate") or 0.0),
                "first_attempt_position_consistency": float(
                    analysis.get("first_attempt_position_consistency") or 0.0
                ),
                "judge_human_agreement": analysis.get("judge_human_agreement") or {},
                "completed_at": group.get("completed_at"),
            }
        evidence = sorted(latest.values(), key=lambda item: tuple(str(item.get(key) or "") for key in ("experiment", "benchmark_id", "writer", "judge")))
        corpora = {str(item.get("benchmark_id")) for item in evidence if item.get("pairs")}
        writers = {str(item.get("writer")) for item in evidence if item.get("pairs")}
        judges = {str(item.get("judge")) for item in evidence if item.get("pairs")}
        writer_infrastructures = {self._provider_infrastructure(item) for item in writers}
        judge_infrastructures = {self._provider_infrastructure(item) for item in judges}
        provider_groups: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for item in evidence:
            provider_groups.setdefault(
                (str(item.get("experiment")), str(item.get("writer")), str(item.get("judge"))), []
            ).append(item)
        provider_scope_rows = {
            "|".join(key): {
                "corpora": len({str(row.get("benchmark_id")) for row in rows if row.get("pairs")}),
                "passed": len({str(row.get("benchmark_id")) for row in rows if row.get("pairs")}) >= 2
                and all(bool(row.get("gate")) for row in rows),
            }
            for key, rows in provider_groups.items()
        }
        provider_scope = any(row["passed"] for row in provider_scope_rows.values())
        judge_agreement = self._judge_agreement(judge_decisions)
        agreement_ready = bool(judge_agreement) and all(row["passed"] for row in judge_agreement.values())
        judge_calibration = {
            judge: {
                "passed": any(
                    bool((row.get("judge_human_agreement") or {}).get("gate_passed"))
                    for row in evidence
                    if row.get("experiment") == "retrieval_ab" and row.get("judge") == judge
                ),
                "evidence": [
                    row.get("judge_human_agreement")
                    for row in evidence
                    if row.get("experiment") == "retrieval_ab"
                    and row.get("judge") == judge
                    and (row.get("judge_human_agreement") or {}).get("available")
                ],
            }
            for judge in judges
        }
        judge_calibration_ready = bool(judge_calibration) and all(
            row["passed"] for row in judge_calibration.values()
        )
        observed_scope = {
            (
                str(item.get("experiment") or ""),
                str(item.get("benchmark_id") or ""),
                str(item.get("writer") or ""),
                str(item.get("judge") or ""),
            )
            for item in evidence
            if item.get("pairs")
        }
        expected_scope = {
            (experiment, corpus.benchmark_id, writer, judge)
            for corpus in self.campaign.corpora
            for experiment in corpus.enabled_experiments
            if experiment in {"retrieval_ab", "p12_context_ab"}
            for writer in self.campaign.writer_providers
            for judge in self.campaign.judge_providers
        }
        missing_scope = sorted("|".join(item) for item in expected_scope - observed_scope)
        experiment_coverage_ready = bool(expected_scope) and not missing_scope
        global_scope = bool(
            provider_scope_rows
            and len(writer_infrastructures) >= 2
            and len(judge_infrastructures) >= 2
            and all(row["passed"] for row in provider_scope_rows.values())
            and agreement_ready
            and judge_calibration_ready
            and experiment_coverage_ready
        )
        return {
            "jobs": len({str(row.get("job_id")) for row in self.store.jobs() if row.get("job_id")}),
            "usage": self.store.load_state().get("usage") or {},
            "evidence": evidence,
            "component_reports": component_reports,
            "corpora": sorted(corpora),
            "writer_providers": sorted(writers),
            "judge_providers": sorted(judges),
            "writer_infrastructures": sorted(writer_infrastructures),
            "judge_infrastructures": sorted(judge_infrastructures),
            "judge_agreement": judge_agreement,
            "judge_agreement_gate_passed": agreement_ready,
            "judge_human_calibration": judge_calibration,
            "judge_human_calibration_gate_passed": judge_calibration_ready,
            "experiment_coverage_gate_passed": experiment_coverage_ready,
            "missing_experiment_scope": missing_scope,
            "provider_scope_gate_passed": provider_scope,
            "provider_scope": provider_scope_rows,
            "global_scope_gate_passed": global_scope,
            "recommendation": "global_adoption" if global_scope else "retain_current_defaults",
            "failure_count": len(read_jsonl(self.store.failures_path)),
            "failures_by_scope": self._failure_counts_by_scope(),
        }

    def _judge_agreement(
        self,
        grouped: Dict[tuple[str, str, str], Dict[str, Dict[str, str]]],
    ) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for group_key, by_judge in grouped.items():
            for left, right in itertools.combinations(sorted(by_judge), 2):
                common = sorted(set(by_judge[left]) & set(by_judge[right]))
                agreement = (
                    sum(by_judge[left][pair_id] == by_judge[right][pair_id] for pair_id in common) / len(common)
                    if common
                    else 0.0
                )
                key = "|".join((*group_key, left, right))
                result[key] = {
                    "experiment": group_key[0],
                    "benchmark_id": group_key[1],
                    "writer": group_key[2],
                    "judge_a": left,
                    "judge_b": right,
                    "common_pairs": len(common),
                    "agreement": agreement,
                    "passed": len(common) >= self.campaign.stop.min_pairs
                    and agreement >= self.campaign.stop.judge_agreement,
                }
        return result

    @staticmethod
    def _provider_infrastructure(profile_id: str) -> str:
        profile = llm_config_service.get_profile_by_id(str(profile_id)) or {}
        return str(profile.get("provider") or f"profile:{profile_id}")

    def _failure_counts_by_scope(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in read_jsonl(self.store.failures_path):
            failure = classify_benchmark_failure_record(dict(row.get("failure") or {}))
            scope = str(failure.get("failure_scope") or "unknown")
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def _release_manifest(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        repo = Path(__file__).resolve().parents[2]
        revision = self._git(repo, ["rev-parse", "HEAD"])
        dirty = bool(self._git(repo, ["status", "--porcelain"]))
        corpora = []
        for corpus in self.campaign.corpora:
            manifest = read_json(self.harness.pipeline.corpus.paths(corpus.benchmark_id).manifest, {}) or {}
            corpora.append(
                {
                    "benchmark_id": corpus.benchmark_id,
                    "corpus_hash": manifest.get("corpus_hash") or manifest.get("source_sha256"),
                    "manifest_fingerprint": stable_fingerprint(manifest),
                    "data_classification": corpus.data_classification,
                }
            )
        created_at = time.time()
        artifact_paths = [
            self.store.config_path,
            self.store.state_path,
            self.store.jobs_path,
            self.store.failures_path,
            self.store.replay_cases_path,
            *(sorted(self.store.job_dir.glob("*.json")) if self.store.job_dir.exists() else []),
        ]
        artifacts = {
            path.relative_to(self.store.directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifact_paths
            if path.is_file()
        }
        evidence_scope = "global" if summary.get("global_scope_gate_passed") else (
            "provider" if summary.get("provider_scope_gate_passed") else "corpus"
        )
        return {
            "schema_version": 2,
            "campaign_id": self.campaign.id,
            "campaign_fingerprint": self.campaign.fingerprint,
            "code_revision": revision,
            "dirty_worktree": dirty,
            "evidence_scope": evidence_scope,
            "corpora": corpora,
            "writer_providers": self.campaign.writer_providers,
            "judge_providers": self.campaign.judge_providers,
            "writer_infrastructures": summary.get("writer_infrastructures") or [],
            "judge_infrastructures": summary.get("judge_infrastructures") or [],
            "quality_gates": summary,
            "job_ledger_sha256": hashlib.sha256(self.store.jobs_path.read_bytes()).hexdigest()
            if self.store.jobs_path.exists()
            else "",
            "job_fingerprints": {
                str(row.get("job_id")): row.get("fingerprints") or {}
                for row in self.store.jobs()
                if row.get("status") == "completed"
            },
            "artifacts": artifacts,
            "created_at": created_at,
            "expires_at": created_at + 30 * 86400,
        }

    def _scene_ids(self, corpus: Any) -> List[str]:
        if corpus.scene_ids:
            return list(dict.fromkeys(corpus.scene_ids))
        paths = self.harness.pipeline.corpus.paths(corpus.benchmark_id)
        rows = read_jsonl(paths.generated_dir / "candidate_scene_briefs.jsonl")
        return [str(row.get("id")) for row in rows if row.get("id")]

    def _enforce_privacy(self, corpus: Any) -> None:
        if not corpus.allow_external_api:
            raise PermissionError(f"external_api_not_approved:{corpus.benchmark_id}")
        if corpus.data_classification == "private" and not self.campaign.privacy.allow_private_egress:
            raise PermissionError(f"private_egress_not_approved:{corpus.benchmark_id}")
        manifest = read_json(self.harness.pipeline.corpus.paths(corpus.benchmark_id).manifest, {}) or {}
        if not manifest.get("allow_external_api"):
            raise PermissionError(f"benchmark_manifest_external_api_disabled:{corpus.benchmark_id}")

    async def _should_skip(self, job_id: str) -> bool:
        latest = next((row for row in reversed(self.store.jobs()) if str(row.get("job_id") or "") == job_id), None)
        status = str((latest or {}).get("status") or "")
        if status == "completed" and latest is not None:
            return self._completed_job_is_current(latest)
        if status == "skipped":
            return True
        if status == "running":
            await self._record_stop(f"uncertain_job_requires_manual_resolution:{job_id}")
            return True
        if status == "failed" and not self.campaign.retry_provider_errors:
            return True
        return False

    def _latest_jobs(self) -> List[Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in self.store.jobs():
            job_id = str(row.get("job_id") or "")
            if job_id:
                latest[job_id] = row
        return list(latest.values())

    def _completed_job_is_current(self, row: Dict[str, Any]) -> bool:
        result_path = Path(str(row.get("result_path") or ""))
        if not result_path.is_file():
            return False
        expected_sha256 = str(row.get("result_sha256") or "")
        if not expected_sha256 or hashlib.sha256(result_path.read_bytes()).hexdigest() != expected_sha256:
            return False
        result = read_json(result_path, {}) or {}
        current = self._job_fingerprints(
            str(row.get("benchmark_id") or ""),
            str(row.get("experiment") or ""),
            result=result,
        )
        return bool(row.get("fingerprints")) and row.get("fingerprints") == current

    def _budget_exhausted(self, next_requests: int, next_tokens: int) -> bool:
        state = self.store.load_state()
        usage = dict(state.get("usage") or {})
        elapsed = time.time() - float(state.get("started_at") or time.time())
        return bool(
            int(usage.get("requests") or 0) + next_requests > self.campaign.budget.max_requests
            or int(usage.get("total_tokens") or 0) + next_tokens > self.campaign.budget.max_tokens
            or elapsed > self.campaign.budget.max_elapsed_seconds
        )

    def _sequential_stop(self, analysis: Dict[str, Any]) -> str:
        pairs = int(analysis.get("comparable_pairs") or 0)
        scenes = int(analysis.get("independent_scenes") or 0)
        rate = float(analysis.get("comparable_rate") or 0.0)
        position_consistency = float(analysis.get("position_consistency") or 0.0)
        ci = analysis.get("strategy_b_preference_ci95") or {}
        lower, upper = float(ci.get("lower") or 0.0), float(ci.get("upper") or 1.0)
        if pairs < self.campaign.stop.min_pairs or scenes < self.campaign.stop.min_scenes:
            return ""
        if rate < self.campaign.stop.comparable_rate:
            return "low_comparable_rate"
        if position_consistency < self.campaign.stop.position_consistency:
            return "low_position_consistency"
        if lower > self.campaign.stop.win_ci_lower:
            return "decisive_win"
        if upper < self.campaign.stop.loss_ci_upper:
            return "decisive_loss"
        if upper - lower <= self.campaign.stop.futility_ci_width and lower <= 0.5 <= upper:
            return "futility"
        return ""

    async def _record_stop(self, reason: str) -> None:
        async with self._write_lock:
            state = self.store.load_state()
            reasons = list(state.get("stop_reasons") or [])
            if reason not in reasons:
                reasons.append(reason)
            state["stop_reasons"] = reasons
            self.store.save_json(self.store.state_path, state)

    def _job_fingerprints(
        self,
        benchmark_id: str,
        experiment: str,
        *,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        paths = self.harness.pipeline.corpus.paths(benchmark_id)
        result = result or {}
        if experiment in {"retrieval_ab", "retrieval_ab_generation"}:
            candidates = read_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl")
            pair_ids = {
                str(item)
                for item in (
                    result.get("pair_ids")
                    or ((result.get("generated") or {}).get("pair_ids") or [])
                )
                if str(item)
            }
            if pair_ids:
                candidates = [row for row in candidates if str(row.get("pair_id") or "") in pair_ids]
            candidate_fingerprints = sorted(
                stable_fingerprint(
                    {
                        "id": row.get("id"),
                        "pair_id": row.get("pair_id"),
                        "candidate_sha256": hashlib.sha256(
                            str(row.get("candidate_text") or row.get("chapter_text") or "").encode("utf-8")
                        ).hexdigest(),
                        "prompt_version": row.get("prompt_version"),
                        "retrieval_execution": row.get("retrieval_execution") or {},
                        "writer_provider": row.get("writer_provider"),
                        "writer_model": row.get("writer_model"),
                    }
                )
                for row in candidates
            )
            base = {
                "prompt_versions": sorted(
                    {str(row.get("prompt_version")) for row in candidates if row.get("prompt_version")}
                ),
                "context_fingerprints": sorted(
                    {
                        str((row.get("retrieval_execution") or {}).get("execution_signature"))
                        for row in candidates
                        if (row.get("retrieval_execution") or {}).get("execution_signature")
                    }
                ),
                "candidate_fingerprints": candidate_fingerprints,
                "tool_fingerprint": "not_applicable_no_tools",
            }
            if experiment == "retrieval_ab_generation":
                return base
            expected_pairs = {
                pair["pair_id"]: pair["pair_fingerprint"]
                for pair in LongformBenchmarkHarness._strategy_ab_pairs(candidates)
            }
            score_path_value = str(((result.get("scored") or {}).get("path") or ""))
            score_path = Path(score_path_value) if score_path_value else None
            pairwise = read_jsonl(score_path) if score_path is not None and score_path.is_file() else []
            return {
                **base,
                "expected_pair_fingerprints": sorted(expected_pairs.values()),
                "pair_fingerprints": sorted(
                    {str(row.get("pair_fingerprint")) for row in pairwise if row.get("pair_fingerprint")}
                ),
            }
        if experiment in {"p12_context_generation", "p12_context_ab"}:
            candidate_path_value = str(
                result.get("candidate_path") or ((result.get("generated") or {}).get("candidate_path") or "")
            )
            pairwise_path_value = str(((result.get("scored") or {}).get("pairwise_path") or ""))
            candidates = read_jsonl(Path(candidate_path_value)) if candidate_path_value else []
            pairwise = read_jsonl(Path(pairwise_path_value)) if pairwise_path_value else []
            candidate_fingerprints = sorted(
                stable_fingerprint(
                    {
                        "id": row.get("id"),
                        "pair_id": row.get("pair_id"),
                        "candidate_sha256": row.get("candidate_sha256"),
                        "context_fingerprint": row.get("context_fingerprint"),
                        "prompt_version": row.get("prompt_version"),
                        "writer_provider": row.get("writer_provider"),
                        "writer_model": row.get("writer_model"),
                    }
                )
                for row in candidates
            )
            base = {
                "prompt_versions": sorted(
                    {str(row.get("prompt_version")) for row in candidates if row.get("prompt_version")}
                ),
                "context_fingerprints": sorted(
                    {str(row.get("context_fingerprint")) for row in candidates if row.get("context_fingerprint")}
                ),
                "candidate_fingerprints": candidate_fingerprints,
                "tool_fingerprint": "not_applicable_no_tools",
            }
            if experiment == "p12_context_generation":
                return base
            candidates_by_pair: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for candidate in candidates:
                candidates_by_pair.setdefault(str(candidate.get("pair_id") or ""), {})[
                    str(candidate.get("strategy_role") or "")
                ] = candidate
            expected_pair_fingerprints = []
            for row in pairwise:
                roles = candidates_by_pair.get(str(row.get("pair_id") or ""), {})
                first, second = roles.get("A"), roles.get("B")
                if not first or not second:
                    continue
                expected_pair_fingerprints.append(
                    p12_pair_fingerprint(
                        {
                            **row,
                            "context_fingerprint_a": first.get("context_fingerprint"),
                            "context_fingerprint_b": second.get("context_fingerprint"),
                            "candidate_a_sha256": first.get("candidate_sha256"),
                            "candidate_b_sha256": second.get("candidate_sha256"),
                            "writer_provider": first.get("writer_provider"),
                            "writer_model": first.get("writer_model"),
                            "prompt_version": first.get("prompt_version"),
                            "judge_prompt_version": POINTWISE_PAIR_JUDGE_PROMPT_VERSION,
                        }
                    )
                )
            return {
                **base,
                "expected_pair_fingerprints": sorted(expected_pair_fingerprints),
                "pair_fingerprints": sorted(
                    {str(row.get("pair_fingerprint")) for row in pairwise if row.get("pair_fingerprint")}
                ),
            }
        return {"tool_fingerprint": "not_applicable_no_tools"}

    def _without_content(self, value: Any) -> Any:
        fields = set(self.campaign.privacy.redact_fields)
        if isinstance(value, dict):
            return {key: ("[REDACTED]" if key in fields else self._without_content(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._without_content(item) for item in value]
        return value

    @staticmethod
    def _git(repo: Path, args: Iterable[str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=repo, check=False, capture_output=True, text=True, encoding="utf-8"
            )
        except OSError:
            return ""
        return completed.stdout.strip()
