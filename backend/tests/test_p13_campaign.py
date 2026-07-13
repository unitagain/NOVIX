"""P13 resumable campaign, privacy and release-evidence contracts."""

from __future__ import annotations

import asyncio

import pytest

from app.eval.campaign_models import EvalCampaign, campaign_job_id
from app.eval.campaign_privacy import export_p12_cases_from_traces
from app.eval.campaign_runner import CampaignRunner
from app.eval.longform_artifacts import BenchmarkPaths, read_jsonl, write_json, write_jsonl
from app.eval.longform_benchmark import LongformBenchmarkHarness
from app.eval.longform_pipeline import LongformBenchmarkPipeline


def _campaign(**overrides):
    value = {
        "id": "campaign-test",
        "corpora": [
            {
                "benchmark_id": "demo",
                "enabled_experiments": ["suite", "retrieval_ab", "p12_context_ab"],
                "scene_ids": ["s1", "s2"],
                "data_classification": "private",
                "allow_external_api": True,
            }
        ],
        "writer_providers": ["writer-a"],
        "judge_providers": ["judge-a"],
        "trials": 1,
        "budget": {"max_requests": 100, "max_tokens": 100000, "batch_scenes": 1},
        "privacy": {"allow_private_egress": True},
        "stop": {"min_pairs": 1, "min_scenes": 1, "win_ci_lower": 0.55},
    }
    value.update(overrides)
    return EvalCampaign.from_dict(value)


class FakeHarness:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.pipeline = LongformBenchmarkPipeline.build(self)

    def paths(self, benchmark_id):
        return BenchmarkPaths(self.root, benchmark_id)

    async def run_suite(self, **kwargs):
        self.calls.append(("suite", kwargs["benchmark_id"]))
        return {"success": True, "metrics": {"retrieval": {"recall": 1.0}}}

    async def generate_strategy_ab(self, **kwargs):
        self.calls.append(("generate", tuple(kwargs["scene_ids"])))
        pair_ids = [f"pair-{scene_id}-{kwargs['provider']}" for scene_id in kwargs["scene_ids"]]
        return {
            "success": True,
            "generated_pairs": 1,
            "generated_candidates": 2,
            "pair_ids": pair_ids,
            "requests_attempted": 2,
            "usage_tokens": 100,
            "usage": {"prompt_tokens": 40, "completion_tokens": 60, "total_tokens": 100},
        }

    async def score_strategy_ab(self, **kwargs):
        pair_ids = list(kwargs["pair_ids"])
        self.calls.append(("score", tuple(pair_ids), kwargs["provider"]))
        path = self.root / f"judge-{kwargs['provider']}-{'-'.join(pair_ids)}.jsonl"
        write_jsonl(
            path,
            [
                {
                    "pair_id": pair_id,
                    "position_consistent": True,
                    "judge_winner": "B",
                }
                for pair_id in pair_ids
            ],
        )
        return {
            "success": True,
            "scored_pairs": 1,
            "requests_attempted": 2,
            "usage_tokens": 50,
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            "path": str(path),
            "analysis": {
                "comparable_pairs": 1,
                "independent_scenes": 1,
                "comparable_rate": 1.0,
                "position_consistency": 1.0,
                "first_attempt_comparable_rate": 1.0,
                "first_attempt_position_consistency": 1.0,
                "strategy_b_preference_ci95": {"lower": 0.7, "upper": 0.9},
                "adoption_gate_passed": True,
            },
        }

    def analyze_strategy_ab(self, **kwargs):
        rows = list(kwargs["pairwise_rows"])
        return {
            "comparable_pairs": len(rows),
            "independent_scenes": len(rows),
            "comparable_rate": 1.0,
            "position_consistency": 1.0,
            "first_attempt_comparable_rate": 1.0,
            "first_attempt_position_consistency": 1.0,
            "strategy_b_preference_ci95": {"lower": 0.7, "upper": 0.9},
            "adoption_gate_passed": True,
        }

    async def generate_p12_context_ab(self, **kwargs):
        self.calls.append(("generate-p12", kwargs["provider"]))
        path = self.root / f"p12-{kwargs['provider']}.jsonl"
        write_jsonl(path, [{"pair_id": f"p12-{kwargs['provider']}", "writer_provider": kwargs["provider"]}])
        return {
            "success": True,
            "pairs": 1,
            "candidate_path": str(path),
            "requests_attempted": 2,
            "usage": {"prompt_tokens": 40, "completion_tokens": 60, "total_tokens": 100},
        }

    async def score_p12_context_ab(self, **kwargs):
        self.calls.append(("score-p12", kwargs["provider"], kwargs["candidate_path"]))
        path = self.root / f"p12-judge-{kwargs['provider']}.jsonl"
        write_jsonl(
            path,
            [
                {
                    "pair_id": "p12",
                    "judge_provider": kwargs["provider"],
                    "judge_winner": "B",
                    "position_consistent": True,
                }
            ],
        )
        return {
            "success": True,
            "pairwise_path": str(path),
            "requests_attempted": 2,
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            "analysis": {
                "pairs": 1,
                "comparable_pairs": 1,
                "independent_scenes": 1,
                "comparable_rate": 1.0,
                "strategy_b_preference_ci95": {"lower": 0.7, "upper": 0.9},
                "adoption_gate_passed": True,
                "failures": [],
            },
        }


