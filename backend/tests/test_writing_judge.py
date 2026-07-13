# -*- coding: utf-8 -*-
"""P6 writing judge framework tests.

默认不调用外部 LLM；真实 API 路径由 run_writing_judge_eval 在配置存在时执行。
"""

import asyncio

from app.eval.writing_judge import (
    POINTWISE_PAIR_JUDGE_PROMPT_VERSION,
    build_pairwise_judge_messages,
    build_pointwise_candidate_judge_messages,
    build_rubric_judge_messages,
    judge_extra_body,
    parse_rubric_judge_response,
    run_pointwise_pair_judge_eval,
    run_writing_judge_eval,
)


def _case():
    return {
        "canon_summary": "玉佩是主角母亲遗物。李四惧怕火。",
        "prior_summary": "上一章主角刚进入迷雾森林。",
        "resident_context": "进入森林前，李四仍在队伍中并携带火把。",
        "scene_brief": "主角在章首发现玉佩发热，但仍不知道原因。",
        "chapter_opening": "玉佩在掌心发热，他停在雾边。",
        "chapter_text": "玉佩在掌心发热，他停在雾边。李四看见火光后后退半步。",
        "candidate_a": "玉佩在掌心发热，他停在雾边。",
        "candidate_b": "主角早已知道玉佩来自敌人。",
    }


def test_rubric_judge_prompt_is_evidence_bound_json():
    messages = build_rubric_judge_messages(_case())
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "只基于" in messages[0]["content"]
    assert "评分目标始终是 candidate_text/chapter_text" in messages[0]["content"]
    assert "required_json_schema" in messages[1]["content"]
    assert "context_utilization" in messages[1]["content"]
    assert "factual_consistency" in messages[1]["content"]
    assert "score_anchors" in messages[1]["content"]
    assert "penalty_rules" in messages[1]["content"]
    assert "readability" in messages[1]["content"]


def test_pairwise_judge_prompt_is_blind():
    messages = build_pairwise_judge_messages(_case())
    assert "盲评" in messages[0]["content"]
    assert "candidate_a" in messages[1]["content"]
    assert "candidate_b" in messages[1]["content"]
    assert "anti_position_bias" in messages[1]["content"]
    assert "context-quality-v3" in messages[1]["content"]
    assert "resident_context" in messages[1]["content"]
    assert "不是必须复述的清单" in messages[1]["content"]
    assert "最终候选质量" in messages[1]["content"]
    assert "不要默认选择 A" in messages[1]["content"]


def test_parse_rubric_judge_response_normalizes_scores():
    raw = """
    {
      "scores": {
        "factual_consistency": 6,
        "timeline_consistency": 4,
        "character_consistency": 3,
        "foreshadowing_integrity": 2,
        "style_consistency": -1
      },
      "violations": [{"type": "fact", "severity": "medium", "evidence": "x", "explanation": "y"}],
      "overall_score": 4.5,
      "summary": "基本可用"
    }
    """
    parsed = parse_rubric_judge_response(raw)
    assert parsed["success"] is True
    assert parsed["scores"]["context_utilization"] == 0.0
    assert parsed["scores"]["factual_consistency"] == 5.0
    assert parsed["scores"]["style_consistency"] == 0.0
    assert parsed["scores"]["readability"] == 0.0
    assert parsed["overall_score"] == 4.5
    assert len(parsed["violations"]) == 1


def test_pointwise_candidate_prompt_has_compact_scores_only_schema():
    messages = build_pointwise_candidate_judge_messages(_case())
    assert POINTWISE_PAIR_JUDGE_PROMPT_VERSION in messages[1]["content"]
    assert "required_json_schema" in messages[1]["content"]
    assert "violations" not in messages[1]["content"]
    assert "calibration_notes" not in messages[1]["content"]


def test_writing_judge_without_config_reports_unavailable():
    result = asyncio.run(run_writing_judge_eval(_case(), provider="missing-profile-for-test"))
    assert result["available"] is False
    assert result["success"] is False
    assert result["reason"]


def test_judge_extra_body_disables_optional_reasoning(monkeypatch):
    profiles = {
        "qwen": {"model": "qwen3.5-397b-a17b"},
        "glm": {"model": "glm-5.1"},
        "plain": {"model": "deepseek-v4-flash"},
    }
    monkeypatch.setattr(
        "app.eval.writing_judge.llm_config_service.get_profile_by_id",
        lambda profile_id: profiles.get(profile_id),
    )
    assert judge_extra_body("qwen") == {"enable_thinking": False}
    assert judge_extra_body("glm") == {"thinking": {"type": "disabled"}}
    assert judge_extra_body("plain") is None


def test_pointwise_pair_judge_derives_order_invariant_winner(monkeypatch):
    async def score(case, **_kwargs):
        strong = case["candidate_text"] == _case()["candidate_a"]
        value = 4.5 if strong else 2.0
        return {
            "available": True,
            "success": True,
            "judge": {"scores": {field: value for field in (
                "context_utilization",
                "factual_consistency",
                "timeline_consistency",
                "character_consistency",
                "foreshadowing_integrity",
                "style_consistency",
                "readability",
            )}},
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "provider": "judge-provider",
            "model": "judge-model",
        }

    monkeypatch.setattr("app.eval.writing_judge.run_pointwise_candidate_judge_eval", score)

    result = asyncio.run(run_pointwise_pair_judge_eval(_case(), provider="judge"))

    assert result["success"] is True
    assert result["order_invariant"] is True
    assert result["judge"]["winner"] == "A"
    assert result["judge"]["score_a"] > result["judge"]["score_b"]
    assert result["prompt_version"] == POINTWISE_PAIR_JUDGE_PROMPT_VERSION
    assert len(result["usage_rows"]) == 2
