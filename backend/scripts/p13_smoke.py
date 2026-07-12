"""Real-API smoke for the resumable P13 campaign control plane."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.eval.campaign_models import EvalCampaign
from app.eval.campaign_runner import CampaignRunner
from app.eval.longform_artifacts import read_jsonl, write_jsonl
from app.eval.longform_benchmark import LongformBenchmarkHarness


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wenshape-p13-") as directory:
        root = Path(directory) / "benchmarks"
        source = Path(directory) / "synthetic.md"
        chapters = []
        for index in range(1, 16):
            chapters.append(
                f"# 第{index}章\n林岚在第{index}个雨夜检查编号 K{index:02d} 的钥匙。"
                f"她确认仓库{index}号门保持关闭，并把时间记为二十三点{index:02d}分。"
            )
        source.write_text("\n\n".join(chapters), encoding="utf-8")
        harness = LongformBenchmarkHarness(root)
        harness.import_corpus(
            source=source,
            benchmark_id="p13_synthetic",
            corpus_name="P13 synthetic smoke",
            license_status="synthetic",
            allow_external_api=True,
            split_mode="heading",
        )
        await harness.generate_candidates(benchmark_id="p13_synthetic")
        paths = harness.paths("p13_synthetic")
        scene_rows = read_jsonl(paths.generated_dir / "candidate_scene_briefs.jsonl")
        scene_id = str(scene_rows[0]["id"])
        write_jsonl(
            paths.generated_dir / "p12_context_cases.jsonl",
            [
                {
                    "pair_id": "p13-memory-smoke",
                    "scene_id": scene_id,
                    "chapter_id": scene_rows[0].get("chapter_id"),
                    "scene_brief": "林岚在雨夜进入仓库，保持悬疑和克制。",
                    "prior_summary": "林岚正在追查钥匙与仓库的联系。",
                    "judge_canon_summary": "钥匙仍由林岚持有；仓库门尚未开启。",
                    "variants": {
                        "memory_off": {"creative_memory": []},
                        "memory_on": {"creative_memory": [{"description": "作者偏好克制、少解释的悬疑叙述"}]},
                    },
                }
            ],
        )
        campaign = EvalCampaign.from_dict(
            {
                "id": "p13-real-smoke",
                "corpora": [
                    {
                        "benchmark_id": "p13_synthetic",
                        "enabled_experiments": ["suite", "retrieval_ab", "p12_context_ab"],
                        "scene_ids": [scene_id],
                        "data_classification": "synthetic",
                        "allow_external_api": True,
                    }
                ],
                "writer_providers": ["deepseek"],
                "judge_providers": ["deepseek"],
                "retrieval_strategy_a": "bm25",
                "retrieval_strategy_b": "minimal",
                "trials": 1,
                "suite": "smoke",
                "budget": {"max_requests": 20, "max_tokens": 100000, "batch_scenes": 1},
                "stop": {"min_pairs": 1, "min_scenes": 1},
                "privacy": {"allow_private_egress": False},
            }
        )
        first = await CampaignRunner(root, campaign, harness=harness).run()
        jobs_before = len(harness.paths("p13_synthetic").benchmark_dir.as_posix())
        second = await CampaignRunner(root, campaign, harness=harness).run()
        state = second["summary"]
        output = {
            "success": first["success"] and second["success"],
            "status": second["status"],
            "jobs": state["jobs"],
            "requests": state["usage"].get("requests"),
            "tokens": state["usage"].get("total_tokens"),
            "failures": state["failure_count"],
            "resume_job_count_stable": first["summary"]["jobs"] == second["summary"]["jobs"],
            "manifest_revision_present": bool(second["manifest"].get("code_revision")),
            "global_adoption": state["global_scope_gate_passed"],
            "synthetic_only": True,
            "internal_check": jobs_before > 0,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not output["success"] or not output["resume_job_count_stable"]:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