def _prepare(root):
    paths = BenchmarkPaths(root, "demo")
    write_json(
        paths.manifest,
        {"benchmark_id": "demo", "allow_external_api": True, "corpus_hash": "corpus-hash"},
    )
    write_jsonl(paths.generated_dir / "candidate_scene_briefs.jsonl", [{"id": "s1"}, {"id": "s2"}])
    write_jsonl(
        paths.generated_dir / "p12_context_cases.jsonl",
        [
            {
                "pair_id": "p1",
                "scene_id": "s1",
                "variants": {"memory_off": {}, "memory_on": {"memory": ["x"]}},
            }
        ],
    )


def test_campaign_resume_budget_and_manifest(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    harness = FakeHarness(root)
    campaign = _campaign()
    first = asyncio.run(CampaignRunner(root, campaign, harness=harness).run())
    assert first["success"] is True
    assert first["manifest"]["campaign_fingerprint"] == campaign.fingerprint
    assert first["manifest"]["schema_version"] == 2
    assert first["manifest"]["artifacts"]["config.json"]
    assert first["manifest"]["expires_at"] > first["manifest"]["created_at"]
    first_call_count = len(harness.calls)
    second = asyncio.run(CampaignRunner(root, campaign, harness=harness).run())
    assert second["success"] is True
    assert len(harness.calls) == first_call_count
    jobs = read_jsonl(root / "campaigns" / campaign.id / "jobs.jsonl")
    completed = [row for row in jobs if row["status"] == "completed"]
    retrieval_jobs = [row for row in completed if row["experiment"] == "retrieval_ab"]
    generation_jobs = [row for row in completed if row["experiment"] == "retrieval_ab_generation"]
    assert all(row["usage"]["requests"] == 2 for row in retrieval_jobs)
    assert all(row["usage"]["prompt_tokens"] == 30 for row in retrieval_jobs)
    assert all(row["usage"]["completion_tokens"] == 20 for row in retrieval_jobs)
    assert all(row["usage"]["requests"] == 2 for row in generation_jobs)
    assert len({row["job_id"] for row in completed}) == len(completed)
    assert all(any(item["job_id"] == row["job_id"] and item["status"] == "running" for item in jobs) for row in completed)
    assert any("decisive_win" in reason for reason in second["summary"].get("evidence", []) or []) is False


def test_campaign_config_change_is_rejected(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    asyncio.run(CampaignRunner(root, _campaign(), harness=FakeHarness(root)).run())
    changed = _campaign(trials=2)
    with pytest.raises(ValueError, match="campaign_config_changed"):
        asyncio.run(CampaignRunner(root, changed, harness=FakeHarness(root)).run())


def test_private_egress_requires_explicit_policy(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    campaign = _campaign(privacy={"allow_private_egress": False})
    with pytest.raises(PermissionError, match="private_egress_not_approved"):
        asyncio.run(CampaignRunner(root, campaign, harness=FakeHarness(root)).run())


def test_corpus_manifest_deny_overrides_campaign_egress_approval(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    paths = BenchmarkPaths(root, "demo")
    write_json(
        paths.manifest,
        {
            "benchmark_id": "demo",
            "allow_external_api": False,
            "data_classification": "local_only",
            "corpus_hash": "corpus-hash",
        },
    )
    with pytest.raises(PermissionError, match="benchmark_manifest_external_api_disabled"):
        asyncio.run(CampaignRunner(root, _campaign(), harness=FakeHarness(root)).run())


def test_trace_export_only_accepts_explicit_reviewed_case(tmp_path):
    trace = tmp_path / "trace.json"
    write_json(
        trace,
        {
            "events": [
                {"type": "llm_request", "data": {"messages": ["private"]}},
                {
                    "type": "p12_eval_case",
                    "data": {
                        "pair_id": "p1",
                        "variants": {"memory_off": {"ids": []}, "memory_on": {"ids": ["m1"]}},
                    },
                },
            ]
        },
    )
    output = tmp_path / "cases.jsonl"
    result = export_p12_cases_from_traces([trace], output_path=output, allow_content=False, redact_fields=["content"])
    assert result["exported"] == 1
    rows = read_jsonl(output)
    assert rows[0]["pair_id"] == "p1"
    assert rows[0]["source_trace_sha256"]


def test_campaign_job_id_is_stable():
    assert campaign_job_id("a", "b") == campaign_job_id("a", "b")
    assert campaign_job_id("a", "b") != campaign_job_id("b", "a")


def test_uncertain_running_job_is_not_reissued(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    campaign = _campaign(
        corpora=[
            {
                "benchmark_id": "demo",
                "enabled_experiments": ["retrieval_ab"],
                "scene_ids": ["s1"],
                "data_classification": "private",
                "allow_external_api": True,
            }
        ]
    )
    harness = FakeHarness(root)
    runner = CampaignRunner(root, campaign, harness=harness)
    runner._initialize_state()
    job_id = campaign_job_id(campaign.id, "demo", "retrieval_ab_generation", "writer-a", "s1")
    runner.store.append_jsonl(
        runner.store.jobs_path,
        {
            "job_id": job_id,
            "benchmark_id": "demo",
            "experiment": "retrieval_ab_generation",
            "status": "running",
        },
    )
    result = asyncio.run(runner.run())
    assert harness.calls == []
    state = result["summary"]
    assert state["jobs"] == 1
    persisted = runner.store.load_state()
    assert any(str(reason).startswith("uncertain_job_requires_manual_resolution") for reason in persisted["stop_reasons"])


def test_same_campaign_cannot_run_concurrently(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    campaign = _campaign(
        corpora=[
            {
                "benchmark_id": "demo",
                "enabled_experiments": ["suite"],
                "data_classification": "private",
                "allow_external_api": True,
            }
        ]
    )

    class SlowHarness(FakeHarness):
        async def run_suite(self, **kwargs):
            await asyncio.sleep(1.2)
            return await super().run_suite(**kwargs)

    async def scenario():
        first = CampaignRunner(root, campaign, harness=SlowHarness(root)).run()
        second = CampaignRunner(root, campaign, harness=SlowHarness(root)).run()
        return await asyncio.gather(first, second, return_exceptions=True)

    results = asyncio.run(scenario())
    assert sum(isinstance(item, TimeoutError) for item in results) == 1
    assert sum(isinstance(item, dict) and item.get("success") for item in results) == 1


def test_multiple_judges_share_one_frozen_generation(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    harness = FakeHarness(root)
    campaign = _campaign(
        judge_providers=["judge-a", "judge-b"],
        stop={
            "min_pairs": 1,
            "min_scenes": 1,
            "win_ci_lower": 1.0,
            "judge_agreement": 0.8,
        },
    )

    result = asyncio.run(CampaignRunner(root, campaign, harness=harness).run())

    generate_calls = [call for call in harness.calls if call[0] == "generate"]
    score_calls = [call for call in harness.calls if call[0] == "score"]
    p12_generate_calls = [call for call in harness.calls if call[0] == "generate-p12"]
    p12_score_calls = [call for call in harness.calls if call[0] == "score-p12"]
    assert len(generate_calls) == 2
    assert len(score_calls) == 4
    by_pair_ids = {}
    for _, pair_ids, judge in score_calls:
        by_pair_ids.setdefault(pair_ids, set()).add(judge)
    assert all(judges == {"judge-a", "judge-b"} for judges in by_pair_ids.values())
    assert len(p12_generate_calls) == 1
    assert len(p12_score_calls) == 2
    assert len({call[2] for call in p12_score_calls}) == 1
    assert result["summary"]["judge_agreement_gate_passed"] is True
    assert any(key.startswith("p12_context_ab|") for key in result["summary"]["judge_agreement"])


def test_provider_infrastructure_uses_underlying_profile_provider(monkeypatch):
    profiles = {
        "qwen": {"provider": "aistudio"},
        "glm": {"provider": "aistudio"},
        "deepseek": {"provider": "deepseek"},
    }
    monkeypatch.setattr(
        "app.eval.campaign_runner.llm_config_service.get_profile_by_id",
        lambda profile_id: profiles.get(profile_id),
    )

    assert CampaignRunner._provider_infrastructure("qwen") == "aistudio"
    assert CampaignRunner._provider_infrastructure("glm") == "aistudio"
    assert CampaignRunner._provider_infrastructure("deepseek") == "deepseek"


def test_completed_judge_job_is_invalidated_when_pair_fingerprint_changes(tmp_path):
    root = tmp_path / "benchmarks"
    _prepare(root)
    harness = FakeHarness(root)
    runner = CampaignRunner(root, _campaign(), harness=harness)
    runner._initialize_state()
    paths = BenchmarkPaths(root, "demo")
    pair_id = "pair-s1-writer-a"
    first = {
        "id": f"{pair_id}-A",
        "pair_id": pair_id,
        "chapter_id": "c1",
        "scene_id": "s1",
        "strategy_role": "A",
        "retrieval_strategy": "bm25",
        "candidate_text": "候选 A",
        "reference_excerpt": "原始续文",
        "prompt_version": "writer-v1",
        "retrieval_execution": {"execution_signature": "exec-a"},
        "writer_provider": "writer-a",
        "writer_model": "writer-model",
    }
    second = {
        **first,
        "id": f"{pair_id}-B",
        "strategy_role": "B",
        "retrieval_strategy": "jit_hybrid",
        "candidate_text": "候选 B",
        "retrieval_execution": {"execution_signature": "exec-b"},
    }
    write_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl", [first, second])
    fingerprint = LongformBenchmarkHarness._strategy_ab_pair_fingerprint(first, second)
    score_path = paths.generated_dir / "score.jsonl"
    write_jsonl(
        score_path,
        [
            {
                "pair_id": pair_id,
                "pair_fingerprint": fingerprint,
                "judge_winner": "B",
                "position_consistent": True,
            }
        ],
    )
    job_id = campaign_job_id("campaign-test", "demo", "retrieval_ab", "writer-a", "judge-a", pair_id)
    asyncio.run(
        runner._finish_job(
            job_id,
            "demo",
            "retrieval_ab",
            {
                "pair_ids": [pair_id],
                "writer": "writer-a",
                "judge": "judge-a",
                "scored": {"path": str(score_path)},
            },
            usage={},
        )
    )
    completed = runner.store.jobs()[-1]
    assert runner._completed_job_is_current(completed) is True

    write_jsonl(
        paths.generated_dir / "strategy_ab_candidates.jsonl",
        [{**first, "reference_excerpt": "更新后的 held-out 续文"}, second],
    )

    assert runner._completed_job_is_current(completed) is False
