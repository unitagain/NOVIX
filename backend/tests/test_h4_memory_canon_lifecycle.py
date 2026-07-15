"""H4 conservative long-term-state lifecycle and stale-pack contracts."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.context_engine.memory_record import (
    MEMORY_STATUSES_V2,
    MEMORY_TRANSITIONS,
    MemoryRecordV2,
    build_memory_graph,
    memory_transition_allowed,
)
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope
from app.schemas.canon import Fact
from app.services.memory_lifecycle_service import MemoryLifecycleService
from app.storage.canon import CanonStorage
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.memory_pack import MemoryPackStorage


def test_invalid_legacy_memory_is_conservatively_normalized():
    missing = MemoryRecordV2.from_mapping({"slug": "missing", "confidence": "invalid"})
    invalid = MemoryRecordV2.from_mapping({"slug": "invalid", "status": "approved", "trust_label": "maybe"})

    assert missing.status == invalid.status == "needs_review"
    assert missing.confidence == 0.0
    assert missing.trust_label == invalid.trust_label == "unknown"
    assert "status:needs_review" in missing.recall_block_reasons(())


def test_model_confidence_alone_never_auto_activates(tmp_path: Path):
    store = CreativeMemoryStorage(str(tmp_path))
    plain = asyncio.run(
        store.write_candidate_memory(
            "p",
            "plain",
            "作者偏好短句",
            "模型推断",
            confidence=0.99,
            source="writer",
            source_type="internal",
        )
    )
    verified = asyncio.run(
        store.write_candidate_memory(
            "p",
            "verified",
            "作者偏好克制表达",
            "确定性低影响反馈",
            confidence=0.99,
            source="feedback_pipeline",
            source_type="internal",
            source_refs=["feedback:event-1"],
            deterministic_source=True,
            reversible=True,
            impact="low",
        )
    )

    assert asyncio.run(store.read_memory("p", plain))["status"] == "needs_review"
    verified_record = asyncio.run(store.read_memory("p", verified))
    assert verified_record["status"] == "active"
    assert verified_record["activation"] == "auto_active_verified"


def test_recall_is_pure_and_excludes_every_ineligible_class(tmp_path: Path):
    store = CreativeMemoryStorage(str(tmp_path))
    lifecycle = MemoryLifecycleService(store)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    asyncio.run(store.write_memory("p", "valid", "对白使用短句", "作者确认", source="author"))
    asyncio.run(store.write_memory("p", "expired", "对白使用旧句式", "旧偏好", expires_at=expired_at))
    asyncio.run(store.write_memory("p", "formal", "对白使用正式语气", "决定 A"))
    asyncio.run(store.write_memory("p", "casual", "对白使用口语", "决定 B"))
    asyncio.run(
        store.write_memory(
            "p",
            "untrusted",
            "对白使用外部网页风格",
            "不可信内容",
            source="https://example.invalid/wiki",
            source_type="external",
            trust_label="untrusted",
            status="active",
            enforce_trust_policy=False,
            _lifecycle_override=True,
        )
    )
    asyncio.run(
        store.write_memory(
            "p",
            "provenance-free",
            "对白使用无来源规则",
            "缺少来源",
            source="legacy",
            trust_label="trusted",
            status="active",
            enforce_trust_policy=False,
            _lifecycle_override=True,
        )
    )
    assert asyncio.run(lifecycle.set_conflicts("p", "formal", ["casual"]))
    asyncio.run(store.write_candidate_memory("p", "review", "对白可能华丽", "模型推断", confidence=0.99))
    asyncio.run(store.write_candidate_memory("p", "reject", "对白使用错误偏好", "模型推断"))
    assert asyncio.run(lifecycle.reject("p", "reject"))
    asyncio.run(store.write_memory("p", "old", "对白使用冗长表达", "旧决定"))
    asyncio.run(lifecycle.supersede("p", "old", "new", description="对白使用凝练表达", body="新决定"))

    expired_path = store._memory_path("p", "expired")
    before = expired_path.read_bytes()
    hits = asyncio.run(store.recall("p", "对白使用", top_k=20))
    after = expired_path.read_bytes()

    assert before == after
    assert asyncio.run(store.read_memory("p", "expired"))["status"] == "active"
    assert {item["slug"] for item in hits} == {"valid", "new"}


def test_conflict_and_supersession_links_are_integrity_checked(tmp_path: Path):
    store = CreativeMemoryStorage(str(tmp_path))
    lifecycle = MemoryLifecycleService(store)
    asyncio.run(store.write_memory("p", "a", "A", "A"))
    asyncio.run(store.write_memory("p", "b", "B", "B"))

    assert not asyncio.run(lifecycle.set_conflicts("p", "a", ["missing"]))
    assert not asyncio.run(lifecycle.set_conflicts("p", "a", ["a"]))
    assert asyncio.run(lifecycle.set_conflicts("p", "a", ["b"]))
    assert asyncio.run(store.read_memory("p", "a"))["conflicts_with"] == ["b"]
    assert asyncio.run(store.read_memory("p", "b"))["conflicts_with"] == ["a"]

    with pytest.raises(ValueError, match="memory_lifecycle_service_required"):
        asyncio.run(store.write_memory("p", "a", "A", "A", supersedes=["b"]))
    cyclic = build_memory_graph(
        [
            {"id": "a", "status": "active", "supersedes": ["b"]},
            {"id": "b", "status": "active", "supersedes": ["a"]},
        ]
    )
    assert cyclic.invalid_ids == {"a", "b"}
    assert any(item["reason"] == "cycle" for item in cyclic.errors)
    graph = build_memory_graph(asyncio.run(store.list_headers("p", statuses=["*"])))
    assert graph.errors == []


def test_memory_transition_state_machine_has_no_terminal_escape():
    for current in MEMORY_STATUSES_V2:
        for target in MEMORY_STATUSES_V2:
            expected = current == target or target in MEMORY_TRANSITIONS[current]
            assert memory_transition_allowed(current, target) is expected
    for terminal in ("superseded", "expired", "rejected"):
        assert MEMORY_TRANSITIONS[terminal] == set()
        assert not memory_transition_allowed(terminal, "active")


def test_ttl_migration_and_index_rebuild_are_idempotent(tmp_path: Path):
    store = CreativeMemoryStorage(str(tmp_path))
    lifecycle = MemoryLifecycleService(store)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    asyncio.run(store.write_memory("p", "expired", "过期偏好", "旧内容", expires_at=expired_at))
    assert asyncio.run(lifecycle.expire_due("p")) == ["expired"]
    assert asyncio.run(lifecycle.expire_due("p")) == []

    legacy_path = store._memory_path("p", "legacy")
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("legacy body without frontmatter", encoding="utf-8")
    assert asyncio.run(lifecycle.migrate_legacy("p")) == 1
    assert asyncio.run(lifecycle.migrate_legacy("p")) == 0
    legacy = asyncio.run(store.read_memory("p", "legacy"))
    assert legacy["status"] == "needs_review"
    assert legacy["schema_version"] == 2

    asyncio.run(store.write_memory("p", "active", "有效偏好", "作者确认"))
    expected = asyncio.run(store.read_index("p"))
    store._index_path("p").unlink()
    rebuilt = asyncio.run(store.read_index("p"))
    assert rebuilt == expected
    assert asyncio.run(store.read_index("p")) == expected


def test_canon_legacy_and_model_declared_status_are_conservative(tmp_path: Path):
    store = CanonStorage(str(tmp_path))
    path = store.get_project_path("p") / "canon" / "facts.jsonl"
    asyncio.run(
        store.write_jsonl(
            path,
            [
                {"id": "legacy", "statement": "旧数据", "source": "C0", "introduced_in": "C0"},
                {
                    "id": "invalid",
                    "statement": "非法状态",
                    "source": "V1C001",
                    "introduced_in": "V1C001",
                    "status": "approved",
                },
            ],
        )
    )
    asyncio.run(
        store.add_fact(
            "p",
            Fact(
                id="model",
                statement="模型高置信事实",
                source="V1C001",
                introduced_in="V1C001",
                status="confirmed",
                confidence=0.99,
                confidence_method="model_declared",
            ),
        )
    )
    asyncio.run(
        store.add_fact(
            "p",
            Fact(
                id="author",
                statement="作者确认事实",
                source="V1C001",
                introduced_in="V1C001",
                status="confirmed",
                confirmed_by="author",
            ),
        )
    )

    raw = {item["id"]: item for item in asyncio.run(store.get_all_facts_raw("p"))}
    assert raw["legacy"]["status"] == raw["invalid"]["status"] == raw["model"]["status"] == "needs_review"
    assert [fact.id for fact in asyncio.run(store.get_eligible_facts("p"))] == ["author"]
    assert asyncio.run(store.normalize_fact_records("p")) >= 2
    assert asyncio.run(store.normalize_fact_records("p")) == 0


def test_explicit_review_can_promote_external_canon_and_memory(tmp_path: Path):
    canon = CanonStorage(str(tmp_path))
    memory = CreativeMemoryStorage(str(tmp_path))
    asyncio.run(
        canon.add_fact(
            "p",
            Fact(
                id="external",
                statement="外部资料事实",
                source="https://example.invalid/wiki",
                introduced_in="V1C001",
                status="confirmed",
                source_type="external",
            ),
        )
    )
    assert asyncio.run(canon.get_fact("p", "external")).status == "needs_review"
    assert asyncio.run(canon.confirm_facts("p", ["external"])) == 1
    assert [fact.id for fact in asyncio.run(canon.get_eligible_facts("p"))] == ["external"]

    slug = asyncio.run(
        memory.write_candidate_memory(
            "p",
            "external-memory",
            "外部资料偏好",
            "待审核",
            source="https://example.invalid/wiki",
            source_type="external",
        )
    )
    assert asyncio.run(memory.confirm_memory("p", slug))
    promoted = asyncio.run(memory.read_memory("p", slug))
    assert promoted["status"] == "active"
    assert promoted["trust_label"] == "trusted"


def test_memory_pack_detects_source_and_context_epoch_staleness(tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    source = store.get_project_path("p") / "cards" / "hero.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("name: Hero\n", encoding="utf-8")
    scope = new_turn_scope(project_id="p", chapter_id="V1C001", context_epoch=3)
    with bind_turn_scope(scope):
        asyncio.run(store.write_pack("p", "V1C001", {"payload": {"facts": ["x"]}}))
        fresh = asyncio.run(store.read_pack("p", "V1C001"))
    assert fresh and fresh["stale"] is False
    assert fresh["source_binding"]["context_epoch"] == 3

    source.write_text("name: Changed Hero\n", encoding="utf-8")
    changed = asyncio.run(store.read_pack("p", "V1C001"))
    assert changed and changed["stale"] is True
    assert "source_fingerprint_changed" in changed["stale_reasons"]

    scope3 = new_turn_scope(project_id="p", chapter_id="V1C001", context_epoch=3)
    with bind_turn_scope(scope3):
        asyncio.run(store.write_pack("p", "V1C001", {"payload": {"facts": ["y"]}}))
    scope4 = new_turn_scope(project_id="p", chapter_id="V1C001", context_epoch=4)
    with bind_turn_scope(scope4):
        epoch_stale = asyncio.run(store.read_pack("p", "V1C001"))
    assert epoch_stale and "context_epoch_changed" in epoch_stale["stale_reasons"]


def test_legacy_memory_pack_without_binding_is_stale(tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    path = store.get_pack_path("p", "V1C001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"payload":{"legacy":true}}', encoding="utf-8")

    pack = asyncio.run(store.read_pack("p", "V1C001"))
    assert pack and pack["stale"] is True
    assert pack["stale_reasons"] == ["source_binding_missing"]


def test_memory_pack_detects_added_and_deleted_files_without_path_leak(tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    source_root = store.get_project_path("p") / "cards"
    source_root.mkdir(parents=True, exist_ok=True)
    original = source_root / "private-character-name.yaml"
    original.write_text("name: one\n", encoding="utf-8")
    asyncio.run(store.write_pack("p", "V1C001", {"payload": {}}))

    added = source_root / "private-added-name.yaml"
    added.write_text("name: two\n", encoding="utf-8")
    added_pack = asyncio.run(store.read_pack("p", "V1C001"))
    assert added_pack and added_pack["source_verification"]["change_counts"]["added"] == 1
    assert "private-added-name" not in str(added_pack["source_verification"])

    asyncio.run(store.write_pack("p", "V1C001", {"payload": {}}))
    original.unlink()
    deleted_pack = asyncio.run(store.read_pack("p", "V1C001"))
    assert deleted_pack and deleted_pack["source_verification"]["change_counts"]["missing"] == 1
    assert "private-character-name" not in str(deleted_pack["source_verification"])


def test_memory_pack_scan_is_offloaded_from_event_loop(monkeypatch, tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    source = store.get_project_path("p") / "cards" / "hero.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("name: Hero\n", encoding="utf-8")
    original = store._capture_source_revisions
    events = []

    def slow_capture(project_id, chapter):
        time.sleep(0.05)
        result = original(project_id, chapter)
        events.append("scan")
        return result

    monkeypatch.setattr(store, "_capture_source_revisions", slow_capture)

    async def scenario():
        scan = asyncio.create_task(store.profile_source_scan("p"))
        await asyncio.sleep(0.01)
        events.append("tick")
        diagnostics = await scan
        return diagnostics

    diagnostics = asyncio.run(scenario())
    assert events == ["tick", "scan"]
    assert diagnostics["files_scanned"] == 1
    assert diagnostics["bytes_hashed"] > 0


def test_memory_pack_hybrid_index_hashes_only_changed_files(tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    root = store.get_project_path("p") / "drafts"
    root.mkdir(parents=True, exist_ok=True)
    first = root / "first.md"
    second = root / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    cold = asyncio.run(store.profile_source_scan("p"))
    warm = asyncio.run(store.profile_source_scan("p"))
    first.write_text("first changed", encoding="utf-8")
    changed = asyncio.run(store.profile_source_scan("p"))

    assert cold["fallback_reason"] == "index_missing"
    assert cold["bytes_hashed"] == len("first") + len("second")
    assert warm["fallback_reason"] == ""
    assert warm["bytes_hashed"] == 0
    assert changed["bytes_hashed"] == len("first changed")


def test_memory_pack_touch_without_content_change_does_not_mark_pack_stale(tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    source = store.get_project_path("p") / "cards" / "hero.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("name: Hero\n", encoding="utf-8")
    asyncio.run(store.write_pack("p", "V1C001", {"payload": {}}))

    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    pack = asyncio.run(store.read_pack("p", "V1C001"))

    assert pack and pack["stale"] is False
    assert pack["source_verification"]["scan_diagnostics"]["bytes_hashed"] == source.stat().st_size


@pytest.mark.parametrize(
    ("index_payload", "expected_reason"),
    [
        ("not-json", "index_unreadable"),
        ('{"schema_version":999,"files":{}}', "index_schema_mismatch"),
        ('{"schema_version":1,"files":[]}', "index_invalid"),
    ],
)
def test_memory_pack_invalid_index_falls_back_to_full_hash(index_payload, expected_reason, tmp_path: Path):
    store = MemoryPackStorage(str(tmp_path))
    source = store.get_project_path("p") / "cards" / "hero.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("name: Hero\n", encoding="utf-8")
    asyncio.run(store.profile_source_scan("p"))
    index = store.get_project_path("p") / "memory_packs" / "source_revision_index.json"
    index.write_text(index_payload, encoding="utf-8")

    recovered = asyncio.run(store.profile_source_scan("p"))
    assert recovered["fallback_reason"] == expected_reason
    assert recovered["bytes_hashed"] == source.stat().st_size
