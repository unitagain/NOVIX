# -*- coding: utf-8 -*-
"""P6 writing quality judge.

该轨道必须调用真实 LLM gateway；默认测试只验证 schema 和无配置降级。
不要用本地 fake judge 证明写作质量。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.llm_gateway import get_gateway
from app.services.llm_config_service import llm_config_service
from app.error_contract import benchmark_failure
from app.utils.llm_output import parse_json_payload


RUBRIC_FIELDS = (
    "context_utilization",
    "factual_consistency",
    "timeline_consistency",
    "character_consistency",
    "foreshadowing_integrity",
    "style_consistency",
    "readability",
)

PAIRWISE_JUDGE_PROMPT_VERSION = "context-quality-v3"


def judge_extra_body(profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Disable optional model reasoning so judge output remains bounded JSON."""

    if not profile_id:
        return None
    profile = llm_config_service.get_profile_by_id(str(profile_id)) or {}
    model = str(profile.get("model") or "").lower()
    if model.startswith("qwen"):
        return {"enable_thinking": False}
    if model.startswith("glm"):
        return {"thinking": {"type": "disabled"}}
    return None

SCORE_ANCHORS = {
    "5": "可直接进入人工复核的强样本；事实、时间线、人设、风格均稳定，没有明显结构或格式问题。",
    "4": "整体可用；只有轻微瑕疵，不影响读者理解和后端策略判断。",
    "3": "勉强可用；存在可见问题，但仍能看出上下文被利用，且没有严重连续性破坏。",
    "2": "质量偏低；上下文利用弱、泛化套话明显，或出现轻中度事实/人设/时间线漂移。",
    "1": "基本不可用；明显跑偏、过短、空泛、结构残缺，或大量忽略给定上下文。",
    "0": "完全失败；非正文、JSON/元叙述外壳、截断、拒答、乱码，或与任务/上下文几乎无关。",
}


