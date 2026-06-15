# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  编排器分析Mixin - 章节分析、事实表持久化和卡片创建
  AnalysisMixin - Chapter analysis, canon persistence, and card-creation methods.
  Extracted from orchestrator.py, injected back via Mixin inheritance.
  所有方法通过 self 访问 Orchestrator 的 storage / agent / select_engine 等属性。
"""

import time
from typing import Any, Dict, List, Optional

from app.schemas.canon import Fact, TimelineEvent, CharacterState
from app.schemas.draft import ChapterSummary, CardProposal
from app.schemas.card import CharacterCard, WorldCard, StyleCard
from app.schemas.evidence import EvidenceItem
from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger
from app.orchestrator._types import SessionStatus

logger = get_logger(__name__)


class AnalysisMixin:
    """
    编排器分析Mixin - 章节分析、事实表持久化和卡片创建

    Provides methods for analyzing chapter content, extracting canonical facts,
    updating character states, detecting proposals, and managing card creation.
    Supports batch operations for efficient multi-chapter processing.
    """

    def _resolve_volume_id_from_analysis(self, chapter: str, analysis: Dict[str, Any]) -> str:
        """
        从分析结果中最好地解析volume_id / Best-effort resolve volume_id for batching volume summary refresh.

        在批量保存/同步时，为避免每章都触发一次分卷摘要（LLM 调用），这里提前收集 volume_id，
        最终按卷统一刷新一次即可。

        During batch save/sync, pre-collect volume_ids to avoid triggering a volume summary
        LLM call for each chapter. Instead, refresh once per volume at the end.

        Args:
            chapter: 章节ID / Chapter identifier.
            analysis: 分析结果字典 / Analysis result dictionary.

        Returns:
            分卷ID，默认 'V1' / Volume ID (defaults to 'V1').
        """
        if isinstance(analysis, dict):
            summary = analysis.get("summary") or {}
            if isinstance(summary, dict):
                volume_id = str(summary.get("volume_id") or "").strip()
                if volume_id:
                    return volume_id
        normalized = self._normalize_chapter_id(chapter)
        return ChapterIDValidator.extract_volume_id(normalized) or "V1"

    async def _refresh_volume_summaries(self, project_id: str, volume_ids: List[str]) -> None:
        """
        刷新分卷摘要（每卷一次） / Rebuild volume summaries once per volume (best-effort).

        Generates volume-level summaries by aggregating chapter summaries.
        Prevents redundant processing by deduplicating volume IDs.

        Args:
            project_id: 项目ID / Project identifier.
            volume_ids: 分卷ID列表 / List of volume IDs to refresh.
        """
        seen = set()
        for volume_id in [str(v or "").strip() for v in (volume_ids or []) if str(v or "").strip()]:
            if volume_id in seen:
                continue
            seen.add(volume_id)
            try:
                volume_summaries = await self.draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)
                volume_summary = await self.archivist.generate_volume_summary(
                    project_id=project_id,
                    volume_id=volume_id,
                    chapter_summaries=volume_summaries,
                )
                await self.draft_storage.volume_storage.save_volume_summary(project_id, volume_summary)
            except Exception as exc:
                logger.warning("Failed to refresh volume summary for %s: %s", volume_id, exc)

    async def analyze_chapter(
        self,
        project_id: str,
        chapter: str,
        content: Optional[str] = None,
        chapter_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        手动触发章节分析（不持久化） / Manually trigger analysis for a chapter (no persistence).

        Analyzes chapter content to extract summaries, canonical facts, timeline events,
        and character states. Returns analysis payload without saving to storage.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            content: 章节内容 / Chapter content (optional, loads from draft if not provided).
            chapter_title: 章节标题 / Chapter title (optional).

        Returns:
            Analysis result dict with 'success' flag and 'analysis' payload.
        """
        try:
            draft_content = content or ""
            if not draft_content:
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    return {"success": False, "error": "No draft found"}

                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    return {"success": False, "error": "Draft content missing"}
                draft_content = draft.content

            self.current_project_id = project_id
            self.current_chapter = chapter
            await self._update_status(SessionStatus.GENERATING_BRIEF, "Analyzing content...")

            analysis = await self._build_analysis(
                project_id=project_id,
                chapter=chapter,
                content=draft_content,
                chapter_title=chapter_title,
            )

            await self._update_status(SessionStatus.IDLE, "Analysis completed.")
            return {"success": True, "analysis": analysis}
        except Exception as exc:
            return await self._handle_error(f"Analysis failed: {exc}", exc=exc)

    async def _build_analysis(
        self,
        project_id: str,
        chapter: str,
        content: str,
        chapter_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构建分析载荷（摘要、事实、建议）不持久化 / Build analysis payload (summary, facts, proposals) without persisting.

        Calls archivist to generate chapter summaries, extract canonical updates,
        and detect proposals. Combines results into comprehensive analysis.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            content: 章节内容文本 / Chapter content text.
            chapter_title: 章节标题 / Chapter title (optional).

        Returns:
            Analysis payload with summary, facts, timeline events, states, and proposals.
        """
        scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        title = chapter_title or (scene_brief.title if scene_brief and scene_brief.title else chapter)

        summary = await self.archivist.generate_chapter_summary(
            project_id=project_id,
            chapter=chapter,
            chapter_title=title,
            final_draft=content,
        )
        volume_id = summary.volume_id or ChapterIDValidator.extract_volume_id(chapter) or "V1"
        summary_data = summary.model_dump()
        summary_data["chapter"] = chapter
        summary_data["volume_id"] = volume_id
        summary_data["word_count"] = len(content)
        if not summary_data.get("title"):
            summary_data["title"] = title
        summary = ChapterSummary(**summary_data)

        canon_updates = await self.archivist.extract_canon_updates(
            project_id=project_id,
            chapter=chapter,
            final_draft=content,
        )

        facts = canon_updates.get("facts", []) or []
        if len(facts) > 5:
            facts = facts[:5]

        return {
            "summary": summary.model_dump(),
            "facts": [fact.model_dump() for fact in facts],
            "timeline_events": [event.model_dump() for event in canon_updates.get("timeline_events", []) or []],
            "character_states": [state.model_dump() for state in canon_updates.get("character_states", []) or []],
            # Auto card creation has been removed from analysis flow.
            "proposals": [],
        }

    async def analyze_sync(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        """
        批量分析和覆盖选定章节的摘要/事实/卡片 / Batch analyze and overwrite summaries/facts/cards for selected chapters.

        Performs full analysis pipeline for multiple chapters including:
        - Building analysis payload (summary, facts, timeline, character states)
        - Saving analysis to storage (with overwrite option)
        - Building chapter bindings with focus character detection
        - Refreshing volume summaries

        Args:
            project_id: 项目ID / Project identifier.
            chapters: 章节ID列表 / List of chapter identifiers.

        Returns:
            Batch result dict with per-chapter status and statistics.
        """
        results = []
        # Keep caller-selected chapter order stable to avoid UI reorder surprises.
        chapter_list = [str(ch).strip() for ch in (chapters or []) if str(ch).strip()]
        chapters = list(dict.fromkeys(chapter_list))
        total = len(chapters)
        completed = 0
        volume_ids_to_refresh: List[str] = []

        async def emit_progress(message: str) -> None:
            if not self.progress_callback:
                return
            await self.progress_callback(
                {
                    "status": "sync",
                    "message": message,
                    "project_id": project_id,
                }
            )

        if total == 0:
            return {"success": True, "results": []}

        chapter_list = [str(ch).strip() for ch in (chapters or []) if str(ch).strip()]
        chapters = list(dict.fromkeys(chapter_list))
        for chapter in chapters:
            try:
                completed += 1
                await emit_progress(f"同步分析中 ({completed}/{total})：{chapter}")
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    results.append({"chapter": chapter, "success": False, "error": "No draft found"})
                    continue
                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    results.append({"chapter": chapter, "success": False, "error": "Draft content missing"})
                    continue
                analysis = await self._build_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    content=draft.content,
                    chapter_title=None,
                )
                await emit_progress(f"同步保存中 ({completed}/{total})：{chapter}")
                volume_ids_to_refresh.append(self._resolve_volume_id_from_analysis(chapter, analysis))
                save_result = await self.save_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    analysis=analysis,
                    overwrite=True,
                    rebuild_volume_summary=False,
                )
                bindings_result = {"bindings_built": False}
                try:
                    from app.services.chapter_binding_service import chapter_binding_service

                    await emit_progress(f"同步绑定中 ({completed}/{total})：{chapter}")
                    focus_characters: List[str] = []
                    try:
                        focus_characters = await self.archivist.bind_focus_characters(
                            project_id=project_id,
                            chapter=chapter,
                            final_draft=draft.content,
                            limit=5,
                        )
                    except Exception as exc:
                        bindings_result["focus_error"] = str(exc)

                    base_binding = await chapter_binding_service.build_bindings(project_id, chapter, force=True)
                    if focus_characters:
                        base_binding["characters"] = focus_characters
                        base_binding["focus_characters"] = focus_characters
                        base_binding["binding_method"] = "llm_focus"
                    else:
                        base_binding["binding_method"] = base_binding.get("binding_method") or "algorithmic"

                    await chapter_binding_service.write_bindings(project_id, chapter, base_binding)
                    bindings_result["bindings_built"] = True
                    bindings_result["binding_method"] = base_binding.get("binding_method")
                    bindings_result["focus_characters"] = focus_characters
                except Exception as exc:
                    bindings_result["bindings_error"] = str(exc)
                # 将 analysis 一并返回给前端，用于批量同步后展示/校对“事实/摘要”等分析内容。
                # 注意：此处 analysis 已经持久化（save_analysis），前端若二次编辑可通过 save-analysis-batch 覆盖写入。
                results.append({"chapter": chapter, "analysis": analysis, **save_result, **bindings_result})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})

        await emit_progress("同步收尾：刷新分卷摘要...")
        await self._refresh_volume_summaries(project_id, volume_ids_to_refresh)
        await emit_progress("同步完成")
        return {"success": True, "results": results}

    async def analyze_batch(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        """
        批量分析章节并返回分析载荷 / Batch analyze chapters and return analysis payload.

        Analyzes multiple chapters without persisting results. Useful for previewing
        analysis before committing via save_analysis_batch.

        Args:
            project_id: 项目ID / Project identifier.
            chapters: 章节ID列表 / List of chapter identifiers.

        Returns:
            Batch result dict with per-chapter analysis payload.
        """
        results = []
        for chapter in chapters:
            try:
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    results.append({"chapter": chapter, "success": False, "error": "No draft found"})
                    continue
                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    results.append({"chapter": chapter, "success": False, "error": "Draft content missing"})
                    continue
                analysis = await self._build_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    content=draft.content,
                    chapter_title=None,
                )
                results.append({"chapter": chapter, "success": True, "analysis": analysis})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})

        return {"success": True, "results": results}

    async def save_analysis_batch(
        self,
        project_id: str,
        items: List[Dict[str, Any]],
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        持久化分析载荷批次 / Persist analysis payload batch.

        Saves multiple analysis payloads to storage at once. Optionally overwrites
        existing facts and settings. Batches volume summary refresh for efficiency.

        Args:
            project_id: 项目ID / Project identifier.
            items: 分析项列表，每项包含 'chapter' 和 'analysis' / List of items with 'chapter' and 'analysis'.
            overwrite: 覆盖现有数据 / Overwrite existing facts and cards.

        Returns:
            Batch result dict with per-item status and overall success flag.
        """
        results = []
        volume_ids_to_refresh: List[str] = []
        for item in items:
            chapter = item.get("chapter")
            analysis = item.get("analysis", {}) if isinstance(item, dict) else {}
            if not chapter:
                results.append({"chapter": "", "success": False, "error": "Missing chapter"})
                continue
            try:
                volume_ids_to_refresh.append(
                    self._resolve_volume_id_from_analysis(str(chapter), analysis if isinstance(analysis, dict) else {})
                )
                result = await self.save_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    analysis=analysis,
                    overwrite=overwrite,
                    rebuild_volume_summary=False,
                )
                results.append({"chapter": chapter, **result})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})
        await self._refresh_volume_summaries(project_id, volume_ids_to_refresh)
        return {"success": True, "results": results}

    async def save_analysis(
        self,
        project_id: str,
        chapter: str,
        analysis: Dict[str, Any],
        overwrite: bool = False,
        rebuild_volume_summary: bool = False,
    ) -> Dict[str, Any]:
        """
        持久化分析输出（摘要、事实、卡片） / Persist analysis output (summary, facts, cards).

        Saves chapter analysis including summaries, canonical facts, timeline events,
        and character states to storage. Optionally creates cards from proposals.
        Volume summary rebuild is off by default for speed; callers that need it
        (e.g. _analyze_content, _refresh_volume_summaries) enable it explicitly.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            analysis: 分析载荷 / Analysis result dictionary.
            overwrite: 覆盖现有数据 / Overwrite existing facts and settings.
            rebuild_volume_summary: 重建分卷摘要（默认关闭以加速保存） / Rebuild volume summary (off by default for speed).

        Returns:
            Save result dict with success flag and statistics.
        """
        try:
            summary_data = analysis.get("summary", {}) or {}
            summary_data["chapter"] = self._normalize_chapter_id(summary_data.get("chapter") or chapter)
            existing_summary = await self.draft_storage.get_chapter_summary(project_id, summary_data["chapter"])
            if existing_summary:
                # Preserve manual chapter ordering and stable metadata during analysis overwrite.
                if summary_data.get("order_index") is None:
                    summary_data["order_index"] = existing_summary.order_index
                if not summary_data.get("volume_id"):
                    summary_data["volume_id"] = existing_summary.volume_id
                if not summary_data.get("title"):
                    summary_data["title"] = existing_summary.title
            summary = ChapterSummary(**summary_data)
            summary.new_facts = []
            if not summary.volume_id:
                summary.volume_id = ChapterIDValidator.extract_volume_id(summary.chapter) or "V1"
            if not summary.title:
                summary.title = chapter

            await self.draft_storage.save_chapter_summary(project_id, summary)

            if rebuild_volume_summary:
                volume_summaries = await self.draft_storage.list_chapter_summaries(
                    project_id,
                    volume_id=summary.volume_id,
                )
                volume_summary = await self.archivist.generate_volume_summary(
                    project_id=project_id,
                    volume_id=summary.volume_id,
                    chapter_summaries=volume_summaries,
                )
                await self.draft_storage.volume_storage.save_volume_summary(project_id, volume_summary)

            facts_saved = 0
            timeline_saved = 0
            states_saved = 0

            # Overwrite: normalize + delete in a single read-write pass
            if overwrite:
                await self.canon_storage.delete_and_normalize_by_chapter(project_id, summary.chapter)

            # Load existing fact IDs once before the loop (avoid N × O(M) scans)
            existing_facts = await self.canon_storage.get_all_facts_raw(project_id)
            existing_ids = {item.get("id") for item in existing_facts if item.get("id")}
            next_fact_index = len(existing_facts) + 1

            facts_input = analysis.get("facts", []) or []
            if len(facts_input) > 5:
                facts_input = facts_input[:5]

            for item in facts_input:
                fact_data = item if isinstance(item, dict) else {}
                fact_data = {**fact_data}
                if not fact_data.get("statement") and not fact_data.get("content"):
                    continue
                fact_data["statement"] = fact_data.get("statement") or fact_data.get("content") or ""
                fact_data["source"] = fact_data.get("source") or summary.chapter
                fact_data["introduced_in"] = fact_data.get("introduced_in") or summary.chapter
                if not fact_data.get("id") or fact_data.get("id") in existing_ids:
                    fact_data["id"] = f"F{next_fact_index:04d}"
                    next_fact_index += 1
                existing_ids.add(fact_data["id"])
                await self.canon_storage.add_fact(project_id, Fact(**fact_data))
                facts_saved += 1

            for item in analysis.get("timeline_events", []) or []:
                event_data = item if isinstance(item, dict) else {}
                event_data = {**event_data, "source": event_data.get("source") or chapter}
                await self.canon_storage.add_timeline_event(project_id, TimelineEvent(**event_data))
                timeline_saved += 1

            for item in analysis.get("character_states", []) or []:
                state_data = item if isinstance(item, dict) else {}
                if not state_data.get("character"):
                    continue
                state_data = {**state_data, "last_seen": state_data.get("last_seen") or chapter}
                await self.canon_storage.update_character_state(project_id, CharacterState(**state_data))
                states_saved += 1

            return {
                "success": True,
                "stats": {
                    "facts_saved": facts_saved,
                    "timeline_saved": timeline_saved,
                    "states_saved": states_saved,
                    "cards_created": 0,
                },
            }
        except Exception as exc:
            return await self._handle_error(f"Analysis save failed: {exc}", exc=exc)

    async def _analyze_content(self, project_id: str, chapter: str, content: str):
        """
        运行后期草稿分析（摘要 + 事实表更新） / Run post-draft analysis (summaries + canon updates).

        Called after user confirms a chapter draft. Generates summaries at chapter
        and volume levels, extracts and persists canonical facts.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            content: 最终草稿内容 / Final draft content text.
        """
        try:
            normalized_chapter = self._normalize_chapter_id(chapter)
            scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
            chapter_title = scene_brief.title if scene_brief and scene_brief.title else chapter

            summary = await self.archivist.generate_chapter_summary(
                project_id=project_id,
                chapter=normalized_chapter,
                chapter_title=chapter_title,
                final_draft=content,
            )
            summary.chapter = normalized_chapter
            await self.draft_storage.save_chapter_summary(project_id, summary)

            volume_id = ChapterIDValidator.extract_volume_id(normalized_chapter) or "V1"
            volume_summaries = await self.draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)
            volume_summary = await self.archivist.generate_volume_summary(
                project_id=project_id,
                volume_id=volume_id,
                chapter_summaries=volume_summaries,
            )
            await self.draft_storage.volume_storage.save_volume_summary(project_id, volume_summary)
        except Exception as exc:
            logger.warning("Failed to generate summaries: %s", exc)

        try:
            canon_updates = await self.archivist.extract_canon_updates(
                project_id=project_id,
                chapter=normalized_chapter,
                final_draft=content,
            )

            for fact in canon_updates.get("facts", []) or []:
                await self.canon_storage.add_fact(project_id, fact)

            for event in canon_updates.get("timeline_events", []) or []:
                await self.canon_storage.add_timeline_event(project_id, event)

            for state in canon_updates.get("character_states", []) or []:
                await self.canon_storage.update_character_state(project_id, state)

            try:
                report = await self.canon_storage.detect_conflicts(
                    project_id=project_id,
                    chapter=chapter,
                    new_facts=canon_updates.get("facts", []) or [],
                    new_timeline_events=canon_updates.get("timeline_events", []) or [],
                    new_character_states=canon_updates.get("character_states", []) or [],
                )
                await self.draft_storage.save_conflict_report(
                    project_id=project_id,
                    chapter=chapter,
                    report=report,
                )
            except Exception as exc:
                logger.warning("Failed to detect conflicts: %s", exc)
        except Exception as exc:
            logger.warning("Failed to update canon: %s", exc)

    async def _detect_proposals(self, project_id: str, content: Any) -> List[Dict]:
        """
        从内容检测设定建议 / Detect setting proposals from content.

        Calls archivist to identify new characters, world settings, and other
        entities mentioned in the draft. Filters proposals by type to match
        product requirements (e.g., character creation may be disabled).

        Args:
            project_id: 项目ID / Project identifier.
            content: 草稿内容对象或文本 / Draft content object or text.

        Returns:
            设定建议列表 / List of setting proposal dicts.
        """
        # Product decision: disable auto proposal generation in analysis flow entirely.
        return []

    async def _create_cards_from_proposals(
        self,
        project_id: str,
        proposals: List[Dict[str, Any]],
        overwrite: bool = False,
    ) -> int:
        """
        从建议创建卡片，返回创建数量 / Create cards from proposals. Returns created count.

        Converts setting proposals into actual character and world cards,
        saving them to storage. Respects overwrite flag to update existing cards.

        Args:
            project_id: 项目ID / Project identifier.
            proposals: 设定建议列表 / List of proposal dicts.
            overwrite: 覆盖现有卡片 / Overwrite existing cards with same name.

        Returns:
            创建的卡片数量 / Number of cards created.
        """
        # Hard-stop safeguard: never auto-create cards from analysis proposals.
        return 0

        created = 0
        for item in proposals:
            try:
                proposal = CardProposal(**(item or {}))
            except Exception:
                continue

            name = (proposal.name or "").strip()
            if not name:
                continue

            ptype = (proposal.type or "").lower()
            if ptype == "character":
                existing = await self.card_storage.get_character_card(project_id, name)
                if existing and not overwrite:
                    continue
                card = CharacterCard(
                    name=name,
                    description=self._merge_card_description(
                        proposal.description,
                        proposal.rationale,
                    ),
                )
                await self.card_storage.save_character_card(project_id, card)
                created += 1
                continue

            if ptype == "world":
                existing = await self.card_storage.get_world_card(project_id, name)
                if existing and not overwrite:
                    continue
                card = WorldCard(
                    name=name,
                    description=self._merge_card_description(
                        proposal.description,
                        proposal.rationale,
                    ),
                )
                await self.card_storage.save_world_card(project_id, card)
                created += 1
                continue

        return created

    async def extract_style_profile(self, project_id: str, sample_text: str) -> StyleCard:
        """
        从示例文本提取写作风格指导 / Extract writing style guidance from sample text.

        Calls archivist to analyze writing style and return a StyleCard
        for consistent voice across the project.

        Args:
            project_id: 项目ID / Project identifier.
            sample_text: 示例文本 / Sample text to analyze.

        Returns:
            写作风格卡片 / StyleCard object with style guidance.
        """
        style_text = await self.archivist.extract_style_profile(sample_text)
        return StyleCard(style=style_text)

    async def _persist_research_trace_memory(
        self,
        project_id: str,
        chapter: str,
        working_memory_payload: Optional[Dict[str, Any]],
    ) -> None:
        """
        持久化研究追踪为内存证据项 / Persist research trace as memory evidence items.

        Converts research trace information (queries, rounds, sufficiency) into
        evidence items for later retrieval and context building.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            working_memory_payload: 工作记忆载荷 / Working memory payload with research trace.
        """
        if not working_memory_payload:
            return
        trace = working_memory_payload.get("research_trace") or []
        stop_reason = working_memory_payload.get("research_stop_reason") or ""
        report = working_memory_payload.get("sufficiency_report") or {}
        if not trace:
            return

        lines = [f"研究轮次: {len(trace)}", f"停止原因: {stop_reason or 'unknown'}"]
        if report:
            needs = "是" if report.get("needs_user_input") else "否"
            lines.append(f"证据不足需反问: {needs}")
            weak = report.get("weak_gaps") or []
            if weak:
                lines.append("薄弱缺口: " + "；".join([str(item) for item in weak[:4]]))

        for item in trace[:5]:
            if not isinstance(item, dict):
                continue
            queries = item.get("queries") or []
            types = item.get("types") or {}
            count = item.get("count")
            lines.append(f"第{item.get('round')}轮: {', '.join(queries[:4])} | types={types} | count={count}")

        text = "\n".join([line for line in lines if line])
        if not text:
            return

        try:
            from app.services.evidence_service import evidence_service
        except Exception as exc:
            logger.debug("evidence_service not available: %s", exc)
            return

        item = EvidenceItem(
            id=f"memory:research:{int(time.time())}",
            type="memory",
            text=text,
            source={"chapter": chapter, "kind": "research_trace"},
            scope="chapter",
            entities=[],
            meta={"kind": "research_trace"},
        )
        await evidence_service.append_memory_items(project_id, [item])

    async def _persist_answer_memory(
        self,
        project_id: str,
        chapter: str,
        answers: List[Dict[str, Any]],
    ) -> None:
        """
        持久化预写答题为内存证据项 / Persist pre-writing answers as memory evidence items.

        Converts user-provided pre-writing answers into evidence items
        for retrieval and context building in future sessions.

        Args:
            project_id: 项目ID / Project identifier.
            chapter: 章节ID / Chapter identifier.
            answers: 用户答题列表 / List of user answer dicts.
        """
        if not answers:
            return
        try:
            from app.services.evidence_service import evidence_service
            from app.services.working_memory_service import _answer_to_evidence_items
        except Exception as exc:
            logger.debug("evidence/working_memory service not available: %s", exc)
            return

        items = []
        for raw in _answer_to_evidence_items(answers, chapter=chapter):
            try:
                items.append(
                    EvidenceItem(
                        id=raw.get("id") or "",
                        type="memory",
                        text=raw.get("text") or "",
                        source={
                            **(raw.get("source") or {}),
                            "chapter": chapter,
                        },
                        scope="chapter",
                        entities=[],
                        meta=raw.get("meta") or {},
                    )
                )
            except Exception:
                continue

        if items:
            await evidence_service.append_memory_items(project_id, items)
