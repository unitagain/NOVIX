"""P12 lifecycle, recall isolation, compaction and recovery contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.context_engine.compact_artifact import CompactArtifactV2, CompactVerifier
from app.context_engine.context_plan import build_context_plan_v2
from app.context_engine.memory_record import MemoryRecordV2
from app.eval.p12_context_eval import (
    analyze_p12_pairwise,
    assemble_p12_context,
    generate_p12_candidates,
    p12_pair_fingerprint,
)
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.session_history import SessionHistoryStorage
from app.orchestrator.architecture import service_boundaries


def test_memory_record_v2_filters_expiry_trust_and_validity():
    now = datetime.now(timezone.utc)
    record = MemoryRecordV2.from_mapping(
        {
            "slug": "tone",
            "status": "active",
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        }
    )
    assert "expired" in record.recall_block_reasons(())
    future = MemoryRecordV2.from_mapping(
        {"slug": "future", "status": "active", "valid_from": (now + timedelta(days=1)).isoformat()}
    )
    assert "not_yet_valid" in future.recall_block_reasons(())


def test_expired_and_conflicting_memories_never_enter_recall(tmp_path):
    store = CreativeMemoryStorage(str(tmp_path))
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    asyncio.run(store.write_memory("p", "expired", "对白使用短句", "旧偏好", expires_at=expired_at))
    asyncio.run(store.write_memory("p", "formal", "对白使用正式书面语", "决定 A"))
    asyncio.run(store.write_memory("p", "casual", "对白使用自然口语", "决定 B"))
    asyncio.run(store.set_memory_conflicts("p", "formal", ["casual"]))

    hits = asyncio.run(store.recall("p", "对白使用", top_k=10))
    assert hits == []
    assert asyncio.run(store.read_memory("p", "expired"))["status"] == "expired"


def test_supersession_is_traceable_and_old_record_is_not_recalled(tmp_path):
    store = CreativeMemoryStorage(str(tmp_path))
    asyncio.run(store.write_memory("p", "old-tone", "文风采用热烈表达", "旧决定"))
    replacement = asyncio.run(
        store.supersede_memory(
            "p",
            "old-tone",
            "new-tone",
            description="文风采用冷峻表达",
            body="作者最新确认",
            confirmed_by="author",
        )
    )
    assert replacement == "new-tone"
    old = asyncio.run(store.read_memory("p", "old-tone"))
    new = asyncio.run(store.read_memory("p", "new-tone"))
    assert old["status"] == "superseded"
    assert new["supersedes"] == ["old-tone"]
    assert new["confirmed_by"] == "author"
    hits = asyncio.run(store.recall("p", "文风表达", top_k=10))
    assert [item["slug"] for item in hits] == ["new-tone"]
    assert hits[0]["recall_score"] > 0
    assert "conflict=false" in hits[0]["recall_reason"]


async def _structured_summary(_messages):
    return {
        "decisions": ["第三章改为雨夜开场"],
        "constraints": ["不得让凶手提前现身"],
        "entity_state": ["钥匙仍在林岚手中"],
        "open_loops": ["确认码头会面时间"],
        "recent_summary": "作者与助手完成了第三章结构讨论。",
    }


def test_compact_artifact_epoch_verification_and_recovery(tmp_path):
    store = SessionHistoryStorage(str(tmp_path))
    for index in range(12):
        asyncio.run(store.append("p", {"role": "user", "content": f"message-{index}"}))
    result = asyncio.run(store.compact("p", _structured_summary, keep_recent=4, trigger_at=8))
    assert result["compacted"] is True
    assert result["context_epoch"] == 1
    assert result["verification"]["valid"] is True

    artifact = asyncio.run(store.read_compact_artifact("p", result["compact_artifact_id"]))
    recovered = asyncio.run(store.recover_compact_sources("p", result["compact_artifact_id"]))
    assert artifact["constraints"] == ["不得让凶手提前现身"]
    assert len(recovered) == 8
    rebuilt = CompactArtifactV2(**artifact)
    assert CompactVerifier.verify(rebuilt, recovered)["valid"] is True
    active = asyncio.run(store.load("p"))
    assert active[0]["context_epoch"] == 1
    assert len(active) == 5


def test_compact_verifier_rejects_source_drift():
    source = [{"event_id": "e1", "role": "user", "content": "保留约束"}]
    artifact = CompactArtifactV2.from_summary(
        artifact_id="compact_epoch_000001",
        epoch=1,
        parent_epoch=None,
        summary={"recent_summary": "保留约束"},
        source_messages=source,
        recovery_refs=["e1"],
    )
    drifted = [{"event_id": "e1", "role": "user", "content": "内容已变化"}]
    result = CompactVerifier.verify(artifact, drifted)
    assert result["valid"] is False
    assert "source_hash_mismatch" in result["errors"]


def test_compact_semantic_verifier_blocks_commit(tmp_path):
    store = SessionHistoryStorage(str(tmp_path))
    for index in range(10):
        asyncio.run(store.append("p", {"role": "user", "content": f"m-{index}"}))

    async def reject(_artifact, _source):
        return {"available": True, "valid": False, "unsupported_claims": ["无来源事实"]}

    result = asyncio.run(
        store.compact("p", _structured_summary, keep_recent=3, trigger_at=6, semantic_verifier=reject)
    )
    assert result["compacted"] is False
    assert result["error"] == "compact_semantic_verification_failed"
    assert asyncio.run(store.current_context_epoch("p")) == 0
    assert len(asyncio.run(store.load("p"))) == 10


def test_p12_context_variants_hard_filter_memory_and_expose_recovery():
    memories = [
        {"slug": "valid", "status": "active", "description": "有效偏好"},
        {"slug": "rejected", "status": "rejected", "description": "错误偏好"},
    ]
    payload = assemble_p12_context(
        variant="memory_on",
        base_context={"scene": "雨夜"},
        memories=memories,
    )
    assert [item["slug"] for item in payload["creative_memory"]] == ["valid"]
    assert payload["memory_excluded"][0]["reasons"] == ["status:rejected"]
    assert payload["p12_context_fingerprint"]

    recovered = assemble_p12_context(
        variant="recovery_refs_on",
        base_context={},
        compact_artifact={"id": "c1", "epoch": 1, "recent_summary": "摘要"},
        recovered_sources=[{"event_id": "e1", "content": "原始约束"}],
    )
    assert recovered["recovered_sources"][0]["event_id"] == "e1"


def test_p12_pairwise_gate_and_failures_are_goal_directed():
    row = {
            "pair_id": "p1",
            "scene_id": "s1",
            "judge_winner": "A",
            "position_consistent": True,
            "memory_pollution_count": 1,
            "recoverable": False,
            "variant_a": "memory_off",
            "variant_b": "memory_on",
        }
    row["pair_fingerprint"] = p12_pair_fingerprint(row)
    rows = [row]
    result = analyze_p12_pairwise(rows, bootstrap_samples=100)
    assert result["adoption_gate_passed"] is False
    assert result["recommendation"] == "retain_baseline_and_promote_failures"
    assert {row["category"] for row in result["failures"]} >= {
        "memory_pollution",
        "compact_unrecoverable",
        "variant_b_negative_gain",
    }
    assert all(row["contains_corpus_text"] is False for row in result["failures"])


def test_p12_architecture_contract_has_single_owners():
    boundaries = {row["name"]: row for row in service_boundaries()}
    assert boundaries["memory_lifecycle"]["current"] == "MemoryLifecycleService + MemoryRecordV2"
    assert "CompactArtifactV2" in boundaries["compact_lifecycle"]["current"]
    assert "P12 context A/B" in boundaries["benchmark_pipeline"]["current"]


def test_context_plan_uses_persisted_compact_epoch(tmp_path):
    plan = build_context_plan_v2(
        turn_id="turn-1",
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        project_root=tmp_path,
        context_epoch=3,
    )
    assert plan.context_epoch == "3"
    assert plan.fingerprints["plan"]


def test_p12_real_candidate_generator_requires_explicit_distinct_variants():
    class Gateway:
        async def chat(self, messages, **_kwargs):
            context = messages[1]["content"]
            return {
                "content": '{"candidate_text":"候选续写-' + str(len(context)) + '"}',
                "provider": "real-compatible",
                "model": "writer",
                "usage": {"total_tokens": 10},
            }

    case = {
        "pair_id": "p-memory",
        "scene_id": "s-memory",
        "scene_brief": "继续雨夜场景",
        "variants": {
            "memory_off": {"creative_memory": []},
            "memory_on": {"creative_memory": [{"description": "保持克制"}]},
        },
    }
    generated = asyncio.run(generate_p12_candidates([case], gateway=Gateway(), provider="writer"))
    assert generated["pairs"] == 1
    assert {row["variant"] for row in generated["candidates"]} == {"memory_off", "memory_on"}
    assert all(row["candidate_sha256"] for row in generated["candidates"])