def build_rubric_judge_messages(case: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build evidence-bound rubric judge messages."""

    system = (
        "你是长篇中文小说后端 benchmark 的严谨校准评审员。只基于用户提供的 canon、前文摘要、"
        "scene brief、候选正文和参考字段评分；不得引入外部知识，不得按已知名著情节补全。"
        "评分目标始终是 candidate_text/chapter_text，不是 reference_excerpt。输出严格 JSON。"
    )
    candidate_text = str(case.get("candidate_text") or case.get("chapter_text") or "")
    user = {
        "task_type": str(case.get("task_type") or "rubric_prose_quality"),
        "writer_variant": str(case.get("writer_variant") or ""),
        "generation_quality": str(case.get("generation_quality") or ""),
        "review_target": "candidate_text",
        "evaluation_goal": (
            "判断后端提供的上下文信号是否让候选正文更符合项目事实、人物状态、时间线和风格；"
            "不是评选文笔最顺的泛化续写。"
        ),
        "canon_summary": str(case.get("canon_summary") or ""),
        "prior_summary": str(case.get("prior_summary") or ""),
        "resident_context": str(case.get("resident_context") or ""),
        "scene_brief": str(case.get("scene_brief") or ""),
        "reference_excerpt": str(case.get("reference_excerpt") or ""),
        "chapter_opening": str(case.get("chapter_opening") or ""),
        "candidate_text": candidate_text,
        "chapter_text": candidate_text,
        "score_anchors": SCORE_ANCHORS,
        "rubric": {
            "context_utilization": "0-5 分；候选是否具体利用了 canon、前文摘要和 scene brief 中的可观察信息。",
            "factual_consistency": "0-5 分；是否遵循 canon、scene brief 和前文明确事实。",
            "timeline_consistency": "0-5 分；是否保持事件顺序、已发生/未发生状态、场景时点一致。",
            "character_consistency": "0-5 分；人物称谓、关系、动机、状态是否与输入一致。",
            "foreshadowing_integrity": "0-5 分；是否保留线索/伏笔连续性，没有无依据改写关键线索。",
            "style_consistency": "0-5 分；叙述语气、节奏、视角是否贴近给定前文，不机械复述任务。",
            "readability": "0-5 分；是否是完整可读的小说正文，非 JSON 外壳、非说明文字、非截断输出。",
        },
        "penalty_rules": [
            "出现 JSON 外壳、字段名、代码块、任务说明或自我评价混入正文时，readability 最高 1，overall_score 最高 2。",
            "候选明显过短、截断或只有场景摘要时，overall_score 最高 2。",
            "候选大量引入输入没有支持的重大反转、人物关系或结局时，overall_score 最高 2。",
            "候选没有具体利用 canon_summary、prior_summary 或 scene_brief 中的任何可观察信息时，context_utilization 最高 1，overall_score 最高 3。",
            "候选只写通用悬疑/武侠/言情氛围，没有可核对的人物状态、地点、道具或事件承接时，overall_score 最高 2。",
            "低上下文候选可以因上下文不足而自洽，但不能凭空奖励；只按可观察文本质量评分。",
            "reference_excerpt 只用于理解期望语境，不得因候选接近原文或名著记忆而自动给高分。",
        ],
        "required_json_schema": {
            "scores": {field: "number" for field in RUBRIC_FIELDS},
            "violations": [
                {
                    "type": "fact|timeline|character|foreshadowing|style",
                    "severity": "low|medium|high",
                    "evidence": "引用输入中的可观察证据",
                    "explanation": "为什么构成问题",
                }
            ],
            "overall_score": "number 0-5",
            "summary": "简短中文结论",
            "calibration_notes": ["列出影响分数的关键可观察证据"],
        },
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def build_pairwise_judge_messages(case: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build blind pairwise judge messages for strategy A/B comparison."""

    system = (
        "你是长篇中文小说后端 benchmark 的盲评员。只基于输入证据比较候选 A/B 的最终续写质量。"
        "上下文利用只有在提高事实、时间线、人物、风格或可读性时才构成优势。不得根据候选顺序偏置，"
        "不得引入外部知识或已知名著情节。输出严格 JSON。"
    )
    user = {
        "prompt_version": PAIRWISE_JUDGE_PROMPT_VERSION,
        "evaluation_goal": (
            "比较哪个候选在给定项目上下文下形成了更可靠、自然、可继续使用的小说正文；"
            "上下文工程是手段，最终候选质量才是判定目标。"
        ),
        "anti_position_bias": (
            "A/B 只是临时标签，候选顺序没有任何质量含义。必须基于可观察上下文承接选择；"
            "若两个候选各有明显问题或只因先读到 A 而倾向 A，应选择 tie。"
        ),
        "canon_summary": str(case.get("canon_summary") or ""),
        "prior_summary": str(case.get("prior_summary") or ""),
        "resident_context": str(case.get("resident_context") or ""),
        "scene_brief": str(case.get("scene_brief") or ""),
        "reference_excerpt": str(case.get("reference_excerpt") or ""),
        "candidate_a": str(case.get("candidate_a") or ""),
        "candidate_b": str(case.get("candidate_b") or ""),
        "comparison_rules": [
            "优先选择更具体承接 canon、前文摘要、人物状态、地点、道具和事件顺序的候选。",
            "canon_summary 是候选证据而不是必须复述的清单；机械提及或堆砌 canon 不得获得额外优势。",
            "只有当 canon 信息适用于当前 scene 且被自然整合时才奖励上下文利用。",
            "resident_context 是当前 scene 之前的近邻正文，用于消解代词、人物在场和当前状态，不能当作新事件复演。",
            "若 canon_summary 与 resident_context/prior_summary/scene brief 的当前时点冲突，以更局部的证据为准。",
            "事实承接、时间线、人设、风格和可读性共同决定胜负，不能只凭命中更多上下文词语获胜。",
            "候选若出现 JSON 外壳、任务说明、截断或非正文，应显著扣分。",
            "只写通用氛围、没有可核对上下文承接的候选，不应因文笔流畅胜出。",
            "reference_excerpt 只作语境参考，不要求候选复写原文。",
            "如果胜负理由不能绑定到 canon_summary、prior_summary 或 scene_brief 的具体信息，选择 tie。",
            "不要默认选择 A；候选 B 若更具体承接上下文，应明确选择 B。",
            "理由必须简短，只列最多 3 条关键差异。",
            "不要长段引用候选文本；如需引用，每条证据不超过 20 个中文字符。",
        ],
        "required_json_schema": {
            "winner": "A|B|tie",
            "confidence": "number 0-1",
            "reasons": ["最多 3 条短理由"],
            "violations_a": ["最多 2 条候选 A 的关键问题"],
            "violations_b": ["最多 2 条候选 B 的关键问题"],
        },
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def parse_rubric_judge_response(raw: str) -> Dict[str, Any]:
    """Parse and normalize a rubric judge JSON response."""

    data, err = parse_json_payload(raw, expected_type=dict)
    if err or not isinstance(data, dict):
        return {"success": False, "error": err or "judge response is not an object", "raw": raw}

    raw_scores = data.get("scores") or {}
    if not isinstance(raw_scores, dict):
        raw_scores = {}
    scores: Dict[str, float] = {}
    for field in RUBRIC_FIELDS:
        try:
            value = float(raw_scores.get(field, 0))
        except (TypeError, ValueError):
            value = 0.0
        scores[field] = max(0.0, min(5.0, value))

    try:
        overall = float(data.get("overall_score", sum(scores.values()) / len(scores)))
    except (TypeError, ValueError):
        overall = sum(scores.values()) / len(scores)

    violations = data.get("violations") or []
    if not isinstance(violations, list):
        violations = []
    return {
        "success": True,
        "scores": scores,
        "overall_score": max(0.0, min(5.0, overall)),
        "violations": [item for item in violations if isinstance(item, dict)],
        "summary": str(data.get("summary") or ""),
    }


async def run_writing_judge_eval(
    case: Dict[str, Any],
    *,
    provider: Optional[str] = None,
    agent_name: str = "editor",
    require_available: bool = False,
) -> Dict[str, Any]:
    """Run a real LLM rubric judge through the configured gateway."""

    gateway = get_gateway()
    try:
        profile_id = provider or gateway.get_provider_for_agent(agent_name)
        response = await gateway.chat(
            build_rubric_judge_messages(case),
            provider=profile_id,
            temperature=0.0,
            max_tokens=1200,
            response_format={"type": "json_object"},
            extra_body=judge_extra_body(profile_id),
        )
    except Exception as exc:
        if require_available:
            raise
        return {"available": False, **benchmark_failure(exc)}

    parsed = parse_rubric_judge_response(str(response.get("content") or ""))
    return {
        "available": True,
        "success": bool(parsed.get("success")),
        "judge": parsed,
        "usage": response.get("usage") or {},
        "model": response.get("model"),
        "provider": response.get("provider"),
        "elapsed_time": response.get("elapsed_time"),
    }


async def run_pairwise_judge_eval(
    case: Dict[str, Any],
    *,
    provider: Optional[str] = None,
    agent_name: str = "editor",
    require_available: bool = False,
) -> Dict[str, Any]:
    """Run a real LLM pairwise judge through the configured gateway."""

    gateway = get_gateway()
    try:
        profile_id = provider or gateway.get_provider_for_agent(agent_name)
        response = await gateway.chat(
            build_pairwise_judge_messages(case),
            provider=profile_id,
            temperature=0.0,
            max_tokens=2200,
            response_format={"type": "json_object"},
            extra_body=judge_extra_body(profile_id),
        )
    except Exception as exc:
        if require_available:
            raise
        return {"available": False, **benchmark_failure(exc)}

    data, err = parse_json_payload(str(response.get("content") or ""), expected_type=dict)
    finish_reason = str(response.get("finish_reason") or "")
    reason = "judge_output_truncated" if finish_reason == "length" else err
    return {
        "available": True,
        "success": not reason and isinstance(data, dict),
        "judge": data if isinstance(data, dict) else {},
        "prompt_version": PAIRWISE_JUDGE_PROMPT_VERSION,
        "error": reason,
        "finish_reason": finish_reason,
        "usage": response.get("usage") or {},
        "model": response.get("model"),
        "provider": response.get("provider"),
        "elapsed_time": response.get("elapsed_time"),
    }
