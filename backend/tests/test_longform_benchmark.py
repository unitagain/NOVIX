# -*- coding: utf-8 -*-
"""P9 longform benchmark harness tests."""

import asyncio
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.eval.longform_benchmark import (
    LongformBenchmarkHarness,
    RetrievalStrategySpec,
    _api_safety_block_reason,
    _candidate_semantic_completeness,
    _load_api_safety_policy,
    _paragraphs,
    _project_candidate_to_complete_prefix,
    _sentence_split,
    _shorten_prose,
    ensure_benchmark_gitignore,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)


class _DeterministicEmbedder:
    async def embed(self, texts):
        vectors = []
        for text in texts:
            value = str(text or "")
            vectors.append(
                [
                    1.0 if "灯塔" in value else 0.0,
                    1.0 if "钥匙" in value else 0.0,
                    1.0 if "管家" in value else 0.0,
                    min(1.0, len(value) / 100.0),
                ]
            )
        return vectors


class _BrokenEmbedder:
    async def embed(self, texts):
        raise RuntimeError("semantic backend unavailable for test")


class _DeterministicReranker:
    async def rerank(self, query, documents):
        return [1.0 if "钥匙" in document else 0.0 for document in documents]


def _write_corpus(root: Path) -> Path:
    source = root / "corpus_src"
    source.mkdir()
    (source / "01.md").write_text(
        "\n".join(
            [
                "# 第一章",
                "林舟来到孤岛，发现灯塔已经熄灭。",
                "沈砚告诉林舟，钥匙一直在管家手里。",
                "夜里钟声响起，众人决定留在大厅。",
            ]
        ),
        encoding="utf-8",
    )
    (source / "02.md").write_text(
        "\n".join(
            [
                "# 第二章",
                "清晨，管家被发现死亡，钥匙落在壁炉旁。",
                "沈砚怀疑林舟隐瞒了昨夜的行踪。",
                "童谣第二句预示下一次危险会发生在海边。",
            ]
        ),
        encoding="utf-8",
    )
    return source


def _write_epub(root: Path) -> Path:
    epub = root / "demo.epub"
    chapter_1 = "第一章 风起"
    chapter_2 = "第二章 夜谈"
    body_1 = "林舟来到码头，发现灯塔已经熄灭。沈砚告诉林舟，铜钥匙一直在管家手里。"
    body_2 = "清晨，管家被发现死亡，铜钥匙落在壁炉旁。沈砚怀疑林舟隐瞒了昨夜的行踪。"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c1" href="chapters/c1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapters/c2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapters/c1.xhtml",
            f"<html><body><h1>{chapter_1}</h1><p>{body_1}</p><p>{body_1}</p><p>{body_1}</p></body></html>",
        )
        archive.writestr(
            "OEBPS/chapters/c2.xhtml",
            f"<html><body><h1>{chapter_2}</h1><p>{body_2}</p><p>{body_2}</p><p>{body_2}</p></body></html>",
        )
    return epub


def test_longform_benchmark_imports_epub_spine_as_chapters(tmp_path):
    source = _write_epub(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")

    imported = harness.import_corpus(source=source, benchmark_id="epub_demo", corpus_name="epub demo")

    assert imported["success"] is True
    manifest = json.loads((tmp_path / "benchmarks" / "epub_demo" / "manifest.json").read_text(encoding="utf-8"))
    chapters = read_jsonl(tmp_path / "benchmarks" / "epub_demo" / "corpus" / "chapters.jsonl")
    assert manifest["chapter_count"] == 2
    assert manifest["word_count"] > 160
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["data_classification"] == "private"
    assert [row["title"] for row in chapters] == ["第一章 风起", "第二章 夜谈"]
    assert "铜钥匙" in chapters[0]["text"]


def test_longform_benchmark_import_generate_run_report_compare_promote(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _BrokenEmbedder(),
    )

    imported = harness.import_corpus(
        source=source,
        benchmark_id="demo",
        corpus_name="demo novel",
        license_status="private",
    )
    assert imported["success"] is True
    manifest_path = tmp_path / "benchmarks" / "demo" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["chapter_count"] == 2
    assert manifest["license_status"] == "private"
    generated = asyncio.run(harness.generate_candidates(benchmark_id="demo"))
    assert generated["success"] is True
    candidate_facts = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "candidate_canon.jsonl")
    candidate_queries = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "candidate_queries.jsonl")
    style_profile = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "candidate_style_profile.jsonl")
    candidate_characters = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "candidate_characters.jsonl")
    no_context_cases = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "no_context_probe.jsonl")
    timeline_probes = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "timeline_foreshadow_probe.jsonl")
    counterfactual = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "counterfactual.jsonl")
    assert candidate_facts
    assert candidate_queries
    assert style_profile
    assert candidate_characters
    assert no_context_cases
    assert timeline_probes
    assert counterfactual[0]["expected_marker"].startswith("CF_MARKER_")
    assert counterfactual[0]["expected_marker"] in counterfactual[0]["mutated"]
    assert candidate_facts[0]["status"] == "candidate"
    assert "trace_ref" in candidate_facts[0]
    calibration_candidates = read_jsonl(
        tmp_path / "benchmarks" / "demo" / "generated" / "calibration_candidates.jsonl"
    )
    calibration_controls = read_jsonl(
        tmp_path / "benchmarks" / "demo" / "generated" / "calibration_controls.jsonl"
    )
    scene_briefs = read_jsonl(tmp_path / "benchmarks" / "demo" / "generated" / "candidate_scene_briefs.jsonl")
    assert calibration_controls == calibration_candidates
    assert all("resident_context" in row for row in scene_briefs)
    assert calibration_candidates[0]["task_type"] == "rubric_prose_quality"
    assert calibration_candidates[0]["candidate_text"]
    assert calibration_candidates[0]["chapter_text"] == calibration_candidates[0]["candidate_text"]
    assert calibration_candidates[0]["reference_excerpt"]

    review = harness.build_review_pack(benchmark_id="demo", size=3)
    assert review["success"] is True
    assert review["counts"]["timeline_foreshadow_probes"] > 0
    assert Path(review["path"]).exists()
    review_payload = json.loads(Path(review["path"]).read_text(encoding="utf-8"))
    assert review_payload["calibration"][0]["human_overall_score"] is None
    assert "scoring_guide" in review_payload
    assert review_payload["scoring_guide"]["score_target"].startswith("calibration[*].candidate_text")
    assert review_payload["calibration"][0]["candidate_text"]
    assert review_payload["calibration"][0]["reference_excerpt"]
    review_payload["facts"][0]["accepted"] = True
    review_payload["timeline"][0]["accepted"] = True
    review_payload["queries"][0]["accepted"] = True
    review_payload["calibration"][0]["human_overall_score"] = 4.0
    review_payload["calibration"][0]["human_notes"] = "可接受"
    Path(review["path"]).write_text(json.dumps(review_payload, ensure_ascii=False), encoding="utf-8")
    applied = harness.apply_review_pack(benchmark_id="demo", review_file=review["path"])
    assert applied["promoted_counts"]["facts"] == 1
    assert applied["promoted_counts"]["calibration"] == 1
    gold_facts = read_jsonl(tmp_path / "benchmarks" / "demo" / "gold" / "canon.jsonl")
    gold_calibration = read_jsonl(tmp_path / "benchmarks" / "demo" / "gold" / "calibration_set.jsonl")
    assert gold_facts[0]["status"] == "confirmed"
    assert gold_calibration[0]["human_overall_score"] == 4.0

    run_a = asyncio.run(harness.run_suite(benchmark_id="demo", suite="smoke", strategy="bm25", run_id="run_a"))
    run_b = asyncio.run(harness.run_suite(benchmark_id="demo", suite="smoke", strategy="jit_hybrid", run_id="run_b"))
    assert run_a["success"] is True
    assert run_b["success"] is False
    assert run_a["metrics"]["retrieval"]["available"] is True
    assert run_b["metrics"]["retrieval"]["executed_strategy"] == "bm25_degraded"
    assert run_b["metrics"]["retrieval"]["strategy_fidelity"] is False
    assert run_a["metrics"]["timeline_foreshadow_probe"]["available"] is True
    assert run_a["metrics"]["no_context_probe"]["reason"] == "no_context_probe_not_requested"
    assert run_a["metrics"]["counterfactual_adherence"]["reason"] == "counterfactual_not_requested"
    assert run_a["metrics"]["cost"]["full_stuffing_reference_tokens"] >= run_a["metrics"]["cost"]["jit_reference_tokens"]
    assert (tmp_path / "benchmarks" / "demo" / "runs" / "run_a" / "trace.json").exists()
    manifest_after_run = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_after_run["commands"]
    assert manifest_after_run["estimated_cost"]["strategy"] == "jit_hybrid" or manifest_after_run["estimated_cost"]["strategy"] == "bm25"
    assert "llm_tokens" in manifest_after_run["actual_cost"]

    comparison = harness.compare_runs(benchmark_id="demo", run_a="run_a", run_b="run_b")
    assert comparison["success"] is True
    assert comparison["comparison"]["strategy_a"] == "bm25"
    assert comparison["comparison"]["strategy_b"] == "jit_hybrid"
    assert comparison["comparison"]["recommendation"] == "invalid_strategy_execution"
    assert comparison["comparison"]["distinct_execution"] is False
    assert comparison["comparison"]["strategy_fidelity"] is False
    assert "sample_size_note" in comparison["comparison"]
    assert comparison["comparison"]["sample_size_ready"] is False
    assert comparison["comparison"]["adoption_gate_passed"] is False

    report = harness.report(benchmark_id="demo", run_id="run_a")
    assert report["success"] is True
    report_text = Path(report["report"]).read_text(encoding="utf-8")
    assert report_text.startswith("# Longform Benchmark Report")
    assert "counterfactual adherence" in report_text
    assert "## Calibration" in report_text

    promoted = harness.promote_failures(benchmark_id="demo", run_id="run_a", limit=10)
    assert promoted["success"] is True
    assert (tmp_path / "benchmarks" / "demo" / "generated" / "replay_cases.jsonl").exists()

    llm_calibration = asyncio.run(
        harness.generate_writing_calibration(benchmark_id="demo", provider="missing-profile-for-test")
    )
    assert llm_calibration["success"] is False
    assert llm_calibration["reason"].startswith("manifest.allow_external_api is false")


