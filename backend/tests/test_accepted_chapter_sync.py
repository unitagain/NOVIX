import asyncio

from app.orchestrator.orchestrator import Orchestrator
from app.schemas.canon import Fact


def _turn_effect(
    *,
    change_type="chapter_write",
    fact_operation="replace_chapter",
    chapter_summary="两人在雨夜相遇。",
    fact_candidates=None,
):
    return {
        "change_type": change_type,
        "fact_operation": fact_operation,
        "chapter_summary": chapter_summary,
        "fact_candidates": list(fact_candidates or []),
        "message": "已完成。",
    }


def _fact(fact_id, statement, chapter, **overrides):
    data = {
        "id": fact_id,
        "statement": statement,
        "source": chapter,
        "introduced_in": chapter,
        "status": "needs_review",
        "confidence_method": "model_declared",
    }
    data.update(overrides)
    return Fact(**data)


def test_apply_turn_effect_prose_edit_does_not_call_archivist_or_write_canon(tmp_path):
    orchestrator = Orchestrator(str(tmp_path))
    chapter = "V1C001"
    asyncio.run(orchestrator.draft_storage.save_current_draft("p", chapter, "雨夜相遇。"))

    result = asyncio.run(
        orchestrator.apply_turn_effect(
            "p",
            chapter,
            _turn_effect(
                change_type="prose_edit",
                fact_operation="replace_chapter",
                chapter_summary="不应保存的摘要",
                fact_candidates=[
                    {"statement": "不应保存", "evidence": "雨夜相遇。", "category": "事件"}
                ],
            ),
        )
    )

    assert result["success"] is True
    assert result["applied"] is False
    assert result["fact_operation"] == "none"
    assert asyncio.run(orchestrator.draft_storage.get_chapter_summary("p", chapter)) is None
    assert asyncio.run(orchestrator.canon_storage.get_all_facts_raw("p")) == []


def test_apply_turn_effect_replaces_only_unconfirmed_generated_facts(tmp_path):
    orchestrator = Orchestrator(str(tmp_path))
    chapter = "V1C001"
    content = "林舟与沈月在雨夜相遇。沈月把铜钥匙交给林舟。"
    asyncio.run(orchestrator.draft_storage.save_current_draft("p", chapter, content))
    asyncio.run(
        orchestrator.canon_storage.add_fact(
            "p",
            _fact(
                "M0001",
                "作者手工确认的事实",
                chapter,
                status="confirmed",
                confirmed_by="user",
                confidence_method="declared",
            ),
        )
    )
    asyncio.run(orchestrator.canon_storage.add_fact("p", _fact("F0001", "旧的自动事实", chapter)))

    result = asyncio.run(
        orchestrator.apply_turn_effect(
            "p",
            chapter,
            _turn_effect(
                fact_candidates=[
                    {
                        "statement": "林舟与沈月在雨夜相遇",
                        "evidence": "林舟与沈月在雨夜相遇。",
                        "category": "事件",
                    },
                    {
                        "statement": "这条事实来自未采纳正文",
                        "evidence": "沈月把银钥匙交给林舟。",
                        "category": "物品",
                    },
                ]
            ),
        )
    )

    assert result["success"] is True
    assert result["stats"]["facts_saved"] == 1
    assert result["stats"]["facts_rejected_evidence"] == 1
    facts = asyncio.run(orchestrator.canon_storage.get_all_facts_raw("p"))
    statements = {item["statement"] for item in facts}
    assert statements == {"作者手工确认的事实", "林舟与沈月在雨夜相遇"}
    generated = next(item for item in facts if item["statement"] == "林舟与沈月在雨夜相遇")
    assert generated["status"] == "needs_review"
    assert generated["confidence_method"] == "model_declared"
    assert generated["evidence_refs"][0].startswith(f"chapter:{chapter}#sha256:")
    summary = asyncio.run(orchestrator.draft_storage.get_chapter_summary("p", chapter))
    assert summary.brief_summary == "两人在雨夜相遇。"
    assert summary.new_facts == ["林舟与沈月在雨夜相遇"]


def test_apply_turn_effect_merge_deduplicates_without_removing_existing_facts(tmp_path):
    orchestrator = Orchestrator(str(tmp_path))
    chapter = "V1C001"
    content = "沈月把铜钥匙交给林舟。林舟随后离开旧宅。"
    asyncio.run(orchestrator.draft_storage.save_current_draft("p", chapter, content))
    asyncio.run(orchestrator.canon_storage.add_fact("p", _fact("F0001", "沈月把铜钥匙交给林舟", chapter)))

    result = asyncio.run(
        orchestrator.apply_turn_effect(
            "p",
            chapter,
            _turn_effect(
                change_type="plot_edit",
                fact_operation="merge",
                chapter_summary="沈月交出钥匙，林舟离开旧宅。",
                fact_candidates=[
                    {
                        "statement": "沈月把铜钥匙交给林舟",
                        "evidence": "沈月把铜钥匙交给林舟。",
                        "category": "物品",
                    },
                    {
                        "statement": "林舟离开旧宅",
                        "evidence": "林舟随后离开旧宅。",
                        "category": "事件",
                    },
                ],
            ),
        )
    )

    assert result["success"] is True
    assert result["stats"]["facts_saved"] == 1
    assert result["stats"]["facts_deduplicated"] == 1
    facts = asyncio.run(orchestrator.canon_storage.get_all_facts_raw("p"))
    assert {item["statement"] for item in facts} == {"沈月把铜钥匙交给林舟", "林舟离开旧宅"}


def test_apply_turn_effect_requires_saved_final_draft(tmp_path):
    orchestrator = Orchestrator(str(tmp_path))

    result = asyncio.run(orchestrator.apply_turn_effect("p", "V1C001", _turn_effect()))

    assert result == {"success": False, "reason": "draft_missing"}
