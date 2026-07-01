# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  工作记忆服务 - 编译写作工作记忆，通过缺口检测生成针对性问题，支持证据检索和用户交互。
  Working memory compilation and gap-driven questions - Builds working memory packs with evidence retrieval and generates user questions based on content gaps.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.evidence_service import evidence_service
from app.services.chapter_binding_service import chapter_binding_service
from app.schemas.draft import SceneBrief
from app.config import config
from app.utils.language import normalize_language

from app.services.working_memory_helpers import (
    _build_focus_terms,
    _focus_score_text,
    _select_focus_facts,
    _build_rule_text_to_card,
    _maybe_prefix_world_rule,
    _format_material_text,
    _clean_text_for_memory,
    _dedup_material_lines,
    _unique_gaps,
    _answered_gap_texts_from_answers,
    _unknown_gap_texts_from_answers,
    _answered_gap_texts_from_memory,
    _load_chapter_answer_memory_items,
    _query_hits,
    _is_focus_related,
    _merge_chapter_window,
    _unique_texts,
    truncate,
    _safe_score,
    _item_stars,
    _should_include_material,
    _dedup_items,
    _count_types,
    _answer_to_evidence_items,
    _make_question_key,
)


class WorkingMemoryService:
    """
    工作记忆编译服务 - 为写作过程编译上下文和生成缺口问题。

    Compile working memory, evidence packs, and generates gap-driven questions to guide user research.
    Supports semantic reranking, entity tracking, and memory persistence.

    Attributes:
        MIN_GAP_SUPPORT_SCORE: 缺口支持最小分数 / Minimum score for gap support
        MIN_WORLD_RULE_SCORE: 世界规则最小分数 / Minimum score for world rules
        MAX_ITEMS: 各类型证据的最大数量 / Max items per evidence type in memory
    """

    MIN_GAP_SUPPORT_SCORE = 3.0
    MIN_WORLD_RULE_SCORE = 3.5
    SEMANTIC_RERANK_TOP_K = 16

    MAX_ITEMS = {
        "world_rule": 6,
        "fact": 8,
        "summary": 4,
        "world_entity": 6,
        "character": 4,
        "text_chunk": 4,
        "memory": 4,
    }

    def build_gap_items(
        self,
        scene_brief: Optional[SceneBrief],
        chapter_goal: str,
        language: str = "zh",
        seed_characters: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Build gap items from scene brief and chapter goal.

        Args:
            scene_brief: Scene brief object or dict.
            chapter_goal: Target goal text.

        Returns:
            List of gap items with queries.
        """
        gaps: List[Dict[str, Any]] = []
        lang = normalize_language(language, default="zh")

        goal_text = str(chapter_goal or "").strip()
        brief_goal = str(getattr(scene_brief, "goal", "") or "").strip()
        goal_text = goal_text or brief_goal
        if goal_text:
            gaps.append(
                {
                    "kind": "plot_point",
                    "text": (
                        "围绕章节目标的关键推进点是什么（避免偏离目标）"
                        if lang == "zh"
                        else "What is the key progression point aligned with the chapter goal (avoid drifting off-goal)?"
                    ),
                    "queries": [goal_text],
                    "ask_user": True,
                }
            )

        characters = getattr(scene_brief, "characters", []) or []
        character_names = []
        for item in characters:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(getattr(item, "name", "") or "").strip()
            if name:
                character_names.append(name)

        if seed_characters:
            merged = []
            for item in seed_characters:
                name = str(item).strip()
                if name and name not in merged:
                    merged.append(name)
            for name in character_names:
                if name and name not in merged:
                    merged.append(name)
            character_names = merged

        if not character_names:
            gaps.append(
                {
                    "kind": "detail_gap",
                    "text": (
                        "本章涉及的主要角色有哪些"
                        if lang == "zh"
                        else "Who are the main characters involved in this chapter?"
                    ),
                    "queries": ["角色 人物 参与"] if lang == "zh" else ["main characters", "participants", "cast"],
                    "ask_user": True,
                }
            )
        else:
            for name in character_names[:2]:
                gaps.append(
                    {
                        "kind": "character_change",
                        "text": (
                            f"{name} 在本章的动机/状态是否有变化"
                            if lang == "zh"
                            else f"Does {name}'s motivation/state change in this chapter?"
                        ),
                        "queries": (
                            [f"{name} 动机", f"{name} 状态"]
                            if lang == "zh"
                            else [f"{name} motivation", f"{name} current state"]
                        ),
                        "ask_user": True,
                        "entity_name": name,
                    }
                )

        timeline_context = getattr(scene_brief, "timeline_context", {}) or {}
        if not timeline_context:
            gaps.append(
                {
                    "kind": "detail_gap",
                    "text": (
                        "本章时间/地点的具体边界是什么"
                        if lang == "zh"
                        else "What are the concrete boundaries of time and place in this chapter?"
                    ),
                    "queries": ["时间 地点 场景"] if lang == "zh" else ["time", "location", "setting"],
                    "ask_user": True,
                }
            )

        world_constraints = getattr(scene_brief, "world_constraints", []) or []
        if not world_constraints:
            gaps.append(
                {
                    "kind": "plot_point",
                    "text": (
                        "本章需遵守的世界规则/禁忌/代价有哪些"
                        if lang == "zh"
                        else "Which world rules/taboos/costs must be respected in this chapter?"
                    ),
                    "queries": ["规则 禁忌 代价 限制"] if lang == "zh" else ["rules", "taboos", "cost", "constraints"],
                    "ask_user": True,
                }
            )

        facts = getattr(scene_brief, "facts", []) or []
        if not facts:
            gaps.append(
                {
                    "kind": "detail_gap",
                    "text": (
                        "与本章目标直接相关的已确立事实有哪些"
                        if lang == "zh"
                        else "Which established facts directly matter for this chapter goal?"
                    ),
                    "queries": ["关键事实 已确立事实"] if lang == "zh" else ["key facts", "established facts"],
                    "ask_user": True,
                }
            )

        return _unique_gaps(gaps, limit=8)

    async def prepare(
        self,
        project_id: str,
        chapter: str,
        scene_brief: Optional[SceneBrief],
        chapter_goal: str,
        language: str = "zh",
        user_answers: Optional[List[Dict[str, Any]]] = None,
        extra_queries: Optional[List[str]] = None,
        force_minimum_questions: Optional[bool] = None,
        semantic_rerank: Optional[bool] = None,
        round_index: Optional[int] = None,
        trace_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Prepare working memory, evidence pack, and questions.

        Args:
            project_id: Target project id.
            chapter: Chapter id.
            scene_brief: Scene brief object or dict.
            chapter_goal: Chapter goal text.
            semantic_rerank: Enable semantic rerank when available.
            round_index: Optional research round index for trace metadata.
            trace_note: Optional note to include in retrieval stats.

        Returns:
            Dict containing working_memory, gaps, unresolved_gaps, evidence_pack, retrieval_requests, questions.
        """
        user_answers_list = user_answers or []
        lang = normalize_language(language, default="zh")
        if semantic_rerank is None:
            semantic_rerank = bool((config.get("retrieval") or {}).get("semantic_rerank", True))
        rerank_top_k = int((config.get("retrieval") or {}).get("rerank_top_k", self.SEMANTIC_RERANK_TOP_K))

        seed_window = 2
        recent_chapters = await chapter_binding_service.get_recent_chapters(
            project_id,
            chapter,
            window=seed_window,
            include_current=False,
        )
        seed_entities = await chapter_binding_service.get_seed_entities(
            project_id,
            chapter,
            window=seed_window,
            ensure_built=True,
        )
        instruction_entities = await chapter_binding_service.extract_entities_from_text(project_id, chapter_goal)
        instruction_characters = instruction_entities.get("characters") or []
        instruction_worlds = instruction_entities.get("world_entities") or []
        seed_entities = list(dict.fromkeys(seed_entities + instruction_characters + instruction_worlds))

        loose_mentions = chapter_binding_service.extract_loose_mentions(chapter_goal, limit=6)
        missing_mentions = [m for m in (loose_mentions or []) if m and m not in seed_entities]
        binding_chapters = await chapter_binding_service.get_chapters_for_entities(
            project_id,
            instruction_characters + instruction_worlds,
            limit=6,
        )
        recent_character_candidates: List[str] = []
        for ch in recent_chapters:
            bindings = await chapter_binding_service.read_bindings(project_id, ch)
            if not bindings:
                continue
            recent_character_candidates.extend(bindings.get("characters") or [])
        recent_character_candidates = list(dict.fromkeys([item for item in recent_character_candidates if item]))
        if instruction_characters:
            recent_character_candidates = list(
                dict.fromkeys([item for item in instruction_characters + recent_character_candidates if item])
            )

        gaps = self.build_gap_items(
            scene_brief,
            chapter_goal,
            language=lang,
            seed_characters=recent_character_candidates,
        )
        extra_list = [str(q).strip() for q in (extra_queries or []) if str(q).strip()]
        if missing_mentions:
            extra_list = list(dict.fromkeys(extra_list + [str(m).strip() for m in missing_mentions if str(m).strip()]))[
                :8
            ]
        if extra_list:
            gaps.append(
                {
                    "kind": "extra_research",
                    "text": "研究补充查询" if lang == "zh" else "Supplementary research queries",
                    "queries": extra_list,
                    "ask_user": False,
                }
            )
        query_list = []
        for gap in gaps:
            query_list.extend([q for q in gap.get("queries", []) if q])
        query_list = list(dict.fromkeys(query_list))

        answer_items = _answer_to_evidence_items(user_answers_list, chapter=chapter)
        answered_gap_texts = _answered_gap_texts_from_answers(gaps, user_answers_list)
        unknown_gap_texts = _unknown_gap_texts_from_answers(gaps, user_answers_list)

        # If user answers are not provided in this call (e.g. new session),
        # reuse persisted answer memories for the current chapter to avoid
        # re-asking and to keep working memory consistent.
        persisted_answer_items: List[Dict[str, Any]] = []
        if not user_answers_list:
            persisted_answer_items = await _load_chapter_answer_memory_items(project_id, chapter)
            answered_gap_texts |= _answered_gap_texts_from_memory(gaps, persisted_answer_items, chapter)

        evidence_groups: List[Dict[str, Any]] = []
        retrieval_requests: List[Dict[str, Any]] = []
        combined_items: List[Dict[str, Any]] = []
        gap_supported: Dict[str, bool] = {}
        gap_support_scores: Dict[str, float] = {}
        skip_retrieval_kinds = {"character_change"}
        trace_meta = {"round": round_index, "note": trace_note or ""}
        for gap in gaps:
            queries = [q for q in gap.get("queries", []) if q]
            if not queries:
                continue
            gap_text = str(gap.get("text") or "").strip()
            gap_kind = str(gap.get("kind") or "").strip()
            if gap_text and gap_text in answered_gap_texts and gap_kind in skip_retrieval_kinds:
                # For answered non-plot gaps (especially character state checks),
                # skip retrieval to avoid dragging in replay-heavy evidence.
                gap_supported[gap_text] = True
                gap_support_scores[gap_text] = self.MIN_GAP_SUPPORT_SCORE + 1.0
                retrieval_requests.append(
                    {
                        "gap": gap,
                        "queries": queries,
                        "types": {},
                        "count": 0,
                        "skipped": True,
                        "reason": "answered_gap_skip_retrieval",
                    }
                )
                continue
            text_chunk_chapters = None
            semantic_rerank_enabled = False
            rerank_query = None
            if gap_kind == "plot_point":
                text_chunk_chapters = _merge_chapter_window(recent_chapters, binding_chapters)
                semantic_rerank_enabled = bool(semantic_rerank)
                rerank_query = f"{chapter_goal} | {gap_text}" if gap_text else str(chapter_goal or "")
            result = await evidence_service.search(
                project_id=project_id,
                queries=queries,
                seed_entities=seed_entities,
                include_text_chunks=True,
                text_chunk_chapters=text_chunk_chapters,
                semantic_rerank=semantic_rerank_enabled,
                rerank_query=rerank_query,
                rerank_top_k=rerank_top_k,
                trace_meta=trace_meta,
            )
            items = result.get("items", [])
            stats = result.get("stats", {})
            evidence_groups.append(
                {
                    "gap": gap,
                    "queries": queries,
                    "items": items,
                    "stats": stats,
                }
            )
            retrieval_requests.append(
                {
                    "gap": gap,
                    "queries": queries,
                    "types": stats.get("types", {}),
                    "count": len(items),
                    "top_sources": stats.get("top_sources") or [],
                }
            )
            combined_items.extend(items)
            if gap_text:
                score = self._gap_support_score(gap, items)
                gap_support_scores[gap_text] = score
                gap_supported[gap_text] = score >= self.MIN_GAP_SUPPORT_SCORE

        # Defensive fallback: even if all gaps are considered "answered", ensure we
        # still retrieve goal-related evidence so working memory isn't reduced to
        # Q&A memories only.
        if not retrieval_requests:
            goal_text = str(chapter_goal or "").strip()
            brief_goal = str(getattr(scene_brief, "goal", "") or "").strip()
            goal_text = goal_text or brief_goal
            if goal_text:
                result = await evidence_service.search(
                    project_id=project_id,
                    queries=[goal_text],
                    seed_entities=seed_entities,
                    include_text_chunks=True,
                    text_chunk_chapters=_merge_chapter_window(recent_chapters, binding_chapters),
                    semantic_rerank=bool(semantic_rerank),
                    rerank_query=goal_text,
                    rerank_top_k=rerank_top_k,
                    trace_meta=trace_meta,
                )
                items = result.get("items", [])
                stats = result.get("stats", {})
                evidence_groups.append(
                    {
                        "gap": {"kind": "fallback", "text": "goal_fallback", "queries": [goal_text], "ask_user": False},
                        "queries": [goal_text],
                        "items": items,
                        "stats": stats,
                    }
                )
                retrieval_requests.append(
                    {
                        "gap": {"kind": "fallback", "text": "goal_fallback", "queries": [goal_text], "ask_user": False},
                        "queries": [goal_text],
                        "types": stats.get("types", {}),
                        "count": len(items),
                        "skipped": False,
                        "top_sources": stats.get("top_sources") or [],
                    }
                )
                combined_items.extend(items)

        combined_items.extend(answer_items)
        combined_items.extend(persisted_answer_items)
        deduped_items = _dedup_items(combined_items)

        goal_text = str(chapter_goal or "").strip()
        brief_goal = str(getattr(scene_brief, "goal", "") or "").strip()
        goal_text = goal_text or brief_goal
        focus_terms = _build_focus_terms(scene_brief, goal_text)
        if recent_character_candidates:
            focus_terms.extend(recent_character_candidates)
            focus_terms = list(dict.fromkeys([t for t in focus_terms if t]))

        minimum_questions = bool(force_minimum_questions)
        unresolved_gaps = self._select_unresolved_gaps(
            gaps,
            gap_supported,
            gap_support_scores=gap_support_scores,
            focus_terms=focus_terms,
            force_minimum_questions=minimum_questions,
        )

        sufficiency_report = self._build_sufficiency_report(
            gaps=gaps,
            gap_supported=gap_supported,
            gap_support_scores=gap_support_scores,
            evidence_items=deduped_items,
            focus_terms=focus_terms,
            unknown_gap_texts=unknown_gap_texts,
        )

        evidence_pack = {
            "items": deduped_items,
            "groups": evidence_groups,
            "stats": {
                "total": len(deduped_items),
                "types": _count_types(deduped_items),
                "queries": query_list,
            },
        }

        questions = self._build_questions(unresolved_gaps, chapter, language=lang, unknown_gap_texts=unknown_gap_texts)

        working_memory = self._compile_working_memory(
            scene_brief=scene_brief,
            chapter_goal=chapter_goal,
            evidence_items=deduped_items,
            unresolved_gaps=unresolved_gaps,
        )

        return {
            "working_memory": working_memory,
            "gaps": gaps,
            "unresolved_gaps": unresolved_gaps,
            "evidence_pack": evidence_pack,
            "retrieval_requests": retrieval_requests,
            "seed_entities": seed_entities,
            "seed_window": seed_window,
            "questions": questions,
            "sufficiency_report": sufficiency_report,
        }

    def _select_unresolved_gaps(
        self,
        gaps: List[Dict[str, Any]],
        supported: Dict[str, bool],
        gap_support_scores: Dict[str, float],
        focus_terms: List[str],
        force_minimum_questions: bool = True,
    ) -> List[Dict[str, Any]]:
        askable = [g for g in gaps if g.get("ask_user", True)]
        if not askable:
            return []

        selected: List[Dict[str, Any]] = []
        seen = set()

        def add_gap(gap: Dict[str, Any]) -> None:
            text = gap.get("text") or ""
            if not text or text in seen:
                return
            selected.append(gap)
            seen.add(text)

        if force_minimum_questions:
            # Always ask at least one plot-focused question to confirm the intended
            # chapter push, even when retrieval finds related evidence.
            for gap in askable:
                if gap.get("kind") == "plot_point":
                    add_gap(gap)
                    break

        for gap in askable:
            if len(selected) >= 3:
                break
            text = gap.get("text") or ""
            if not text:
                continue
            if not supported.get(text, False):
                add_gap(gap)
                continue
            score = gap_support_scores.get(text, 0.0)
            is_focus = _is_focus_related(text, focus_terms) or gap.get("kind") == "plot_point"
            if score < (self.MIN_GAP_SUPPORT_SCORE + 0.8) and is_focus:
                add_gap(gap)

        if force_minimum_questions:
            for gap in askable:
                if len(selected) >= 3:
                    break
                add_gap(gap)

        return selected

    def _build_questions(
        self,
        gaps: List[Dict[str, Any]],
        chapter: str,
        language: str = "zh",
        unknown_gap_texts: Optional[set] = None,
    ) -> List[Dict[str, str]]:
        questions = []
        lang = normalize_language(language, default="zh")
        for gap in gaps[:3]:
            kind = gap.get("kind") or "detail_gap"
            text = gap.get("text") or ""
            if not text:
                continue
            if unknown_gap_texts and text in unknown_gap_texts:
                continue
            if lang == "en":
                q = str(text).strip()
                starts_like_question = bool(
                    re.match(r"^(what|who|which|where|when|why|how|does|do|is|are|can|should)\b", q, re.I)
                )
                if starts_like_question or "?" in q:
                    question = q
                    if not question.endswith("?"):
                        question += "?"
                else:
                    if kind == "plot_point":
                        question = f"To achieve this chapter goal, {q.rstrip('.')}?"
                    elif kind == "character_change":
                        question = f"Character: {q.rstrip('.')}?"
                    else:
                        question = f"Details: {q.rstrip('.')}?"
                reason = f"Insufficient evidence; gap: {q}"
            else:
                if kind == "plot_point":
                    question = f"为达成本章目标，{text}？"
                elif kind == "character_change":
                    question = f"角色方面：{text}？"
                else:
                    question = f"细节方面：{text}？"
                reason = f"证据不足，缺口：{text}"
            questions.append(
                {
                    "type": kind,
                    "text": question,
                    "key": _make_question_key(chapter, kind, text),
                    "reason": reason,
                }
            )
        return questions

    def _gap_support_score(self, gap: Dict[str, Any], items: List[Dict[str, Any]]) -> float:
        queries = [q for q in gap.get("queries", []) if q]
        if not queries:
            return 0.0
        best = 0.0
        for item in items or []:
            text = str(item.get("text") or "")
            if not text:
                continue
            if not _query_hits(text, queries):
                continue
            best = max(best, _safe_score(item))
        return best

    def _build_sufficiency_report(
        self,
        gaps: List[Dict[str, Any]],
        gap_supported: Dict[str, bool],
        gap_support_scores: Dict[str, float],
        evidence_items: List[Dict[str, Any]],
        focus_terms: List[str],
        unknown_gap_texts: set,
    ) -> Dict[str, Any]:
        unresolved = []
        weak = []
        critical_weak = []
        missing_entities = []

        for gap in gaps or []:
            if not gap.get("ask_user", True):
                continue
            text = str(gap.get("text") or "").strip()
            if not text:
                continue
            supported = gap_supported.get(text, False)
            score = gap_support_scores.get(text, 0.0)
            if not supported:
                unresolved.append(text)
                missing_entities.append(text)
            elif score < (self.MIN_GAP_SUPPORT_SCORE + 0.8):
                weak.append(text)
                is_focus = _is_focus_related(text, focus_terms) or gap.get("kind") == "plot_point"
                if is_focus:
                    critical_weak.append(text)

        insufficient = bool(unresolved or critical_weak)
        report = {
            "sufficient": not insufficient,
            "needs_user_input": insufficient,
            "missing_entities": list(dict.fromkeys(missing_entities)),
            "weak_gaps": list(dict.fromkeys(weak)),
            "critical_weak_gaps": list(dict.fromkeys(critical_weak)),
            "unknown_gaps": list(dict.fromkeys(list(unknown_gap_texts or []))),
            "evidence_types": _count_types(evidence_items or []),
        }
        return report

    def _compile_working_memory(
        self,
        scene_brief: Optional[SceneBrief],
        chapter_goal: str,
        evidence_items: List[Dict[str, Any]],
        unresolved_gaps: List[Dict[str, Any]],
    ) -> str:
        goal_text = str(chapter_goal or "").strip()
        brief_goal = str(getattr(scene_brief, "goal", "") or "").strip()
        goal_text = goal_text or brief_goal or "未提供"

        world_constraints = list(getattr(scene_brief, "world_constraints", []) or [])
        forbidden = list(getattr(scene_brief, "forbidden", []) or [])
        facts = list(getattr(scene_brief, "facts", []) or [])
        focus_terms = _build_focus_terms(scene_brief, goal_text)

        lines = ["本章目标: " + goal_text]

        lines.append("硬约束:")
        constraint_lines = []
        rule_text_to_card = _build_rule_text_to_card(evidence_items)
        constraint_lines.extend([_maybe_prefix_world_rule(str(item), rule_text_to_card) for item in world_constraints])
        constraint_lines.extend([_clean_text_for_memory(f"禁忌: {item}") for item in forbidden if item])
        world_rule_items = [item for item in evidence_items if item.get("type") == "world_rule"]
        world_rule_items.sort(key=lambda x: (_item_stars(x), _safe_score(x)), reverse=True)
        for item in world_rule_items:
            if _item_stars(item) < 3:
                continue
            if _safe_score(item) < self.MIN_WORLD_RULE_SCORE:
                continue
            text = _format_material_text(item)
            if text:
                constraint_lines.append(text)
        constraint_lines = _dedup_material_lines(_unique_texts(constraint_lines))
        if constraint_lines:
            for item in constraint_lines[: self.MAX_ITEMS["world_rule"]]:
                lines.append(f"- {item}")
        else:
            lines.append("- 无")

        lines.append("可用素材:")
        material_lines: List[str] = []
        for t in ["fact", "summary", "text_chunk", "world_entity", "character", "memory"]:
            candidates = [item for item in evidence_items if item.get("type") == t and _should_include_material(item)]
            if t in {"text_chunk", "summary"} and focus_terms:
                candidates.sort(
                    key=lambda x: (_focus_score_text(str(x.get("text") or ""), focus_terms), _safe_score(x)),
                    reverse=True,
                )
                focused = [
                    item for item in candidates if _focus_score_text(str(item.get("text") or ""), focus_terms) > 0
                ]
                candidates = focused or candidates
            else:
                if t in {"world_entity", "character"}:
                    candidates.sort(key=lambda x: (_item_stars(x), _safe_score(x)), reverse=True)
                else:
                    candidates.sort(key=_safe_score, reverse=True)
            for item in candidates[: self.MAX_ITEMS.get(t, 4)]:
                text = _format_material_text(item)
                if text:
                    material_lines.append(text)

        material_lines.extend(_select_focus_facts(facts, focus_terms, limit=12))
        material_lines = _dedup_material_lines(_unique_texts(material_lines))
        if material_lines:
            for item in material_lines:
                lines.append(f"- {truncate(_clean_text_for_memory(item), 140)}")
        else:
            lines.append("- 无")

        lines.append("未解决缺口:")
        if unresolved_gaps:
            for gap in unresolved_gaps[:6]:
                lines.append(f"- {gap.get('text')}")
        else:
            lines.append("- 无")

        return "\n".join(lines)


working_memory_service = WorkingMemoryService()