def test_longform_benchmark_rejects_external_api_for_local_only_corpus(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    with pytest.raises(ValueError, match="local_only_corpus_cannot_allow_external_api"):
        harness.import_corpus(
            source=source,
            benchmark_id="local-only",
            data_classification="local_only",
            allow_external_api=True,
        )


def test_strategy_ab_preflight_filters_identical_contexts_without_provider_calls(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _DeterministicEmbedder(),
        reranker_factory=lambda: _DeterministicReranker(),
    )
    harness.import_corpus(source=source, benchmark_id="preflight")
    asyncio.run(harness.generate_candidates(benchmark_id="preflight", scene_windows=2))
    result = asyncio.run(harness.preflight_strategy_ab(benchmark_id="preflight"))
    assert result["scenes_checked"] >= 2
    assert result["eligible_scenes"] + sum(result["ineligible_reasons"].values()) == result["scenes_checked"]
    assert all("scene_id" in row and "strategies" in row for row in result["rows"])


def test_scene_windows_keep_non_overlapping_held_out_continuation():
    paragraphs = [f"第{index}段正文" * 30 for index in range(1, 6)]
    windows = LongformBenchmarkHarness._chapter_scene_windows(paragraphs, [], max_windows=2)

    assert windows[0]["reference_continuation"]
    assert windows[0]["reference_continuation"] not in windows[0]["text"]
    assert windows[0]["text"] not in windows[0]["reference_continuation"]


def test_refresh_strategy_references_migrates_existing_candidates_without_content_loss(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("reference-migration")
    write_jsonl(
        paths.generated_dir / "candidate_scene_briefs.jsonl",
        [{"id": "scene-1", "reference_continuation": "真实后续片段"}],
    )
    write_jsonl(
        paths.generated_dir / "strategy_ab_candidates.jsonl",
        [{"id": "candidate-1", "scene_id": "scene-1", "candidate_text": "完整候选正文"}],
    )

    result = harness.refresh_strategy_references(benchmark_id="reference-migration")
    rows = read_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl")

    assert result["references_attached"] == 1
    assert rows[0]["candidate_text"] == "完整候选正文"
    assert rows[0]["reference_excerpt"] == "真实后续片段"
    assert rows[0]["reference_available"] is True
    assert rows[0]["reference_sha256"]


def test_strategy_pair_fingerprint_binds_held_out_reference():
    first = {
        "id": "pair-A",
        "candidate_text": "候选 A",
        "strategy_role": "A",
        "retrieval_strategy": "bm25",
        "reference_excerpt": "参考一",
    }
    second = {
        "id": "pair-B",
        "candidate_text": "候选 B",
        "strategy_role": "B",
        "retrieval_strategy": "jit_hybrid",
        "reference_excerpt": "参考一",
    }
    original = LongformBenchmarkHarness._strategy_ab_pair_fingerprint(first, second)

    changed = LongformBenchmarkHarness._strategy_ab_pair_fingerprint(
        {**first, "reference_excerpt": "参考二"},
        {**second, "reference_excerpt": "参考二"},
    )

    assert changed != original


def test_strategy_human_review_pack_is_blinded_and_gold_is_content_free(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("human-review")
    pair_id = "pair-1"
    common = {
        "pair_id": pair_id,
        "chapter_id": "c1",
        "scene_id": "s1",
        "prior_summary": "此前摘要",
        "scene_brief": "继续当前场景",
        "reference_excerpt": "原文后续",
        "writer_provider": "writer",
        "writer_model": "model",
        "prompt_version": "writer-v1",
    }
    write_jsonl(
        paths.generated_dir / "strategy_ab_candidates.jsonl",
        [
            {
                **common,
                "id": "pair-1-A",
                "strategy_role": "A",
                "retrieval_strategy": "bm25",
                "candidate_text": "完整候选 A",
                "retrieval_execution": {"execution_signature": "exec-a"},
            },
            {
                **common,
                "id": "pair-1-B",
                "strategy_role": "B",
                "retrieval_strategy": "jit_hybrid",
                "candidate_text": "完整候选 B",
                "retrieval_execution": {"execution_signature": "exec-b"},
            },
        ],
    )

    pack = harness.build_strategy_review_pack(benchmark_id="human-review", size=1)
    reviews = read_jsonl(Path(pack["review_path"]))
    assert reviews[0]["candidate_left"] in {"完整候选 A", "完整候选 B"}
    assert "strategy_role" not in reviews[0]
    harness.record_strategy_review(
        review_path=pack["review_path"],
        review_id=reviews[0]["review_id"],
        winner="left",
        reason_codes=["continuity", "continuity"],
        reviewer="developer",
    )

    applied = harness.apply_strategy_review_pack(
        benchmark_id="human-review",
        review_path=pack["review_path"],
        key_path=pack["key_path"],
        reviewer="developer",
    )

    assert applied["success"] is True
    gold = read_jsonl(Path(applied["gold_path"]))
    assert gold[0]["human_winner"] in {"A", "B"}
    assert gold[0]["reason_codes"] == ["continuity"]
    assert "candidate_left" not in gold[0]
    assert "candidate_text" not in gold[0]

    harness.record_strategy_review(
        review_path=pack["review_path"],
        review_id=reviews[0]["review_id"],
        winner="incomparable",
        reason_codes=["both_truncated"],
        reviewer="developer",
    )
    harness.apply_strategy_review_pack(
        benchmark_id="human-review",
        review_path=pack["review_path"],
        key_path=pack["key_path"],
        reviewer="developer",
    )
    assert read_jsonl(Path(applied["gold_path"]))[0]["human_winner"] == "incomparable"


def test_strategy_ab_truncated_writer_attempts_are_billed_and_classified_as_data(tmp_path, monkeypatch):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _DeterministicEmbedder(),
        reranker_factory=lambda: _DeterministicReranker(),
    )
    harness.import_corpus(source=source, benchmark_id="truncated", allow_external_api=True)
    asyncio.run(harness.generate_candidates(benchmark_id="truncated", scene_windows=2))
    scene_id = read_jsonl(harness.paths("truncated").generated_dir / "candidate_scene_briefs.jsonl")[0]["id"]

    async def distinct_selection(**kwargs):
        strategy = kwargs["spec"].name
        return {
            "requested_strategy": strategy,
            "executed_strategy": strategy,
            "strategy_fidelity": True,
            "facts": [{"id": f"fact-{strategy}", "statement": f"context-{strategy}"}],
            "latency_ms": 1.0,
            "execution_signature": f"execution-{strategy}",
        }

    class TruncatedGateway:
        async def chat(self, _messages, **_kwargs):
            return {
                "content": "",
                "provider": "glm",
                "model": "glm-5.1",
                "finish_reason": "length",
                "usage": {"prompt_tokens": 10, "completion_tokens": 90, "total_tokens": 100},
            }

    monkeypatch.setattr("app.eval.longform_benchmark.get_gateway", lambda: TruncatedGateway())
    monkeypatch.setattr(harness, "_select_writer_strategy_context", distinct_selection)
    monkeypatch.setattr(
        "app.eval.longform_benchmark.judge_extra_body",
        lambda _profile_id: {"thinking": {"type": "disabled"}},
    )
    result = asyncio.run(
        harness.generate_strategy_ab(
            benchmark_id="truncated",
            provider="glm-profile",
            scene_ids=[scene_id],
        )
    )
    failures = read_jsonl(harness.paths("truncated").generated_dir / "strategy_ab_generation_failures.jsonl")
    assert result["success"] is False
    assert result["requests_attempted"] == 2
    assert result["usage"]["total_tokens"] == 200
    assert {row["reason"] for row in failures} == {"writer_output_truncated"}


def test_strategy_ab_repairs_semantically_incomplete_candidates_once(tmp_path, monkeypatch):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _DeterministicEmbedder(),
        reranker_factory=lambda: _DeterministicReranker(),
    )
    harness.import_corpus(source=source, benchmark_id="repair", allow_external_api=True)
    asyncio.run(harness.generate_candidates(benchmark_id="repair", scene_windows=2))
    scene_id = read_jsonl(harness.paths("repair").generated_dir / "candidate_scene_briefs.jsonl")[0]["id"]

    async def distinct_selection(**kwargs):
        strategy = kwargs["spec"].name
        return {
            "requested_strategy": strategy,
            "executed_strategy": strategy,
            "strategy_fidelity": True,
            "facts": [{"id": f"fact-{strategy}", "statement": f"context-{strategy}"}],
            "latency_ms": 1.0,
            "execution_signature": f"execution-{strategy}",
        }

    incomplete_body = "她沿着走廊继续追查那封信的来历。" * 20 + "他忽然说道：“事情还没有结束……"
    repaired_body = "”\n\n" + "她沿着走廊继续追查那封信的来历，确认身后的门已经关好。" * 40

    class RepairingGateway:
        async def chat(self, messages, *, provider, **_kwargs):
            is_repair = "候选收尾器" in messages[0]["content"]
            assert "response_format" not in _kwargs
            payload = (
                {"continuation_suffix": repaired_body, "self_check": {}}
                if is_repair
                else {"candidate_text": incomplete_body, "self_check": {}}
            )
            return {
                "content": json.dumps(payload),
                "provider": provider,
                "model": "repair-model",
                "finish_reason": "stop",
                "elapsed_time": 0.1 if is_repair else 0.2,
                "usage": {
                    "prompt_tokens": 20 if is_repair else 10,
                    "completion_tokens": 10,
                    "total_tokens": 30 if is_repair else 20,
                },
            }

    monkeypatch.setattr("app.eval.longform_benchmark.get_gateway", lambda: RepairingGateway())
    monkeypatch.setattr(harness, "_select_writer_strategy_context", distinct_selection)
    result = asyncio.run(
        harness.generate_strategy_ab(
            benchmark_id="repair",
            provider="writer-profile",
            scene_ids=[scene_id],
        )
    )

    assert result["success"] is True
    assert result["requests_attempted"] == 4
    assert result["initial_candidate_responses"] == 2
    assert result["initial_candidate_validity_rate"] == 0.0
    assert result["repair_requests_attempted"] == 2
    assert result["repair_success_rate"] == 1.0
    assert result["final_pair_validity_rate"] == 1.0
    rows = read_jsonl(harness.paths("repair").generated_dir / "strategy_ab_candidates.jsonl")
    assert len(rows) == 2
    assert all(row["candidate_generation_stage"] == "semantic_repair" for row in rows)
    assert all(row["gateway_usage"]["total_tokens"] == 50 for row in rows)
    assert all(row["candidate_artifact"]["usage"]["total_tokens"] == 50 for row in rows)
    assert all(row["candidate_artifact"]["usage"]["requests"] == 2 for row in rows)
    assert all(row["generation_latency_ms"] == 300.0 for row in rows)


def test_strategy_pair_ids_are_scoped_by_writer_profile(tmp_path, monkeypatch):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    harness.import_corpus(source=source, benchmark_id="writer-scope", allow_external_api=True)
    asyncio.run(harness.generate_candidates(benchmark_id="writer-scope", scene_windows=2))
    scene_id = read_jsonl(harness.paths("writer-scope").generated_dir / "candidate_scene_briefs.jsonl")[0]["id"]

    async def distinct_selection(**kwargs):
        strategy = kwargs["spec"].name
        return {
            "requested_strategy": strategy,
            "executed_strategy": strategy,
            "strategy_fidelity": True,
            "facts": [{"id": f"fact-{strategy}", "statement": f"context-{strategy}"}],
            "latency_ms": 1.0,
            "execution_signature": f"execution-{strategy}",
        }

    candidate_body = "她沿着昏暗的走廊缓慢向前，确认门外没有脚步声后，才把信封收进衣袋。" * 20

    class SuccessfulGateway:
        async def chat(self, _messages, *, provider, **_kwargs):
            return {
                "content": json.dumps(
                    {
                        "candidate_text": candidate_body,
                        "self_check": {},
                    }
                ),
                "provider": provider,
                "model": f"model-{provider}",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }

    monkeypatch.setattr("app.eval.longform_benchmark.get_gateway", lambda: SuccessfulGateway())
    monkeypatch.setattr(harness, "_select_writer_strategy_context", distinct_selection)
    first = asyncio.run(
        harness.generate_strategy_ab(
            benchmark_id="writer-scope",
            provider="writer-a",
            scene_ids=[scene_id],
        )
    )
    second = asyncio.run(
        harness.generate_strategy_ab(
            benchmark_id="writer-scope",
            provider="writer-b",
            scene_ids=[scene_id],
            append=True,
        )
    )

    assert len(first["pair_ids"]) == 1
    assert len(second["pair_ids"]) == 1
    assert set(first["pair_ids"]).isdisjoint(second["pair_ids"])
    rows = read_jsonl(harness.paths("writer-scope").generated_dir / "strategy_ab_candidates.jsonl")
    assert len(rows) == 4
    assert all(row["candidate_text"] == candidate_body for row in rows)
    assert all(row["candidate_char_count"] == len(candidate_body) for row in rows)
    assert all(row["candidate_storage_complete"] is True for row in rows)


def test_longform_benchmark_cli_help():
    script = Path(__file__).resolve().parents[1] / "scripts" / "longform_benchmark.py"
    result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "longform benchmark" in result.stdout.lower()


def test_benchmark_json_readers_accept_utf8_bom(tmp_path):
    json_path = tmp_path / "review.json"
    jsonl_path = tmp_path / "rows.jsonl"
    json_path.write_text('{"ok": true}', encoding="utf-8-sig")
    jsonl_path.write_text('{"id": "F1"}\n', encoding="utf-8-sig")

    assert read_json(json_path) == {"ok": True}
    assert read_jsonl(jsonl_path) == [{"id": "F1"}]


def test_generate_candidates_preserves_current_real_calibration_candidates(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    harness.import_corpus(source=source, benchmark_id="demo")
    asyncio.run(harness.generate_candidates(benchmark_id="demo"))
    paths = harness.paths("demo")
    current = paths.generated_dir / "calibration_candidates.jsonl"
    real_row = {
        "id": "CAL-real",
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "writer_variant": "full_context",
        "trace_ref": "llm_writing_calibration",
    }
    current.write_text(json.dumps(real_row, ensure_ascii=False) + "\n", encoding="utf-8")

    asyncio.run(harness.generate_candidates(benchmark_id="demo", scene_windows=2))

    assert read_jsonl(current) == [real_row]
    assert read_jsonl(paths.generated_dir / "calibration_controls.jsonl")


def test_longform_benchmark_cli_run_stdout_is_summarized(tmp_path):
    source = _write_corpus(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "longform_benchmark.py"
    root = tmp_path / "benchmarks"

    import_result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "import",
            "--source",
            str(source),
            "--benchmark-id",
            "demo",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_result.returncode == 0
    generate_result = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "generate", "--benchmark-id", "demo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generate_result.returncode == 0
    generate_payload = json.loads(generate_result.stdout)
    assert "data" not in (generate_payload.get("generation") or {}).get("llm", {})
    assert "林舟来到孤岛" not in generate_result.stdout

    run_result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(root),
            "run",
            "--benchmark-id",
            "demo",
            "--suite",
            "smoke",
            "--strategy",
            "bm25",
            "--run-id",
            "cli_run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert run_result.returncode == 0
    payload = json.loads(run_result.stdout)
    assert payload["success"] is True
    assert "metrics_summary" in payload
    assert "metrics" not in payload
    assert '"cases": [' not in run_result.stdout
    assert "林舟来到孤岛" not in run_result.stdout
    assert (root / "demo" / "runs" / "cli_run" / "metrics.json").exists()


def test_retrieval_strategies_execute_real_hybrid_and_full_stuffing(tmp_path):
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _DeterministicEmbedder(),
        reranker_factory=lambda: _DeterministicReranker(),
    )
    facts = [
        {"id": "F1", "statement": "灯塔已经熄灭", "chapter_id": "C0001", "status": "confirmed"},
        {"id": "F2", "statement": "铜钥匙仍由管家保管", "chapter_id": "C0001", "status": "confirmed"},
        {"id": "F3", "statement": "未来有人带走钥匙", "chapter_id": "C0003", "status": "confirmed"},
    ]
    queries = [
        {"query": "谁保管钥匙", "expect": ["F2"], "chapter_id": "C0002"},
        {"query": "灯塔状态", "expect": ["F1"], "chapter_id": "C0002"},
    ]

    hybrid = asyncio.run(harness.pipeline.statistics.retrieval(facts, queries, strategy="hybrid_rerank"))
    stuffing = asyncio.run(harness.pipeline.statistics.retrieval(facts, queries, strategy="full_stuffing"))

    assert hybrid["strategy_fidelity"] is True
    assert hybrid["executed_strategy"] == "hybrid_rerank"
    assert hybrid["semantic_used_cases"] == 2
    assert hybrid["retrieval_policy"]["semantic_rerank"] is True
    assert hybrid["reranker_used_cases"] == 2
    assert hybrid["reranker_runtime_available"] is True
    assert hybrid["latency_ms"]["p95"] >= 0
    assert all(case["ranking_trace"]["signals"]["rerank"] for case in hybrid["cases"])
    assert stuffing["strategy_fidelity"] is True
    assert stuffing["ranking_metrics_available"] is False
    assert stuffing["mrr"] is None
    assert all("F3" not in case["retrieved"] for case in stuffing["cases"])
    assert stuffing["selected_context_tokens"]["mean"] >= hybrid["selected_context_tokens"]["mean"]


def test_retrieval_strategy_rejects_unimplemented_labels(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")

    try:
        harness.pipeline.generation.resolve_strategy("strategy_label_only")
    except ValueError as exc:
        assert "unsupported retrieval strategy" in str(exc)
    else:
        raise AssertionError("unsupported strategy must be rejected")


def test_retrieval_strategy_spec_can_be_passed_directly(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    spec = RetrievalStrategySpec("custom_lexical", top_k=2)

    assert harness.pipeline.generation.resolve_strategy(spec) is spec


def test_llm_semantic_queries_are_normalized_and_stratified(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    chapters = [
        {
            "id": "C0001",
            "order": 1,
            "title": "第一章",
            "text": "林舟来到孤岛后发现用于引导船只的高塔已经失去照明能力。",
        }
    ]
    generated = harness.pipeline.corpus.normalize_generated(
        {
            "queries": [
                {
                    "fact_id": "C0001-F01",
                    "query": "海上导航设施目前能否正常工作？",
                    "query_type": "semantic_paraphrase",
                },
                {
                    "fact_id": "C0001-F01",
                    "query": "林舟来到孤岛后发现用于引导船只的高塔已经失去照明能力吗？",
                    "query_type": "semantic_paraphrase",
                },
            ]
        },
        chapters,
        llm_metadata={"provider": "deepseek", "model": "deepseek-chat"},
    )
    queries = generated["candidate_queries.jsonl"]
    semantic = [row for row in queries if row.get("query_type") == "semantic_paraphrase"]

    assert len(semantic) == 1
    assert semantic[0]["expect"] == ["C0001-F01"]
    assert semantic[0]["difficulty"] == "semantic"
    assert semantic[0]["lexical_overlap"] <= 0.55
    assert semantic[0]["extractor_model"] == "deepseek-chat"

    controls = [{"id": f"L{idx}", "query": f"控制{idx}"} for idx in range(10)]
    selected = harness.pipeline.corpus.select_queries([*controls, *semantic], limit=4)
    assert any(row.get("query_type") == "semantic_paraphrase" for row in selected)


def test_llm_candidate_schema_requires_all_array_sections():
    assert LongformBenchmarkHarness._llm_candidate_schema_errors({"statement": "single row"}) == [
        "queries_must_be_array"
    ]
    assert LongformBenchmarkHarness._llm_candidate_schema_errors({"queries": []}) == [
        "queries_must_not_be_empty"
    ]
    assert LongformBenchmarkHarness._llm_candidate_schema_errors({"queries": [{"query": "x"}]}) == []


def test_llm_generation_summary_hashes_but_does_not_expose_payload():
    summary = LongformBenchmarkHarness._llm_generation_summary(
        {
            "available": True,
            "used": True,
            "success": True,
            "data": {"facts": [{"statement": "private evidence"}], "timeline": [], "queries": [], "scene_briefs": []},
        }
    )

    assert "data" not in summary
    assert summary["data_counts"]["facts"] == 1
    assert summary["data_sha256"]


def test_compare_does_not_treat_tiny_absolute_latency_as_product_regression(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("demo")
    for run_id, strategy, latency, tokens in (
        ("a", "full_stuffing", 0.5, 500),
        ("b", "bm25", 13.0, 100),
    ):
        run_dir = paths.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(json.dumps({"strategy": strategy}), encoding="utf-8")
        (run_dir / "metrics.json").write_text(
            json.dumps(
                {
                    "retrieval": {
                        "recall": 1.0,
                        "mrr": None if strategy == "full_stuffing" else 1.0,
                        "strategy_fidelity": True,
                        "executed_strategy": strategy,
                        "execution_signature": strategy,
                        "latency_ms": {"p95": latency},
                    },
                    "cost": {"selected_context_tokens_per_query": {"mean": tokens}},
                    "memory": {"pollution_rate": 0.0},
                    "character_state_probe": {"accuracy": 1.0},
                    "counterfactual_adherence": {"adherence": 1.0},
                    "case_counts": {"queries": 20},
                }
            ),
            encoding="utf-8",
        )

    comparison = harness.compare_runs(benchmark_id="demo", run_a="a", run_b="b")["comparison"]

    assert comparison["retrieval_p95_latency_delta_pct"] > 0.2
    assert comparison["retrieval_p95_latency_delta_ms"] < 50
    assert comparison["recommendation"] == "prefer_b_for_debug_only_expand_sample"
    assert comparison["component_gate_passed"] is False
    assert comparison["adoption_gate_passed"] is False


def test_compare_separates_component_gate_from_output_adoption(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("demo")
    for run_id, strategy, recall, mrr, latency, tokens in (
        ("a", "bm25", 0.98, 0.96, 25.0, 150),
        ("b", "jit_hybrid", 1.0, 0.98, 170.0, 184),
    ):
        run_dir = paths.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(json.dumps({"strategy": strategy}), encoding="utf-8")
        (run_dir / "metrics.json").write_text(
            json.dumps(
                {
                    "retrieval": {
                        "recall": recall,
                        "mrr": mrr,
                        "strategy_fidelity": True,
                        "executed_strategy": strategy,
                        "execution_signature": strategy,
                        "latency_ms": {"p95": latency},
                    },
                    "cost": {"selected_context_tokens_per_query": {"mean": tokens}},
                    "memory": {"pollution_rate": 0.0},
                    "character_state_probe": {"accuracy": 1.0},
                    "counterfactual_adherence": {"adherence": 1.0},
                    "case_counts": {"queries": 100},
                }
            ),
            encoding="utf-8",
        )

    comparison = harness.compare_runs(benchmark_id="demo", run_a="a", run_b="b")["comparison"]

    assert comparison["component_gate_passed"] is True
    assert comparison["adoption_gate_passed"] is False
    assert comparison["recommendation"] == "prefer_b_for_output_ab"


def test_calibration_writer_messages_have_full_and_low_context_variants():
    brief = {
        "brief": "续写林舟调查灯塔。",
        "prior_summary": "林舟来到孤岛，发现灯塔已经熄灭。",
        "resident_context": "此前沈砚确认铜钥匙仍由管家保管。",
    }
    facts = [
        {
            "id": "F1",
            "statement": "钥匙一直在管家手里。",
            "context_rank_score": 0.8,
            "context_subject_entities": ["管家"],
        }
    ]

    full = LongformBenchmarkHarness._build_calibration_writer_messages(
        brief=brief,
        facts=facts,
        variant="full_context",
    )
    low = LongformBenchmarkHarness._build_calibration_writer_messages(
        brief=brief,
        facts=facts,
        variant="low_context",
    )

    assert "钥匙一直在管家手里" in full[1]["content"]
    assert "此前沈砚确认铜钥匙" in full[1]["content"]
    assert "rank_score" in full[1]["content"]
    assert "钥匙一直在管家手里" not in low[1]["content"]
    assert "此前沈砚确认铜钥匙" not in low[1]["content"]
    assert "candidate_text" in full[1]["content"]
    assert "禁止把原事件或原对话重新演一遍" in full[1]["content"]
    assert "不可逆状态" in full[1]["content"]
    assert "resident_state=true" in full[1]["content"]
    assert '"subject_entities": ["管家"]' in full[1]["content"]
    assert "禁止把一个人物的经历" in full[1]["content"]

    strategy = LongformBenchmarkHarness._build_calibration_writer_messages(
        brief=brief,
        facts=facts,
        variant="strategy_bm25",
        include_context=True,
        prompt_variant="retrieval_context",
        include_fact_metadata=False,
    )
    assert '"variant": "retrieval_context"' in strategy[1]["content"]
    assert "strategy_bm25" not in strategy[1]["content"]
    assert "rank_score" not in strategy[1]["content"]

    direct_strategy = LongformBenchmarkHarness._build_calibration_writer_messages(
        brief=brief,
        facts=facts,
        variant="strategy_bm25",
        include_context=True,
        prompt_variant="retrieval_context",
        include_fact_metadata=True,
        structured_output=False,
    )
    assert "不要 JSON" in direct_strategy[0]["content"]
    assert "required_json_schema" not in direct_strategy[1]["content"]


def test_candidate_semantic_completeness_rejects_short_open_or_dangling_output():
    assert _candidate_semantic_completeness("完整场景。" * 80)["complete"] is True
    assert _candidate_semantic_completeness("他说：“事情还没有结束……")["complete"] is False
    assert _candidate_semantic_completeness("叙述内容" * 80 + "密码")["complete"] is False
    internal_imbalance = _candidate_semantic_completeness("“缺少内部闭合。\n\n" + "完整场景。" * 80)
    assert internal_imbalance["complete"] is True
    assert internal_imbalance["warnings"] == ["internal_quote_imbalance"]


def test_candidate_length_projection_uses_complete_sentence_boundary():
    source = "完整长句。" * 400

    projected = _project_candidate_to_complete_prefix(source, max_chars=1500)

    assert projected["applied"] is True
    assert projected["original_char_count"] == len(source)
    assert projected["projected_char_count"] <= 1500
    assert projected["text"].endswith("。")
    assert _candidate_semantic_completeness(projected["text"])["complete"] is True

    dialogue_source = "“第一句没有闭合。" + "前段叙述。" * 50 + "\n\n" + "最后一段完整结束。" * 250
    dialogue_projection = _project_candidate_to_complete_prefix(dialogue_source, max_chars=1500)
    assert dialogue_projection["applied"] is True
    assert _candidate_semantic_completeness(dialogue_projection["text"])["complete"] is True


def test_normalize_strategy_ab_candidates_updates_artifact_fingerprint(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("normalize")
    original = "完整长句。" * 400
    write_jsonl(
        paths.generated_dir / "strategy_ab_candidates.jsonl",
        [
            {
                "id": "pair-A",
                "pair_id": "pair",
                "strategy_role": "A",
                "candidate_text": original,
                "chapter_text": original,
                "candidate_artifact": {"content_fingerprint": "stale"},
            }
        ],
    )

    result = harness.normalize_strategy_ab_candidates(benchmark_id="normalize")
    row = read_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl")[0]

    assert result["success"] is True
    assert result["length_projections_applied"] == 1
    assert row["candidate_char_count"] <= 1500
    assert row["candidate_text"].endswith("。")
    assert row["candidate_artifact"]["content_fingerprint"] == hashlib.sha256(
        row["candidate_text"].encode("utf-8")
    ).hexdigest()
    assert Path(result["archive_path"]).exists()


def test_strategy_judge_skips_semantically_incomplete_candidate_pairs(tmp_path, monkeypatch):
    import app.eval.longform_benchmark as benchmark_module

    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    paths = harness.paths("incomplete-pair")
    write_json(paths.manifest, {"allow_external_api": True})
    common = {
        "pair_id": "pair-1",
        "chapter_id": "C0001",
        "scene_id": "S1",
        "retrieval_execution": {"strategy_fidelity": True, "execution_signature": "exec"},
        "generation_config": {"temperature": 0.7},
        "writer_provider": "writer",
        "writer_model": "model",
        "prompt_version": "writer-v1",
    }
    write_jsonl(
        paths.generated_dir / "strategy_ab_candidates.jsonl",
        [
            {
                **common,
                "id": "pair-1-A",
                "strategy_role": "A",
                "retrieval_strategy": "bm25",
                "candidate_text": "他说：“还没有结束……",
            },
            {
                **common,
                "id": "pair-1-B",
                "strategy_role": "B",
                "retrieval_strategy": "jit_hybrid",
                "candidate_text": "完整场景。" * 80,
                "retrieval_execution": {"strategy_fidelity": True, "execution_signature": "exec-b"},
            },
        ],
    )

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("judge must not receive semantically incomplete candidates")

    monkeypatch.setattr(benchmark_module, "run_pointwise_pair_judge_eval", must_not_run)
    result = asyncio.run(
        harness.score_strategy_ab(benchmark_id="incomplete-pair", provider="judge", force_external=True)
    )

    assert result["requests_attempted"] == 0
    rows = read_jsonl(Path(result["path"]))
    assert rows[0]["skipped_reason"] == "candidate_semantically_incomplete"
    assert result["analysis"]["candidate_validity_rate"] == 0.0


def test_calibration_context_facts_are_ranked_by_relevance_and_quality():
    brief = {
        "brief": "续写林舟在灯塔调查钥匙。",
        "prior_summary": "林舟发现灯塔熄灭，钥匙可能在管家手里。",
        "canon_refs": ["F-key"],
    }
    facts = [
        {"id": "F-noise", "statement": "远处的云层颜色变化很快。", "confidence": 0.55},
        {"id": "F-key", "statement": "管家曾经保管灯塔钥匙，林舟正在追查钥匙去向。", "confidence": 0.55},
        {"id": "F-short", "statement": "林舟。", "confidence": 0.99},
    ]

    selected = LongformBenchmarkHarness._select_calibration_context_facts(brief, facts, limit=2)
    stats = LongformBenchmarkHarness._calibration_context_pack_stats(selected)

    assert [row["id"] for row in selected] == ["F-key"]
    assert selected[0]["context_rank_score"] > 0.4
    assert stats["fact_count"] == 1
    assert stats["avg_rank_score"] == selected[0]["context_rank_score"]


def test_calibration_context_facts_exclude_future_and_prefer_scene_local_evidence():
    brief = {
        "chapter_id": "C0003",
        "brief": "续写林舟调查失踪的铜钥匙。",
        "prior_summary": "林舟在仓库寻找铜钥匙，并询问沈砚昨夜的动静。",
        "source_start_ratio": 0.45,
        "source_end_ratio": 0.55,
    }
    facts = [
        {
            "id": "F-prior",
            "statement": "林舟此前确认铜钥匙曾由仓库管家负责保管。",
            "confidence": 0.8,
            "chapter_id": "C0002",
            "source_position_ratio": 0.1,
        },
        {
            "id": "F-local",
            "statement": "林舟在仓库门边发现了铜钥匙留下的新鲜划痕。",
            "confidence": 0.8,
            "chapter_id": "C0003",
            "source_position_ratio": 0.5,
        },
        {
            "id": "F-future",
            "statement": "沈砚稍后承认自己已经把铜钥匙交给了陌生访客。",
            "confidence": 0.95,
            "chapter_id": "C0003",
            "source_position_ratio": 0.8,
        },
        {
            "id": "F-next-chapter",
            "statement": "第二天访客带着铜钥匙离开仓库，前往北岸码头。",
            "confidence": 0.95,
            "chapter_id": "C0004",
            "source_position_ratio": 0.1,
        },
        {
            "id": "F-state",
            "statement": "沈砚在前一章遭人下毒后死亡，已经无法再参与调查。",
            "confidence": 0.8,
            "chapter_id": "C0002",
            "source_position_ratio": 0.9,
        },
    ]

    selected = LongformBenchmarkHarness._select_calibration_context_facts(brief, facts, limit=5)

    assert [row["id"] for row in selected][:1] == ["F-local"]
    assert "F-future" not in {row["id"] for row in selected}
    assert "F-next-chapter" not in {row["id"] for row in selected}
    assert selected[0]["context_temporal_relation"] == "scene"
    assert selected[0]["context_locality_score"] == 1.0
    assert next(row for row in selected if row["id"] == "F-prior")["context_temporal_relation"] == "prior_chapter"
    state_row = next(row for row in selected if row["id"] == "F-state")
    assert state_row["context_irreversible_state"] is True
    assert state_row["context_resident_state"] is True


def test_strategy_writer_context_uses_real_engine_and_scene_time_boundary(tmp_path):
    harness = LongformBenchmarkHarness(
        tmp_path / "benchmarks",
        embeddings_factory=lambda: _DeterministicEmbedder(),
    )
    brief = {
        "id": "SB-C0003-01",
        "chapter_id": "C0003",
        "brief": "续写林舟调查灯塔钥匙。",
        "prior_summary": "林舟确认管家保管过钥匙。",
        "resident_context": "林舟走到灯塔门前。",
        "source_end_ratio": 0.5,
    }
    facts = [
        {
            "id": "F-key",
            "statement": "管家一直保管灯塔钥匙。",
            "chapter_id": "C0002",
            "source_position_ratio": 0.8,
        },
        {
            "id": "F-future-scene",
            "statement": "林舟稍后发现钥匙藏在钟楼。",
            "chapter_id": "C0003",
            "source_position_ratio": 0.8,
        },
        {
            "id": "F-future-chapter",
            "statement": "下一章管家承认转移了灯塔钥匙。",
            "chapter_id": "C0004",
            "source_position_ratio": 0.1,
        },
    ]

    eligible, filters = LongformBenchmarkHarness._temporally_valid_strategy_facts(brief, facts)
    selected = asyncio.run(
        harness.pipeline.generation.select_strategy_context(
            benchmark_id="demo",
            brief=brief,
            facts=eligible,
            spec=harness.pipeline.generation.resolve_strategy("jit_hybrid"),
            engine=harness.pipeline.generation.create_strategy_engine(
                harness.pipeline.generation.resolve_strategy("jit_hybrid")
            ),
            top_k=10,
            temporal_filters=filters,
        )
    )

    assert [row["id"] for row in selected["facts"]] == ["F-key"]
    assert selected["strategy_fidelity"] is True
    assert selected["execution_signature"] == "semantic:rrf:no_rerank:top10"
    assert selected["ranking_trace"]["filters"]["future_scene_facts_excluded"] == 1
    assert selected["ranking_trace"]["filters"]["future_chapters_excluded"] == 1
    assert selected["facts"][0]["context_temporal_relation"] == "prior_chapter"
    assert selected["facts"][0]["context_subject_entities"]


def test_merge_calibration_rows_replaces_scene_variant_without_duplicate_counts():
    existing = [
        {
            "id": "CAL-old-full",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "full_context",
        },
        {
            "id": "CAL-low",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "low_context",
        },
    ]
    updates = [
        {
            "id": "CAL-new-full",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "full_context",
        }
    ]

    merged = LongformBenchmarkHarness._merge_calibration_rows(existing, updates)

    assert len(merged) == 2
    assert {row["id"] for row in merged} == {"CAL-low", "CAL-new-full"}


def test_calibration_candidate_response_normalizes_clean_and_nested_json():
    prose = "林舟站在走廊尽头，听见楼下传来短促的敲击声。" * 4
    clean = LongformBenchmarkHarness._normalize_calibration_candidate_response(
        json.dumps({"candidate_text": prose, "self_check": {"ok": True}}, ensure_ascii=False)
    )
    nested = LongformBenchmarkHarness._normalize_calibration_candidate_response(
        json.dumps({"candidate_text": json.dumps({"candidate_text": prose}, ensure_ascii=False)}, ensure_ascii=False)
    )

    assert clean["candidate_text"] == prose
    assert clean["self_check"] == {"ok": True}
    assert clean["generation_quality"] == "clean_json"
    assert nested["candidate_text"] == prose
    assert nested["generation_quality"] == "nested_json_repaired"


def test_calibration_candidate_response_rejects_malformed_or_too_short_output():
    malformed = LongformBenchmarkHarness._normalize_calibration_candidate_response('{"candidate_text": "林舟走进大厅"')
    plain = LongformBenchmarkHarness._normalize_calibration_candidate_response("林舟走进大厅。")
    short = LongformBenchmarkHarness._normalize_calibration_candidate_response(
        json.dumps({"candidate_text": "林舟走进大厅。"}, ensure_ascii=False)
    )

    assert malformed["candidate_text"] == ""
    assert malformed["generation_quality"] == "malformed_json"
    assert plain["generation_quality"] == "non_json_response"
    assert short["reason"] == "too_short_candidate"


def test_calibration_judge_case_scores_candidate_not_reference():
    row = {
        "task_type": "llm_continuation_quality",
        "writer_variant": "full_context",
        "generation_quality": "clean_json",
        "canon_summary": "钥匙一直在管家手里。",
        "prior_summary": "林舟来到码头。",
        "scene_brief": "续写林舟调查灯塔。",
        "candidate_text": "候选正文应该被评分。" * 10,
        "chapter_text": "旧字段不应覆盖 candidate_text。",
        "reference_excerpt": "参考摘录只用于理解语境。",
    }

    case = LongformBenchmarkHarness._calibration_row_to_judge_case(row)

    assert case["candidate_text"].startswith("候选正文")
    assert case["chapter_text"].startswith("候选正文")
    assert case["reference_excerpt"] == "参考摘录只用于理解语境。"
    assert case["writer_variant"] == "full_context"
    assert case["generation_quality"] == "clean_json"


def test_judge_human_agreement_reports_bias_and_distribution():
    rows = [
        {"human_overall_score": 4.0, "judge_overall_score": 5.0, "judge_success": True},
        {"human_overall_score": 2.0, "judge_overall_score": 2.5, "judge_success": True},
        {"human_overall_score": 1.0, "judge_overall_score": 1.0, "judge_success": True},
        {"human_overall_score": 3.0, "judge_success": False},
        {"human_overall_score": 3.0, "judge_skipped_reason": "explicit_sexual_content"},
    ]

    agreement = LongformBenchmarkHarness._calculate_judge_human_agreement(rows)

    assert agreement["available"] is True
    assert agreement["num_cases"] == 3
    assert agreement["attempted_cases"] == 4
    assert agreement["failed_judge_rows"] == 1
    assert agreement["safety_skipped_rows"] == 1
    assert agreement["scoreable_rate"] == 0.75
    assert round(agreement["mae"], 2) == 0.5
    assert round(agreement["mean_bias"], 2) == 0.5
    assert agreement["within_one_point"] == 1.0
    assert agreement["within_half_point"] == 2 / 3


def test_calibration_pairwise_helpers_compare_full_and_low_context():
    rows = [
        {
            "id": "full",
            "chapter_id": "C0001",
            "writer_variant": "full_context",
            "human_overall_score": 4.0,
            "candidate_text": "full candidate",
        },
        {
            "id": "low",
            "chapter_id": "C0001",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
            "candidate_text": "low candidate",
        },
    ]

    pairs = LongformBenchmarkHarness._calibration_context_pairs(rows)
    case = LongformBenchmarkHarness._calibration_pair_to_judge_case(pairs[0]["a"], pairs[0]["b"])
    winner = LongformBenchmarkHarness._pairwise_winner_from_position_swap(
        {"judge": {"winner": "A"}},
        {"judge": {"winner": "B"}},
    )
    agreement = LongformBenchmarkHarness._calculate_pairwise_human_agreement(
        [
            {
                "human_winner": "A",
                "judge_winner": winner,
                "position_consistent": True,
            }
        ]
    )

    assert len(pairs) == 1
    assert pairs[0]["human_winner"] == "A"
    assert case["candidate_a"] == "full candidate"
    assert case["candidate_b"] == "low candidate"
    assert winner == "A"
    assert agreement["score"] == 1.0
    assert agreement["comparable_rate"] == 1.0
    assert agreement["min_pairs"] == 20
    assert agreement["gate_passed"] is False


def test_calibration_pairwise_helpers_pair_by_scene_when_available():
    rows = [
        {
            "id": "full-1",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "full_context",
            "human_overall_score": 4.0,
            "candidate_text": "full scene one",
        },
        {
            "id": "low-1",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
            "candidate_text": "low scene one",
        },
        {
            "id": "full-2",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-02",
            "writer_variant": "full_context",
            "human_overall_score": 2.0,
            "candidate_text": "full scene two",
        },
        {
            "id": "low-2",
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-02",
            "writer_variant": "low_context",
            "human_overall_score": 3.0,
            "candidate_text": "low scene two",
        },
    ]

    pairs = LongformBenchmarkHarness._calibration_context_pairs(rows)

    assert len(pairs) == 2
    assert [pair["scene_id"] for pair in pairs] == ["SB-C0001-01", "SB-C0001-02"]
    assert [pair["human_winner"] for pair in pairs] == ["A", "B"]


def test_strategy_ab_analysis_requires_output_evidence_and_can_pass_full_gate(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    candidates = []
    pairwise_rows = []
    for index in range(100):
        scene_index = index // 2
        trial = (index % 2) + 1
        pair_id = f"SAB-scene-{scene_index:03d}-T{trial:03d}"
        common = {
            "pair_id": pair_id,
            "chapter_id": f"C{scene_index + 1:04d}",
            "scene_id": f"SB-C{scene_index + 1:04d}-01",
            "trial": trial,
            "writer_provider": "deepseek",
            "writer_model": "deepseek-chat",
            "generation_config": {"temperature": 0.7, "max_tokens": 2200, "provider_seed_requested": False},
            "prior_summary": "共同前文",
            "resident_context": "共同近邻上下文",
            "judge_canon_summary": "共同评估事实",
            "generation_latency_ms": 1000,
        }
        first = {
            **common,
            "id": f"{pair_id}-A",
            "strategy_role": "A",
            "retrieval_strategy": "bm25",
                "candidate_text": "候选 A 的场景继续推进。" * 40,
            "canon_refs": ["F1"],
            "retrieval_execution": {
                "strategy_fidelity": True,
                "execution_signature": "lexical:top10",
                "latency_ms": 20,
            },
            "gateway_usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            "context_pack_stats": {"fact_count": 5, "total_token_estimate": 500},
        }
        second = {
            **common,
            "id": f"{pair_id}-B",
            "strategy_role": "B",
            "retrieval_strategy": "jit_hybrid",
                "candidate_text": "候选 B 的场景继续推进。" * 40,
            "canon_refs": ["F1", "F2"],
            "retrieval_execution": {
                "strategy_fidelity": True,
                "execution_signature": "semantic:rrf:no_rerank:top10",
                "latency_ms": 120,
            },
            "gateway_usage": {"prompt_tokens": 1100, "completion_tokens": 500, "total_tokens": 1600},
            "context_pack_stats": {"fact_count": 6, "total_token_estimate": 560},
        }
        candidates.extend([first, second])
        pairwise_rows.append(
            {
                "pair_id": pair_id,
                "chapter_id": common["chapter_id"],
                "scene_id": common["scene_id"],
                "strategy_a": "bm25",
                "strategy_b": "jit_hybrid",
                "success": True,
                "available": True,
                "position_consistent": True,
                "judge_winner": "B",
                "judge_provider": "deepseek",
                "judge_model": "deepseek-chat",
                "judge_prompt_version": "context-quality-v4-pointwise",
                "attempts": [
                    {
                        "forward_provider": "deepseek",
                        "forward_model": "deepseek-chat",
                        "swapped_provider": "deepseek",
                        "swapped_model": "deepseek-chat",
                    }
                ],
                "pair_fingerprint": harness.pipeline.ledger.strategy_pair_fingerprint(first, second),
            }
        )

    summary = harness.analyze_strategy_ab(
        benchmark_id="demo",
        candidates=candidates,
        pairwise_rows=pairwise_rows,
    )

    assert summary["candidate_pairs"] == 100
    assert summary["strategy_b_preference"] == 1.0
    assert summary["strategy_b_preference_ci95"]["lower"] == 1.0
    assert summary["sample_size_ready"] is True
    assert summary["quality_gate_passed"] is True
    assert summary["corpus_gate_passed"] is True
    assert summary["adoption_gate_passed"] is False
    assert summary["recommendation"] == "eligible_for_cross_corpus_compare"
    assert summary["independent_scenes"] == 50
    assert summary["min_trials_per_scene"] == 2
    assert summary["strategy_a"]["strategy"] == "bm25"
    assert summary["strategy_b"]["strategy"] == "jit_hybrid"


def test_strategy_ab_analysis_rejects_small_or_stale_samples(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    first = {
        "id": "pair-A",
        "pair_id": "pair",
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "strategy_role": "A",
        "retrieval_strategy": "bm25",
        "candidate_text": "候选 A" * 40,
        "writer_provider": "deepseek",
        "writer_model": "deepseek-chat",
        "generation_config": {"temperature": 0.7, "max_tokens": 2200},
        "retrieval_execution": {"strategy_fidelity": True, "execution_signature": "lexical:top10"},
        "gateway_usage": {"prompt_tokens": 1000},
    }
    second = {
        **first,
        "id": "pair-B",
        "strategy_role": "B",
        "retrieval_strategy": "jit_hybrid",
        "candidate_text": "候选 B" * 40,
        "retrieval_execution": {
            "strategy_fidelity": True,
            "execution_signature": "semantic:rrf:no_rerank:top10",
        },
    }
    stale = {
        "pair_id": "pair",
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "success": True,
        "position_consistent": True,
        "judge_winner": "B",
        "judge_prompt_version": "context-quality-v4-pointwise",
        "pair_fingerprint": "stale",
    }

    summary = harness.analyze_strategy_ab(
        benchmark_id="demo",
        candidates=[first, second],
        pairwise_rows=[stale],
    )

    assert summary["sample_size_ready"] is False
    assert summary["stale_pairwise_rows"] == 1
    assert summary["adoption_gate_passed"] is False
    assert "sample_size_ready" in summary["gate_reasons"]
    assert "no_stale_rows" in summary["gate_reasons"]


def test_strategy_ab_analysis_reports_raw_and_stabilized_judge_rates(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    common = {
        "pair_id": "pair",
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "trial": 1,
        "writer_provider": "qwen",
        "writer_model": "qwen-model",
        "generation_config": {"temperature": 0.7, "max_tokens": 2200},
        "prior_summary": "前文",
        "resident_context": "近邻正文",
        "judge_canon_summary": "评估事实",
        "gateway_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    first = {
        **common,
        "id": "pair-A",
        "strategy_role": "A",
        "retrieval_strategy": "bm25",
        "candidate_text": "候选 A",
        "canon_summary": "事实 A",
        "retrieval_execution": {"strategy_fidelity": True, "execution_signature": "a"},
    }
    second = {
        **common,
        "id": "pair-B",
        "strategy_role": "B",
        "retrieval_strategy": "jit_hybrid",
        "candidate_text": "候选 B",
        "canon_summary": "事实 B",
        "retrieval_execution": {"strategy_fidelity": True, "execution_signature": "b"},
    }
    pairwise = {
        "pair_id": "pair",
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "position_consistent": True,
        "judge_winner": "B",
        "judge_provider": "deepseek",
        "judge_model": "deepseek-model",
        "judge_prompt_version": "context-quality-v4-pointwise",
        "attempt_count": 2,
        "attempts": [
            {
                "position_consistent": False,
                "judge_winner": None,
                "forward_provider": "deepseek",
                "forward_model": "deepseek-model",
                "swapped_provider": "deepseek",
                "swapped_model": "deepseek-model",
            },
            {
                "position_consistent": True,
                "judge_winner": "B",
                "forward_provider": "deepseek",
                "forward_model": "deepseek-model",
                "swapped_provider": "deepseek",
                "swapped_model": "deepseek-model",
            },
        ],
        "pair_fingerprint": harness.pipeline.ledger.strategy_pair_fingerprint(first, second),
    }

    summary = harness.analyze_strategy_ab(
        benchmark_id="demo",
        candidates=[first, second],
        pairwise_rows=[pairwise],
    )

    assert summary["first_attempt_comparable_rate"] == 0.0
    assert summary["first_attempt_position_consistency"] == 0.0
    assert summary["comparable_rate"] == 1.0
    assert summary["position_consistency"] == 1.0
    assert summary["stabilized_pairs"] == 1


def test_strategy_ab_cross_corpus_gate_is_provider_scoped_until_cross_provider_evidence(tmp_path):
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    for corpus_index, benchmark_id in enumerate(("native_zh", "longform_zh")):
        candidates = []
        pairwise_rows = []
        for index in range(50):
            scene_index = index // 2
            trial = (index % 2) + 1
            pair_id = f"SAB-{benchmark_id}-{scene_index:03d}-T{trial:03d}"
            common = {
                "pair_id": pair_id,
                "chapter_id": f"C{scene_index + 1:04d}",
                "scene_id": f"SB-{benchmark_id}-{scene_index:03d}",
                "trial": trial,
                "writer_provider": "deepseek",
                "writer_model": "deepseek-chat",
                "generation_config": {"temperature": 0.7, "max_tokens": 2200},
                "prior_summary": "共同前文",
                "resident_context": "共同上下文",
                "judge_canon_summary": "共同评估事实",
                "generation_latency_ms": 1000 + corpus_index,
            }
            first = {
                **common,
                "id": f"{pair_id}-A",
                "strategy_role": "A",
                "retrieval_strategy": "bm25",
                "candidate_text": "候选 A 的场景继续推进。" * 40,
                "canon_refs": ["F1"],
                "canon_summary": "事实一",
                "retrieval_execution": {
                    "strategy_fidelity": True,
                    "execution_signature": "lexical:top10",
                    "latency_ms": 20,
                },
                "gateway_usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            }
            second = {
                **common,
                "id": f"{pair_id}-B",
                "strategy_role": "B",
                "retrieval_strategy": "jit_hybrid",
                "candidate_text": "候选 B 的场景继续推进。" * 40,
                "canon_refs": ["F1", "F2"],
                "canon_summary": "事实一\n事实二",
                "retrieval_execution": {
                    "strategy_fidelity": True,
                    "execution_signature": "semantic:rrf:no_rerank:top10",
                    "latency_ms": 120,
                },
                "gateway_usage": {"prompt_tokens": 1100, "completion_tokens": 500, "total_tokens": 1600},
            }
            candidates.extend([first, second])
            pairwise_rows.append(
                {
                    "pair_id": pair_id,
                    "chapter_id": common["chapter_id"],
                    "scene_id": common["scene_id"],
                    "strategy_a": "bm25",
                    "strategy_b": "jit_hybrid",
                    "success": True,
                    "available": True,
                    "position_consistent": True,
                    "judge_winner": "B",
                    "judge_provider": "deepseek",
                    "judge_model": "deepseek-chat",
                    "judge_prompt_version": "context-quality-v4-pointwise",
                    "attempts": [
                        {
                            "forward_provider": "deepseek",
                            "forward_model": "deepseek-chat",
                            "swapped_provider": "deepseek",
                            "swapped_model": "deepseek-chat",
                        }
                    ],
                    "pair_fingerprint": harness.pipeline.ledger.strategy_pair_fingerprint(first, second),
                }
            )
        paths = harness.paths(benchmark_id)
        write_jsonl(paths.generated_dir / "strategy_ab_candidates.jsonl", candidates)
        write_jsonl(paths.generated_dir / "strategy_ab_pairwise_judge_20260710_000000.jsonl", pairwise_rows)

    summary = harness.compare_strategy_ab_corpora(benchmark_ids=["native_zh", "longform_zh"])

    assert summary["comparable_pairs"] == 100
    assert summary["cross_corpus_gate_passed"] is True
    assert summary["provider_scoped_adoption_gate_passed"] is True
    assert summary["global_adoption_gate_passed"] is False
    assert summary["adoption_gate_passed"] is False
    assert summary["recommendation"] == "adopt_strategy_b_for_provider_scope"


def test_pairwise_merge_replaces_matching_scene_rows():
    existing = [
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "judge_winner": None,
        },
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-02",
            "judge_winner": "A",
        },
    ]
    updates = [
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "judge_winner": "B",
        }
    ]

    merged = LongformBenchmarkHarness._merge_pairwise_rows(existing, updates)

    assert len(merged) == 2
    assert [row["scene_id"] for row in merged] == ["SB-C0001-02", "SB-C0001-01"]
    assert merged[-1]["judge_winner"] == "B"


def test_unjudged_human_context_failures_use_scene_ids():
    calibration_rows = [
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "full_context",
            "human_overall_score": 1.0,
        },
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-01",
            "writer_variant": "low_context",
            "human_overall_score": 3.0,
        },
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-02",
            "writer_variant": "full_context",
            "human_overall_score": 2.0,
        },
        {
            "chapter_id": "C0001",
            "scene_id": "SB-C0001-02",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
        },
    ]

    failures = LongformBenchmarkHarness._calibration_failure_rows(
        benchmark_id="demo",
        calibration_rows=calibration_rows,
        pairwise_rows=[],
    )

    assert [row["category"] for row in failures] == [
        "low_context_beats_full_context",
        "no_measured_context_gain",
    ]
    assert [row["source_id"] for row in failures] == ["PAIR-SB-C0001-01", "PAIR-SB-C0001-02"]
    assert len({row["id"] for row in failures}) == 2


def test_pairwise_attempt_selection_uses_first_position_consistent_result():
    inconsistent = LongformBenchmarkHarness._pairwise_attempt_summary(
        1,
        {
            "available": True,
            "success": True,
            "judge": {"winner": "A"},
            "provider": "deepseek",
            "model": "deepseek-chat",
        },
        {
            "available": True,
            "success": True,
            "judge": {"winner": "A"},
            "provider": "deepseek",
            "model": "deepseek-chat",
        },
    )
    consistent = LongformBenchmarkHarness._pairwise_attempt_summary(
        2,
        {"available": True, "success": True, "judge": {"winner": "B"}},
        {"available": True, "success": True, "judge": {"winner": "A"}},
    )
    selected = LongformBenchmarkHarness._select_pairwise_attempt([inconsistent, consistent])

    assert inconsistent["position_consistent"] is False
    assert inconsistent["judge_winner"] is None
    assert consistent["position_consistent"] is True
    assert consistent["judge_winner"] == "B"
    assert selected["attempt"] == 2
    assert selected["judge_winner"] == "B"
    assert inconsistent["judge_provider"] == "deepseek"
    assert inconsistent["judge_model"] == "deepseek-chat"
    assert inconsistent["forward_provider"] == "deepseek"
    assert inconsistent["swapped_model"] == "deepseek-chat"


def test_pairwise_result_persists_selected_judge_identity(monkeypatch, tmp_path):
    import app.eval.longform_benchmark as benchmark_module

    async def _judge(*args, **kwargs):
        return {
            "available": True,
            "success": True,
            "judge": {
                "winner": "B",
                "score_a": 2.0,
                "score_b": 4.0,
                "score_delta_b_minus_a": 2.0,
            },
            "provider": "deepseek",
            "model": "deepseek-chat",
            "prompt_version": "context-quality-v4-pointwise",
            "comparison_method": "independent_pointwise_weighted",
            "order_invariant": True,
            "usage_rows": [{}, {}],
            "error": "",
        }

    monkeypatch.setattr(benchmark_module, "run_pointwise_pair_judge_eval", _judge)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    pair = {
        "chapter_id": "C0001",
        "scene_id": "SB-C0001-01",
        "human_winner": "B",
        "a": {"id": "full", "candidate_text": "A", "human_overall_score": 2.0},
        "b": {"id": "low", "candidate_text": "B", "human_overall_score": 4.0},
    }
    row, _ = asyncio.run(
        harness.pipeline.judge.pairwise_with_retries(
            pair=pair,
            case={"candidate_a": "A", "candidate_b": "B"},
            provider="deepseek",
            require_judge=True,
            max_attempts=1,
        )
    )

    assert row["success"] is True
    assert row["judge_provider"] == "deepseek"
    assert row["judge_model"] == "deepseek-chat"
    assert row["attempts"][0]["candidate_a_model"] == "deepseek-chat"
    assert row["comparison_method"] == "independent_pointwise_weighted"


def test_scored_calibration_targets_require_both_variants(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    harness.import_corpus(source=source, benchmark_id="demo")
    paths = harness.paths("demo")
    rows = [
        {
            "id": "CAL-C0001-full_context-001",
            "chapter_id": "C0001",
            "writer_variant": "full_context",
            "human_overall_score": 4.0,
        },
        {
            "id": "CAL-C0001-low_context-002",
            "chapter_id": "C0001",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
        },
        {
            "id": "CAL-C0002-full_context-003",
            "chapter_id": "C0002",
            "scene_id": "SB-C0002-01",
            "writer_variant": "full_context",
            "human_overall_score": 3.0,
        },
        {
            "id": "CAL-C0002-low_context-004",
            "chapter_id": "C0002",
            "scene_id": "SB-C0002-01",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
        },
        {
            "id": "CAL-C0002-full_context-005",
            "chapter_id": "C0002",
            "scene_id": "SB-C0002-02",
            "writer_variant": "full_context",
            "human_overall_score": 3.0,
        },
    ]
    (paths.gold_dir / "calibration_set.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    scored_scenes, legacy_chapters = LongformBenchmarkHarness._scored_calibration_targets(paths)

    assert scored_scenes == {"SB-C0002-01"}
    assert legacy_chapters == {"C0001"}


def test_generate_writing_calibration_filters_scene_ids_and_appends(tmp_path, monkeypatch):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    harness.import_corpus(source=source, benchmark_id="demo", allow_external_api=True)
    asyncio.run(harness.generate_candidates(benchmark_id="demo", scene_windows=2))
    paths = harness.paths("demo")
    scene_briefs = read_jsonl(paths.generated_dir / "candidate_scene_briefs.jsonl")
    first_scene = scene_briefs[0]
    target_scene = scene_briefs[-1]
    first_scene_id = first_scene["id"]
    target_scene_id = target_scene["id"]
    calls = []

    class FakeGateway:
        def get_provider_for_agent(self, agent):
            return "fake-writer"

        async def chat(self, messages, **kwargs):
            calls.append({"messages": messages, "kwargs": kwargs})
            prose = "林舟顺着潮湿的石阶往下走，仍然记着钥匙和钟声之间的细微联系。" * 5
            return {
                "content": json.dumps(
                    {"candidate_text": prose, "self_check": {"context_used": True}},
                    ensure_ascii=False,
                ),
                "provider": "fake-writer",
                "model": "fake-model",
                "usage": {"total_tokens": 42},
            }

    monkeypatch.setattr("app.eval.longform_benchmark.get_gateway", lambda: FakeGateway())
    monkeypatch.setattr(
        "app.eval.longform_benchmark.judge_extra_body",
        lambda _profile_id: {"thinking": {"type": "disabled"}},
    )

    result = asyncio.run(
        harness.generate_writing_calibration(
            benchmark_id="demo",
            limit=10,
            variants=["full_context"],
            scene_ids=[target_scene_id],
        )
    )
    rows = read_jsonl(paths.generated_dir / "calibration_candidates.jsonl")

    assert result["success"] is True
    assert result["generated"] == 1
    assert result["scene_ids"] == [target_scene_id]
    assert len(calls) == 1
    assert calls[0]["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert rows[0]["scene_id"] == target_scene_id
    assert rows[0]["scene_index"] == target_scene["scene_index"]
    assert rows[0]["writer_variant"] == "full_context"

    appended = asyncio.run(
        harness.generate_writing_calibration(
            benchmark_id="demo",
            limit=10,
            variants=["low_context"],
            scene_ids=[first_scene_id],
            append=True,
        )
    )
    rows = read_jsonl(paths.generated_dir / "calibration_candidates.jsonl")

    assert appended["success"] is True
    assert appended["append"] is True
    assert appended["generated"] == 1
    assert appended["current_total"] == 2
    assert len(rows) == 2
    assert {row["scene_id"] for row in rows} == {target_scene_id, first_scene_id}


def test_analyze_calibration_writes_anonymized_failure_rows(tmp_path):
    source = _write_corpus(tmp_path)
    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    harness.import_corpus(source=source, benchmark_id="demo")
    paths = harness.paths("demo")
    calibration_rows = [
        {
            "id": "CAL-C0001-full_context-001",
            "chapter_id": "C0001",
            "writer_variant": "full_context",
            "human_overall_score": 4.0,
            "judge_overall_score": 5.0,
            "judge_success": True,
            "context_pack_stats": {"fact_count": 2, "avg_rank_score": 0.6, "token_estimate": 80},
        },
        {
            "id": "CAL-C0001-low_context-002",
            "chapter_id": "C0001",
            "writer_variant": "low_context",
            "human_overall_score": 2.0,
            "judge_success": False,
            "context_pack_stats": {"fact_count": 0, "avg_rank_score": 0.0, "token_estimate": 0},
        },
    ]
    pair = harness.pipeline.ledger.calibration_pairs(calibration_rows)[0]
    pairwise_rows = [
        {
            "chapter_id": "C0001",
            "human_winner": "A",
            "judge_winner": "B",
            "position_consistent": True,
            "forward_winner": "B",
            "swapped_winner": "A",
            "judge_prompt_version": "context-quality-v4-pointwise",
            "pair_fingerprint": harness.pipeline.ledger.calibration_pair_fingerprint(pair["a"], pair["b"]),
        },
        {
            "chapter_id": "C0002",
            "human_winner": "A",
            "judge_winner": "",
            "position_consistent": False,
            "forward_winner": "A",
            "swapped_winner": "A",
        },
    ]
    (paths.gold_dir / "calibration_set.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in calibration_rows) + "\n",
        encoding="utf-8",
    )
    (paths.generated_dir / "calibration_pairwise_judge_20260705_000000.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pairwise_rows) + "\n",
        encoding="utf-8",
    )
    (paths.generated_dir / "calibration_candidates.jsonl").write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {
                    "id": "GEN-full",
                    "writer_variant": "full_context",
                    "candidate_text": "候选正文" * 30,
                    "generation_quality": "clean_json",
                    "context_pack_stats": {"fact_count": 2, "avg_rank_score": 0.6, "token_estimate": 80},
                },
                {
                    "id": "GEN-low",
                    "writer_variant": "low_context",
                    "candidate_text": "低上下文正文" * 20,
                    "generation_quality": "clean_json",
                    "context_pack_stats": {"fact_count": 0, "avg_rank_score": 0.0, "token_estimate": 0},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (paths.generated_dir / "calibration_generation_failures.jsonl").write_text(
        json.dumps(
            {
                "chapter_id": "C0003",
                "variant": "full_context",
                "reason": "malformed_candidate_json",
                "generation_quality": "malformed_json",
                "raw_response_sha256": "abc123",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = harness.analyze_calibration(benchmark_id="demo")
    failures = read_jsonl(paths.generated_dir / "calibration_failures.jsonl")

    assert summary["success"] is True
    assert summary["human_by_variant"]["full_context"]["avg"] == 4.0
    assert summary["human_by_variant"]["full_minus_low_avg"] == 2.0
    assert summary["context_pack_by_variant"]["full_context"]["avg_fact_count"] == 2.0
    assert summary["generated_candidates"]["count"] == 2
    assert summary["generated_candidates"]["by_variant"]["full_context"]["generation_quality"]["clean_json"] == 1
    assert summary["generated_candidates"]["by_variant"]["full_context"]["context_pack"]["avg_fact_count"] == 2.0
    assert summary["generation_failures"]["count"] == 1
    assert summary["generation_failures"]["by_reason"]["malformed_candidate_json"] == 1
    assert summary["failure_counts"]["candidate_generation_failed"] == 1
    assert summary["failure_counts"]["rubric_judge_unscoreable"] == 1
    assert summary["failure_counts"]["pairwise_judge_human_disagreement"] == 1
    assert summary["stale_pairwise_rows"] == 1
    assert summary["pairwise"]["human_winner_distribution"]["A"] == 1
    assert failures
    assert all(row["contains_corpus_text"] is False for row in failures)

    promoted = harness.promote_failures(benchmark_id="demo", run_id="calibration_only", limit=10)
    replay_cases = read_jsonl(paths.generated_dir / "replay_cases.jsonl")

    assert promoted["success"] is True
    assert promoted["calibration_promoted"] == len(failures)
    assert any(row["source"] == "calibration_failure" for row in replay_cases)
    assert all(row.get("contains_corpus_text") is False for row in replay_cases)
    assert all("analyze-calibration" in row.get("replay_command", []) for row in replay_cases)


def test_scene_brief_opening_skips_boilerplate_and_weak_headings():
    assert LongformBenchmarkHarness._is_non_story_chapter({"title": "bookcover", "text": "简介文本"}) is True
    assert LongformBenchmarkHarness._is_non_story_chapter({"title": "目录", "text": "第一章"}) is True
    assert LongformBenchmarkHarness._is_non_story_chapter({"title": "第一章", "text": "林舟来到码头。"}) is False
    assert (
        LongformBenchmarkHarness._is_non_story_scene_brief(
            {
                "id": "SB-C0001",
                "brief": "基于《bookcover》的开篇与核心事实，续写保持人物状态和时间线一致。",
                "prior_summary": "侠客行 金庸 简介 传说中的侠客岛每十年派出赏善罚恶二使来中原。",
            }
        )
        is True
    )
    assert (
        LongformBenchmarkHarness._is_non_story_scene_brief(
            {
                "id": "SB-C0002",
                "brief": "基于《一 玄铁令》的开篇与核心事实，续写保持人物状态和时间线一致。",
                "prior_summary": "一 玄铁令 侯监集上众人散去。",
            }
        )
        is False
    )

    opening = LongformBenchmarkHarness._chapter_opening_context(
        ["一", "第一章 风起", "林舟来到码头，发现灯塔已经熄灭。沈砚告诉林舟，钥匙一直在管家手里。"],
        ["一", "第一章 风起", "林舟来到码头，发现灯塔已经熄灭。", "沈砚告诉林舟，钥匙一直在管家手里。"],
    )

    assert opening.startswith("林舟来到码头")
    assert "钥匙一直在管家手里" in opening

    windows = LongformBenchmarkHarness._chapter_scene_windows(
        [
            "第一幕 林舟来到码头，发现灯塔已经熄灭。沈砚告诉林舟，钥匙一直在管家手里。",
            "第二幕 林舟进入仓库，墙上留着新的划痕，管家否认见过钥匙。",
            "第三幕 夜色降临，沈砚在电话里提到旧码头的钟声，林舟意识到仓库划痕和灯塔熄灭发生在同一小时。",
        ],
        [],
        max_windows=3,
    )

    assert len(windows) == 3
    assert windows[0]["scene_index"] == 1
    assert "第一幕" in windows[0]["text"]
    assert "第二幕" in windows[1]["text"]
    assert "第三幕" in windows[2]["text"]
    assert windows[0]["source_start_ratio"] == 0.0
    assert windows[1]["source_start_ratio"] == 0.5
    assert windows[2]["source_end_ratio"] == 1.0


def test_hard_wrapped_prose_is_joined_before_scene_windowing():
    text = "\n\n".join(
        [
            "雨水从窗檐落下来，打在石阶",
            "上，维拉听见屋里传来脚步声。",
            "她没有回头，只是握紧了手中",
            "的钥匙，沿着走廊继续向前。",
            "远处的钟声再次响起，众人都",
            "停下交谈，望向紧闭的房门。",
        ]
        * 3
    )

    paragraphs = _paragraphs(text)
    sentences = _sentence_split(text)
    windows = LongformBenchmarkHarness._chapter_scene_windows(paragraphs, sentences, max_windows=2)

    assert paragraphs[0].startswith("雨水从窗檐落下来，打在石阶上")
    assert all(not paragraph.endswith("石阶") for paragraph in paragraphs)
    assert all(len(window["text"]) >= 180 for window in windows)
    assert all("脚步声" in window["text"] for window in windows)
    assert _shorten_prose("第一句完整结束。第二句也完整结束。第三句仍然完整结束。", 24).endswith("。")


def test_resident_context_uses_only_text_before_scene_and_previous_tail_for_opening():
    sentences = [f"第{idx}句说明林舟在仓库里的调查进展。" for idx in range(1, 21)]

    middle = LongformBenchmarkHarness._resident_context_for_scene(sentences, 0.5, "上一章结尾。")
    opening = LongformBenchmarkHarness._resident_context_for_scene(
        sentences,
        0.0,
        "上一章 下一章 回首页 OCR：示例校对 收藏 上一章结尾保持完整。",
    )

    assert "第10句" in middle
    assert "第11句" not in middle
    assert "上一章结尾保持完整" in opening
    assert "第1句" not in opening
    assert "OCR" not in opening
    assert "回首页" not in opening


def test_longform_name_extraction_prefers_precise_character_candidates():
    text = (
        "而且林舟说着。空气清新。他们这样说。"
        "我亲爱的沈砚先生清清嗓子。艾琳·莫尔看向窗外。菲利普·隆巴德点点头。"
    )
    names = LongformBenchmarkHarness._extract_names(text)
    assert "林舟" in names
    assert "沈砚" in names
    assert "艾琳·莫尔" in names
    assert "菲利普·隆巴德" in names
    assert "空气清新" not in names
    assert "他们" not in names
    assert "这样" not in names
    assert "先生" not in names
    assert "而且" not in names
    assert "我亲爱" not in names
    assert "先生问道" not in names
    assert "菲利普·隆巴德点点头" not in names
    assert LongformBenchmarkHarness._clean_name_candidate("OCR") == ""
    assert LongformBenchmarkHarness._clean_name_candidate("上海·某校对") == ""
    assert LongformBenchmarkHarness._clean_name_candidate("菲利普·隆巴德慢悠悠地说") == "菲利普·隆巴德"
    assert LongformBenchmarkHarness._clean_name_candidate("菲利普·隆巴德的习惯是天") == "菲利普·隆巴德"
    assert LongformBenchmarkHarness._clean_name_candidate("阿姆斯特朗回答") == "阿姆斯特朗"
    assert LongformBenchmarkHarness._clean_name_candidate("挨着埃米莉·布伦特") == "埃米莉·布伦特"
    assert LongformBenchmarkHarness._clean_name_candidate("瞪着他") == ""
    assert LongformBenchmarkHarness._clean_name_candidate("摸摸下巴颏") == ""
    pruned = LongformBenchmarkHarness._prune_character_lexicon(
        [
            {"name": "罗杰斯", "confidence": 0.9, "count": 5},
            {"name": "杰斯", "confidence": 0.8, "count": 5},
            {"name": "艾琳·莫尔", "confidence": 0.9, "count": 3},
            {"name": "莫尔", "confidence": 0.8, "count": 3},
            {"name": "艾琳·莫尔看向窗外", "confidence": 0.7, "count": 3},
            {"name": "艾琳·莫", "confidence": 0.7, "count": 3},
        ]
    )
    assert [row["name"] for row in pruned] == ["罗杰斯", "艾琳·莫尔"]


def test_character_lexicon_requires_general_evidence_not_single_corpus_aliases():
    chapters = [
        {
            "id": "C0001",
            "text": "林舟来到码头。沈砚问林舟是否要等。空气清新，而且这样很好。艾琳·莫尔看向窗外。",
        },
        {
            "id": "C0002",
            "text": "沈砚告诉林舟钥匙丢了。林舟问沈砚昨夜在哪里。罗医生走进大厅。",
        },
    ]
    names = [row["name"] for row in LongformBenchmarkHarness._build_character_lexicon(chapters)]
    assert "林舟" in names
    assert "沈砚" in names
    assert "艾琳·莫尔" in names
    assert "罗" not in names
    assert "空气清新" not in names
    assert "这样" not in names


def test_character_name_cleaning_trims_general_action_tails():
    assert LongformBenchmarkHarness._clean_name_candidate("程昊刚要") == "程昊"
    assert LongformBenchmarkHarness._clean_name_candidate("何姗本想") == "何姗"
    assert LongformBenchmarkHarness._clean_name_candidate("陈树发终于回") == "陈树发"
    assert LongformBenchmarkHarness._clean_name_candidate("白明礼是我") == "白明礼"
    assert LongformBenchmarkHarness._clean_name_candidate("张萱儿") == "张萱儿"

    chapters = [
        {
            "id": "C0001",
            "text": "程昊刚要说道。程昊问道。何姗本想追问。陈树发终于回答。陈树发说道。",
        },
        {"id": "C0002", "text": "程昊说道。何姗说道。陈树发问道。"},
    ]
    names = [row["name"] for row in LongformBenchmarkHarness._build_character_lexicon(chapters)]
    assert "程昊" in names
    assert "陈树发" in names
    assert "程昊刚要" not in names
    assert "何姗本想" not in names
    assert "陈树发终于回" not in names


def test_character_state_probe_accepts_strong_action_context_names_only():
    names = LongformBenchmarkHarness._probe_character_names(
        [
            {
                "name": "程昊",
                "reasons": ["action_context"],
                "confidence": 0.91,
                "count": 8,
                "chapter_count": 3,
            },
            {
                "name": "偶尔",
                "reasons": ["action_context"],
                "confidence": 0.58,
                "count": 3,
                "chapter_count": 2,
            },
            {
                "name": "一声",
                "reasons": ["action_context"],
                "confidence": 0.91,
                "count": 8,
                "chapter_count": 4,
            },
            {
                "name": "如此",
                "reasons": ["action_context"],
                "confidence": 0.91,
                "count": 8,
                "chapter_count": 4,
            },
            {
                "name": "白明礼",
                "reasons": ["title_suffix"],
                "confidence": 0.62,
                "count": 1,
                "chapter_count": 1,
            },
        ]
    )
    assert "程昊" in names
    assert "白明礼" in names
    assert "偶尔" not in names
    assert "一声" not in names
    assert "如此" not in names


def test_query_from_fact_uses_entity_template_only_for_clean_names():
    assert LongformBenchmarkHarness._query_from_fact("李明来到码头，发现灯塔已经熄灭。").startswith("李明相关")
    assert LongformBenchmarkHarness._query_from_fact("几乎要离开房间，窗外的雨还在下。").startswith("检索事实")
    assert LongformBenchmarkHarness._query_from_fact("偶尔想起旧事，雨声还在窗外。").startswith("检索事实")
    assert LongformBenchmarkHarness._query_from_fact("强撑着打开窗户，冷风吹进大厅。").startswith("检索事实")
    assert LongformBenchmarkHarness._query_from_fact("至于陈舟说道，钥匙一直在管家手里。").startswith("陈舟相关")
    assert LongformBenchmarkHarness._query_from_fact("便和白明离开大厅，众人沉默下来。").startswith("白明相关")


def test_api_safety_filter_blocks_hashed_external_cases(tmp_path, monkeypatch):
    policy_dir = tmp_path / "benchmarks"
    policy_dir.mkdir()
    explicit_token = "redflag"
    age_token = "ageflag"
    (policy_dir / "_private_api_safety_policy.json").write_text(
        json.dumps(
            {
                "explicit_sha256": [hashlib.sha256(explicit_token.encode("utf-8")).hexdigest()],
                "age_context_sha256": [hashlib.sha256(age_token.encode("utf-8")).hexdigest()],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _load_api_safety_policy.cache_clear()

    assert _api_safety_block_reason(f"{explicit_token} {age_token}") == "explicit_with_age_context"
    assert _api_safety_block_reason(f"plain {explicit_token}") == "explicit_sexual_content"
    assert _api_safety_block_reason("ordinary harmless text") is None

    harness = LongformBenchmarkHarness(tmp_path / "benchmarks")
    result = asyncio.run(
        harness.pipeline.statistics.no_context_probe(
            [{"id": "unsafe", "query": f"{explicit_token} {age_token}", "evidence": "same case"}],
            provider="deepseek",
            enabled=True,
            require_available=False,
        )
    )
    assert result["available"] is False
    assert result["reason"] == "all cases blocked_by_api_safety_filter"
    assert result["skipped"] == 1
    _load_api_safety_policy.cache_clear()


def test_ensure_benchmark_gitignore(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("# base\n", encoding="utf-8")
    changed = ensure_benchmark_gitignore(repo)
    assert changed is True
    content = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "benchmarks/" in content
    assert "benchmarks/**/corpus/" in content
    assert "benchmarks/**/generated/" in content
    assert "benchmarks/**/gold/" in content
    assert "benchmarks/**/runs/" in content
    assert "backend/benchmarks/" in content
    assert "backend/benchmarks/**/corpus/" in content
    assert "backend/benchmarks/**/generated/" in content
    assert ensure_benchmark_gitignore(repo) is False
