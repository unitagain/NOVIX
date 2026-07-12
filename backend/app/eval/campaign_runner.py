"""Resumable, budgeted real-API campaign orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.eval.campaign_models import CampaignUsage, EvalCampaign, campaign_job_id, stable_fingerprint
from app.eval.campaign_store import CampaignStore
from app.eval.longform_artifacts import read_json, read_jsonl
from app.eval.longform_benchmark import LongformBenchmarkHarness
from app.storage.file_lock import get_file_lock
from app.security.egress_context import EgressPolicy, bind_egress_policy
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
                for judge in self.campaign.judge_providers:
                    await self._run_retrieval_batches(corpus, scene_ids, writer, judge)
        if "p12_context_ab" in corpus.enabled_experiments:
            for writer in self.campaign.writer_providers:
                for judge in self.campaign.judge_providers:
                    await self._run_p12_job(corpus, writer, judge)

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

    async def _run_retrieval_batches(self, corpus: Any, scene_ids: List[str], writer: str, judge: str) -> None:
        stop_prefix = f"{corpus.benchmark_id}:{writer}:{judge}:"
        if any(str(reason).startswith(stop_prefix) for reason in self.store.load_state().get("stop_reasons") or []):
            return
        size = max(1, self.campaign.budget.batch_scenes)
        for offset in range(0, len(scene_ids), size):
            batch = scene_ids[offset : offset + size]
            job_id = campaign_job_id(
                self.campaign.id, corpus.benchmark_id, "retrieval_ab", writer, judge, ",".join(batch)
            )
            if await self._should_skip(job_id):
                continue
            estimated_requests = len(batch) * self.campaign.trials * 4
            if self._budget_exhausted(estimated_requests, estimated_requests * 4000):
                await self._record_stop("campaign_budget_exhausted")
                return
            await self._begin_job(job_id, corpus.benchmark_id, "retrieval_ab")
            started = time.time()
            try:
                with bind_egress_policy(
                    EgressPolicy(
                        corpus_id=corpus.benchmark_id,
                        data_classification=corpus.data_classification,
                        authorized=corpus.allow_external_api,
                    )
                ):
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
                    scored = await self.harness.pipeline.judge.strategy_ab(
                    benchmark_id=corpus.benchmark_id,
                    provider=judge,
                    require_judge=False,
                    pairwise_retries=1 if self.campaign.retry_provider_errors else 0,
                    scene_ids=batch,
                    append_latest=True,
                    force_external=True,
                )
            except Exception as exc:
                failure = benchmark_failure(exc)
                await self._finish_job(
                    job_id,
                    corpus.benchmark_id,
                    "retrieval_ab",
                    {**failure, "writer": writer, "judge": judge},
                    usage={"requests": 0, "elapsed_seconds": time.time() - started},
                    status="failed",
                )
                return
            analysis = dict(scored.get("analysis") or {})
            result = {"generated": generated, "scored": scored, "analysis": analysis, "writer": writer, "judge": judge}
            generated_usage = dict(generated.get("usage") or {})
            judge_usage = dict(scored.get("usage") or {})
            actual_pairs = int(scored.get("scored_pairs") or 0)
            await self._finish_job(
                job_id,
                corpus.benchmark_id,
                "retrieval_ab",
                result,
                usage={
                    "requests": int(generated.get("requests_attempted") or 0)
                    + int(scored.get("requests_attempted") or actual_pairs * 2),
                    "prompt_tokens": int(generated_usage.get("prompt_tokens") or 0)
                    + int(judge_usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(generated_usage.get("completion_tokens") or 0)
                    + int(judge_usage.get("completion_tokens") or 0),
                    "total_tokens": int(generated_usage.get("total_tokens") or 0)
                    + int(judge_usage.get("total_tokens") or 0),
                    "elapsed_seconds": time.time() - started,
                },
            )
            reason = self._sequential_stop(analysis)
            if reason:
                await self._record_stop(f"{corpus.benchmark_id}:{writer}:{judge}:{reason}")
                return

    async def _run_p12_job(self, corpus: Any, writer: str, judge: str) -> None:
        job_id = campaign_job_id(self.campaign.id, corpus.benchmark_id, "p12_context_ab", writer, judge)
        if await self._should_skip(job_id):
            return
        paths = self.harness.pipeline.corpus.paths(corpus.benchmark_id)
        cases = read_jsonl(paths.generated_dir / "p12_context_cases.jsonl")
        estimated_requests = len(cases) * 4
        if not cases:
            await self._begin_job(job_id, corpus.benchmark_id, "p12_context_ab")
            await self._finish_job(
                job_id,
                corpus.benchmark_id,
                "p12_context_ab",
                {"success": False, "reason": "missing_p12_context_cases", "writer": writer, "judge": judge},
                usage={},
                status="skipped",
            )
            return
        if self._budget_exhausted(estimated_requests, estimated_requests * 4000):
            await self._record_stop("campaign_budget_exhausted")
            return
        await self._begin_job(job_id, corpus.benchmark_id, "p12_context_ab")
        started = time.time()
        try:
            with bind_egress_policy(
                EgressPolicy(
                    corpus_id=corpus.benchmark_id,
                    data_classification=corpus.data_classification,
                    authorized=corpus.allow_external_api,
                )
            ):
                generated = await self.harness.pipeline.generation.p12_context_ab(
                    benchmark_id=corpus.benchmark_id, provider=writer, force_external=True
                )
                scored = await self.harness.pipeline.judge.p12_context_ab(
                    benchmark_id=corpus.benchmark_id, provider=judge, force_external=True
                )
        except Exception as exc:
            failure = benchmark_failure(exc)
            await self._finish_job(
                job_id,
                corpus.benchmark_id,
                "p12_context_ab",
                {**failure, "writer": writer, "judge": judge},
                usage={"requests": 0, "elapsed_seconds": time.time() - started},
                status="failed",
            )
            return
        analysis = dict(scored.get("analysis") or {})
        usage = self._p12_usage(paths, writer, judge)
        usage["elapsed_seconds"] = time.time() - started
        await self._finish_job(
            job_id,
            corpus.benchmark_id,
            "p12_context_ab",
            {"generated": generated, "scored": scored, "analysis": analysis, "writer": writer, "judge": judge},
            usage=usage,
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
                "fingerprints": self._job_fingerprints(benchmark_id, experiment),
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
        for job in self.store.jobs():
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
            analysis = result.get("analysis") or {}
            row = {
                "benchmark_id": job.get("benchmark_id"),
                "experiment": job.get("experiment"),
                "writer": result.get("writer"),
                "judge": result.get("judge"),
                "pairs": analysis.get("comparable_pairs") or analysis.get("pairs") or 0,
                "ci": analysis.get("strategy_b_preference_ci95") or {},
                "gate": bool(analysis.get("adoption_gate_passed")),
                "completed_at": job.get("completed_at"),
            }
            key = tuple(str(row.get(field) or "") for field in ("experiment", "benchmark_id", "writer", "judge"))
            latest[key] = row
        evidence = sorted(latest.values(), key=lambda item: tuple(str(item.get(key) or "") for key in ("experiment", "benchmark_id", "writer", "judge")))
        corpora = {str(item.get("benchmark_id")) for item in evidence if item.get("pairs")}
        writers = {str(item.get("writer")) for item in evidence if item.get("pairs")}
        judges = {str(item.get("judge")) for item in evidence if item.get("pairs")}
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
        global_scope = bool(
            provider_scope_rows
            and len(writers) >= 2
            and len(judges) >= 2
            and all(row["passed"] for row in provider_scope_rows.values())
        )
        return {
            "jobs": len({str(row.get("job_id")) for row in self.store.jobs() if row.get("job_id")}),
            "usage": self.store.load_state().get("usage") or {},
            "evidence": evidence,
            "component_reports": component_reports,
            "corpora": sorted(corpora),
            "writer_providers": sorted(writers),
            "judge_providers": sorted(judges),
            "provider_scope_gate_passed": provider_scope,
            "provider_scope": provider_scope_rows,
            "global_scope_gate_passed": global_scope,
            "recommendation": "global_adoption" if global_scope else "retain_current_defaults",
            "failure_count": len(read_jsonl(self.store.failures_path)),
            "failures_by_scope": self._failure_counts_by_scope(),
        }

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
        status = self.store.latest_job_statuses().get(job_id)
        if status in {"completed", "skipped"}:
            return True
        if status == "running":
            await self._record_stop(f"uncertain_job_requires_manual_resolution:{job_id}")
            return True
        if status == "failed" and not self.campaign.retry_provider_errors:
            return True
        return False

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
        ci = analysis.get("strategy_b_preference_ci95") or {}
        lower, upper = float(ci.get("lower") or 0.0), float(ci.get("upper") or 1.0)
        if pairs < self.campaign.stop.min_pairs or scenes < self.campaign.stop.min_scenes:
            return ""
        if rate < self.campaign.stop.comparable_rate:
            return "low_comparable_rate"
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

    @staticmethod
    def _p12_usage(paths: Any, writer: str, judge: str) -> Dict[str, Any]:
        candidates = read_jsonl(paths.generated_dir / "p12_context_candidates.jsonl")
        pairwise = read_jsonl(paths.generated_dir / "p12_context_pairwise.jsonl")
        usages = [row.get("gateway_usage") or {} for row in candidates if row.get("writer_provider") == writer]
        judge_usages = [row.get("judge_usage") or {} for row in pairwise if row.get("judge_provider") == judge]
        all_usage = usages + judge_usages
        return {
            "requests": len(candidates) + len(pairwise) * 2,
            "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in all_usage),
            "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in all_usage),
            "total_tokens": sum(int(item.get("total_tokens") or 0) for item in all_usage),
        }

    def _job_fingerprints(self, benchmark_id: str, experiment: str) -> Dict[str, Any]:
        paths = self.harness.pipeline.corpus.paths(benchmark_id)
        if experiment == "retrieval_ab":
            candidates = read_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl")
            pairwise_files = sorted(paths.generated_dir.glob("strategy_ab_pairwise_judge_*.jsonl"))
            pairwise = read_jsonl(pairwise_files[-1]) if pairwise_files else []
            return {
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
                "pair_fingerprints": sorted(
                    {str(row.get("pair_fingerprint")) for row in pairwise if row.get("pair_fingerprint")}
                ),
                "tool_fingerprint": "not_applicable_no_tools",
            }
        if experiment == "p12_context_ab":
            candidates = read_jsonl(paths.generated_dir / "p12_context_candidates.jsonl")
            pairwise = read_jsonl(paths.generated_dir / "p12_context_pairwise.jsonl")
            return {
                "prompt_versions": sorted(
                    {str(row.get("prompt_version")) for row in candidates if row.get("prompt_version")}
                ),
                "context_fingerprints": sorted(
                    {str(row.get("context_fingerprint")) for row in candidates if row.get("context_fingerprint")}
                ),
                "pair_fingerprints": sorted(
                    {str(row.get("pair_fingerprint")) for row in pairwise if row.get("pair_fingerprint")}
                ),
                "tool_fingerprint": "not_applicable_no_tools",
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
