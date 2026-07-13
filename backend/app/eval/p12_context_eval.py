"""P12 memory/compact context variants and output-level evidence analysis."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from app.context_engine.memory_record import MemoryRecordV2, parse_string_list
from app.error_contract import safe_error_code
from app.eval.longform_statistics import cluster_bootstrap_mean_ci
from app.eval.writing_judge import POINTWISE_PAIR_JUDGE_PROMPT_VERSION, run_pointwise_pair_judge_eval
from app.utils.llm_output import parse_json_payload


@dataclass(frozen=True)
class P12ContextVariant:
    name: str
    memory_enabled: bool = True
    conflict_aware: bool = True
    compact_enabled: bool = False
    recovery_refs_enabled: bool = False


P12_CONTEXT_VARIANTS = {
    "memory_off": P12ContextVariant("memory_off", memory_enabled=False),
    "memory_on": P12ContextVariant("memory_on"),
    "conflict_aware_off": P12ContextVariant("conflict_aware_off", conflict_aware=False),
    "conflict_aware_on": P12ContextVariant("conflict_aware_on"),
    "fresh_history": P12ContextVariant("fresh_history"),
    "compact_history": P12ContextVariant("compact_history", compact_enabled=True),
    "recovery_refs_off": P12ContextVariant("recovery_refs_off", compact_enabled=True),
    "recovery_refs_on": P12ContextVariant(
        "recovery_refs_on", compact_enabled=True, recovery_refs_enabled=True
    ),
}

P12_CONTEXT_COMPARISONS = (
    ("memory_off", "memory_on"),
    ("conflict_aware_off", "conflict_aware_on"),
    ("fresh_history", "compact_history"),
    ("recovery_refs_off", "recovery_refs_on"),
)


def context_variant_fingerprint(name: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        {"variant": name, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def p12_pair_fingerprint(row: Dict[str, Any]) -> str:
    def candidate_hash(role: str) -> str:
        explicit = str(row.get(f"candidate_{role}_sha256") or "")
        if explicit:
            return explicit
        return hashlib.sha256(str(row.get(f"candidate_{role}") or "").encode("utf-8")).hexdigest()

    payload = {
        "pair_id": row.get("pair_id"),
        "scene_id": row.get("scene_id"),
        "variant_a": row.get("variant_a"),
        "variant_b": row.get("variant_b"),
        "context_fingerprint_a": row.get("context_fingerprint_a"),
        "context_fingerprint_b": row.get("context_fingerprint_b"),
        "candidate_a_sha256": candidate_hash("a"),
        "candidate_b_sha256": candidate_hash("b"),
        "writer_provider": row.get("writer_provider"),
        "writer_model": row.get("writer_model"),
        "prompt_version": row.get("prompt_version"),
        "judge_prompt_version": row.get("judge_prompt_version"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assemble_p12_context(
    *,
    variant: str,
    base_context: Dict[str, Any],
    memories: Iterable[Dict[str, Any]] = (),
    compact_artifact: Dict[str, Any] | None = None,
    recovered_sources: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    spec = P12_CONTEXT_VARIANTS[variant]
    payload = dict(base_context)
    memory_rows = [dict(item) for item in memories]
    active_ids = {
        str(item.get("id") or item.get("slug") or "") for item in memory_rows if item.get("status") == "active"
    }
    conflict_participants = set()
    for item in memory_rows:
        if item.get("status") != "active":
            continue
        conflicts = set(parse_string_list(item.get("conflicts_with"))).intersection(active_ids)
        if conflicts:
            conflict_participants.add(str(item.get("id") or item.get("slug") or ""))
            conflict_participants.update(conflicts)
    selected = []
    excluded = []
    if spec.memory_enabled:
        for item in memory_rows:
            record = MemoryRecordV2.from_mapping(item)
            reasons = record.recall_block_reasons(())
            if spec.conflict_aware and record.id in conflict_participants:
                reasons.append("unresolved_conflict")
            if reasons:
                excluded.append({"id": record.id, "reasons": reasons})
            else:
                selected.append(record.to_dict())
    payload["creative_memory"] = selected
    payload["memory_excluded"] = excluded

    if spec.compact_enabled:
        artifact = dict(compact_artifact or {})
        payload["session_projection"] = {
            key: artifact.get(key)
            for key in ("id", "epoch", "decisions", "constraints", "entity_state", "open_loops", "recent_summary")
        }
        if spec.recovery_refs_enabled:
            payload["recovered_sources"] = [dict(item) for item in recovered_sources]
    payload["p12_variant"] = spec.name
    payload["p12_context_fingerprint"] = context_variant_fingerprint(spec.name, payload)
    return payload


def analyze_p12_pairwise(rows: List[Dict[str, Any]], *, bootstrap_samples: int = 10_000) -> Dict[str, Any]:
    current = [
        row
        for row in rows
        if not row.get("stale")
        and row.get("judge_prompt_version") == POINTWISE_PAIR_JUDGE_PROMPT_VERSION
        and str(row.get("pair_fingerprint") or "") == p12_pair_fingerprint(row)
    ]
    stale_rows = len(rows) - len(current)
    comparable = [
        row
        for row in current
        if row.get("position_consistent") is True and str(row.get("judge_winner") or "") in {"A", "B", "tie"}
    ]
    scored = []
    for row in comparable:
        winner = str(row.get("judge_winner"))
        scored.append({**row, "score_b": 1.0 if winner == "B" else 0.0 if winner == "A" else 0.5})
    ci = cluster_bootstrap_mean_ci(
        scored,
        seed_material="p12-context-ab",
        samples=bootstrap_samples,
    )
    scene_ids = {str(row.get("scene_id") or row.get("pair_id") or "") for row in scored}
    trials: Dict[str, int] = {}
    for row in scored:
        scene = str(row.get("scene_id") or row.get("pair_id") or "")
        trials[scene] = trials.get(scene, 0) + 1
    pollution = sum(int(row.get("memory_pollution_count") or 0) for row in current)
    contradictions = sum(int(row.get("severe_contradictions") or 0) for row in current)
    unrecoverable = sum(1 for row in current if row.get("recoverable") is False)
    comparable_rate = len(comparable) / len(current) if current else 0.0
    position_consistency = (
        sum(row.get("position_consistent") is True for row in current) / len(current) if current else 0.0
    )
    first_attempt_comparable = []
    first_attempt_consistent = []
    for row in current:
        attempts = list(row.get("attempts") or [])
        first = attempts[0] if attempts else {}
        consistent = bool(first.get("order_invariant")) if attempts else row.get("position_consistent") is True
        if consistent:
            first_attempt_consistent.append(row)
        winner = str(first.get("winner") or row.get("judge_winner") or "")
        if consistent and winner in {"A", "B", "tie"}:
            first_attempt_comparable.append(row)
    gate = bool(
        len(comparable) >= 100
        and len(scene_ids) >= 20
        and trials
        and min(trials.values()) >= 2
        and comparable_rate >= 0.90
        and pollution == 0
        and contradictions == 0
        and unrecoverable == 0
        and float(ci.get("lower") or 0.0) > 0.55
    )
    failures = p12_failure_rows(current)
    return {
        "pairs": len(current),
        "stale_pairs": stale_rows,
        "comparable_pairs": len(comparable),
        "comparable_rate": comparable_rate,
        "position_consistency": position_consistency,
        "first_attempt_comparable_rate": len(first_attempt_comparable) / len(current) if current else 0.0,
        "first_attempt_position_consistency": len(first_attempt_consistent) / len(current) if current else 0.0,
        "independent_scenes": len(scene_ids),
        "min_trials_per_scene": min(trials.values()) if trials else 0,
        "strategy_b_preference": (sum(row["score_b"] for row in scored) / len(scored)) if scored else 0.0,
        "strategy_b_preference_ci95": ci,
        "memory_pollution_count": pollution,
        "severe_contradictions": contradictions,
        "unrecoverable_pairs": unrecoverable,
        "adoption_gate_passed": gate,
        "recommendation": "adopt_variant_b" if gate else "retain_baseline_and_promote_failures",
        "failures": failures,
    }


def p12_failure_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for row in rows:
        categories = []
        if int(row.get("memory_pollution_count") or 0) > 0:
            categories.append("memory_pollution")
        if int(row.get("severe_contradictions") or 0) > 0:
            categories.append("compact_severe_contradiction")
        if row.get("recoverable") is False:
            categories.append("compact_unrecoverable")
        if row.get("position_consistent") is False:
            categories.append("judge_position_inconsistent")
        if str(row.get("judge_winner") or "") == "A":
            categories.append("variant_b_negative_gain")
        for category in categories:
            failures.append(
                {
                    "id": hashlib.sha256(f"{row.get('pair_id')}|{category}".encode("utf-8")).hexdigest()[:20],
                    "source": "p12_context_ab",
                    "category": category,
                    "pair_id": row.get("pair_id"),
                    "scene_id": row.get("scene_id"),
                    "variant_a": row.get("variant_a"),
                    "variant_b": row.get("variant_b"),
                    "contains_corpus_text": False,
                }
            )
    return failures


async def generate_p12_candidates(
    cases: List[Dict[str, Any]],
    *,
    gateway: Any,
    provider: str | None,
    temperature: float = 0.7,
    max_tokens: int = 2200,
) -> Dict[str, Any]:
    """Generate real-provider pairs from explicit production-derived context cases."""
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for case in cases:
        pair_id = str(case.get("pair_id") or case.get("id") or "").strip()
        scene_id = str(case.get("scene_id") or pair_id)
        variants = dict(case.get("variants") or {})
        if not pair_id or len(variants) != 2:
            failures.append(_p12_generation_failure(pair_id, scene_id, "invalid_case_schema"))
            continue
        roles = list(variants.items())
        if tuple(name for name, _ in roles) not in P12_CONTEXT_COMPARISONS:
            failures.append(_p12_generation_failure(pair_id, scene_id, "unsupported_p12_comparison"))
            continue
        if context_variant_fingerprint(roles[0][0], roles[0][1]) == context_variant_fingerprint(roles[1][0], roles[1][1]):
            failures.append(_p12_generation_failure(pair_id, scene_id, "context_not_distinct"))
            continue
        generation_order = roles if int(hashlib.sha256(pair_id.encode("utf-8")).hexdigest()[:2], 16) % 2 == 0 else list(reversed(roles))
        pair_rows = []
        for order, (variant_name, context) in enumerate(generation_order, 1):
            role = "A" if variant_name == roles[0][0] else "B"
            messages = _p12_writer_messages(case, variant_name, context)
            try:
                response = await gateway.chat(
                    messages,
                    provider=provider,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                failures.append(
                    _p12_generation_failure(pair_id, scene_id, "provider_error", detail=safe_error_code(exc))
                )
                continue
            parsed, error = parse_json_payload(str(response.get("content") or ""), expected_type=dict)
            candidate_text = str((parsed or {}).get("candidate_text") or "").strip()
            if error or not candidate_text:
                failures.append(_p12_generation_failure(pair_id, scene_id, "invalid_candidate_response", detail=error))
                continue
            context_fingerprint = context_variant_fingerprint(variant_name, context)
            row = {
                "id": f"{pair_id}-{role}",
                "pair_id": pair_id,
                "scene_id": scene_id,
                "chapter_id": case.get("chapter_id"),
                "strategy_role": role,
                "variant": variant_name,
                "context_fingerprint": context_fingerprint,
                "candidate_text": candidate_text,
                "candidate_sha256": hashlib.sha256(candidate_text.encode("utf-8")).hexdigest(),
                "scene_brief": case.get("scene_brief"),
                "prior_summary": case.get("prior_summary"),
                "judge_canon_summary": case.get("judge_canon_summary"),
                "writer_provider": response.get("provider") or provider,
                "writer_model": response.get("model"),
                "gateway_usage": response.get("usage") or {},
                "generation_order": order,
                "prompt_version": "p12-context-writer-v1",
                "memory_pollution_count": int((context or {}).get("memory_pollution_count") or 0),
                "severe_contradictions": int((context or {}).get("severe_contradictions") or 0),
                "recoverable": (context or {}).get("recoverable", True),
            }
            pair_rows.append(row)
        if len(pair_rows) == 2:
            candidates.extend(pair_rows)
    return {"candidates": candidates, "failures": failures, "pairs": len(candidates) // 2}


async def score_p12_candidates(
    candidates: List[Dict[str, Any]],
    *,
    provider: str | None,
    require_available: bool = False,
    pairwise_retries: int = 0,
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in candidates:
        grouped.setdefault(str(row.get("pair_id") or ""), {})[str(row.get("strategy_role") or "")] = row
    rows = []
    for pair_id, roles in grouped.items():
        first, second = roles.get("A"), roles.get("B")
        if not first or not second:
            continue
        case = {
            "canon_summary": first.get("judge_canon_summary") or second.get("judge_canon_summary") or "",
            "prior_summary": first.get("prior_summary") or second.get("prior_summary") or "",
            "scene_brief": first.get("scene_brief") or second.get("scene_brief") or "",
            "candidate_a": first.get("candidate_text") or "",
            "candidate_b": second.get("candidate_text") or "",
        }
        attempts = []
        usage_rows = []
        for attempt_number in range(1, max(1, int(pairwise_retries) + 1) + 1):
            comparison = await run_pointwise_pair_judge_eval(
                case,
                provider=provider,
                require_available=require_available,
            )
            usage_rows.extend(comparison.get("usage_rows") or [])
            judge = comparison.get("judge") or {}
            attempt = {
                "attempt": attempt_number,
                "available": bool(comparison.get("available")),
                "success": bool(comparison.get("success")),
                "order_invariant": bool(comparison.get("order_invariant")),
                "winner": str(judge.get("winner") or ""),
                "score_a": judge.get("score_a"),
                "score_b": judge.get("score_b"),
                "score_delta_b_minus_a": judge.get("score_delta_b_minus_a"),
                "provider": comparison.get("provider"),
                "model": comparison.get("model"),
                "prompt_version": comparison.get("prompt_version"),
                "comparison_method": comparison.get("comparison_method"),
                "error": comparison.get("error"),
            }
            attempts.append(attempt)
            if attempt["success"] and attempt["order_invariant"] and attempt["winner"] in {"A", "B", "tie"}:
                break
        selected = next(
            (
                attempt
                for attempt in attempts
                if attempt["success"] and attempt["order_invariant"] and attempt["winner"] in {"A", "B", "tie"}
            ),
            attempts[-1],
        )
        consistent = bool(selected["success"] and selected["order_invariant"])
        winner = selected["winner"] if consistent and selected["winner"] in {"A", "B", "tie"} else None
        judge_usage = {
            key: sum(int((usage or {}).get(key) or 0) for usage in usage_rows)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        row = {
            "pair_id": pair_id,
            "scene_id": first.get("scene_id") or second.get("scene_id"),
            "variant_a": first.get("variant"),
            "variant_b": second.get("variant"),
            "context_fingerprint_a": first.get("context_fingerprint"),
            "context_fingerprint_b": second.get("context_fingerprint"),
            "candidate_a_sha256": first.get("candidate_sha256"),
            "candidate_b_sha256": second.get("candidate_sha256"),
            "writer_provider": first.get("writer_provider"),
            "writer_model": first.get("writer_model"),
            "prompt_version": first.get("prompt_version"),
            "judge_winner": winner,
            "position_consistent": consistent,
            "judge_provider": selected.get("provider") or provider,
            "judge_model": selected.get("model"),
            "judge_prompt_version": selected.get("prompt_version") or POINTWISE_PAIR_JUDGE_PROMPT_VERSION,
            "comparison_method": selected.get("comparison_method"),
            "score_a": selected.get("score_a"),
            "score_b": selected.get("score_b"),
            "score_delta_b_minus_a": selected.get("score_delta_b_minus_a"),
            "judge_usage": judge_usage,
            "requests_attempted": len(usage_rows),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "memory_pollution_count": max(
                int(first.get("memory_pollution_count") or 0), int(second.get("memory_pollution_count") or 0)
            ),
            "severe_contradictions": max(
                int(first.get("severe_contradictions") or 0), int(second.get("severe_contradictions") or 0)
            ),
            "recoverable": bool(first.get("recoverable", True) and second.get("recoverable", True)),
        }
        row["pair_fingerprint"] = p12_pair_fingerprint(row)
        rows.append(row)
    return {"pairwise_rows": rows, "pairs": len(rows)}


def _p12_writer_messages(case: Dict[str, Any], variant_name: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
    payload = {
        "scene_brief": case.get("scene_brief"),
        "prior_summary": case.get("prior_summary"),
        "context": context,
        "task": "续写一个完整场景，只使用给定上下文，不解释过程。",
        "output_schema": {"candidate_text": "string"},
    }
    return [
        {
            "role": "system",
            "content": "你是长篇小说续写器。只输出 JSON 对象 candidate_text；保持人物、约束和叙事连续性。",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _p12_generation_failure(pair_id: str, scene_id: str, reason: str, *, detail: str = "") -> Dict[str, Any]:
    return {
        "id": hashlib.sha256(f"{pair_id}|{reason}|{detail}".encode("utf-8")).hexdigest()[:20],
        "pair_id": pair_id,
        "scene_id": scene_id,
        "reason": reason,
        "detail": detail[:200],
        "contains_corpus_text": False,
    }
