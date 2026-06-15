# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  撰稿人智能体 - 根据场景简要和上下文信息生成章节草稿。
  Writer Agent responsible for generating novel draft chapters based on scene briefs.
"""

from typing import Any, Dict, List, Optional

from app.config import config as app_cfg
from app.context_engine.token_counter import count_tokens, estimate_tokens_fast
from app.utils.logger import get_logger
from app.utils.llm_output import parse_json_payload
from app.utils.text import normalize_prose_paragraphs

from app.agents.base import BaseAgent
from app.prompts import (
    get_writer_system_prompt,
    writer_draft_prompt,
    writer_questions_prompt,
    writer_research_plan_prompt,
)
from app.schemas.draft import SceneBrief
from app.schemas.card import StyleCard

logger = get_logger(__name__)

_writer_cfg = app_cfg.get("writer", {})
DEFAULT_TARGET_WORD_COUNT = int(_writer_cfg.get("default_target_word_count", 3000))


def _get_field(obj, field, default=""):
    """Safely extract field from object or dict, handling missing attributes gracefully."""
    if hasattr(obj, field):
        return getattr(obj, field, default)
    if isinstance(obj, dict):
        return obj.get(field, default)
    return default


class WriterAgent(BaseAgent):
    """
    撰稿人智能体 - 生成章节初稿

    Agent responsible for generating novel draft chapters based on
    scene briefs, context packages, and user feedback. Supports streaming output,
    pre-writing question generation, and research plan suggestion.

    Attributes:
        DEFAULT_QUESTIONS: Pre-writing questions for user confirmation.
    """

    DEFAULT_QUESTIONS = {
        "zh": [
            {"type": "plot_point", "text": "为达成本章目标，尚缺的剧情/世界信息是什么？"},
            {"type": "character_change", "text": "哪些主角的动机或情绪需再确认，避免违背既有事实？"},
            {"type": "detail_gap", "text": "还有哪些具体细节（地点/时间/物件）需要确定后再写？"},
        ],
        "en": [
            {
                "type": "plot_point",
                "text": "What plot or world-building information is still needed to achieve this chapter's goal?",
            },
            {
                "type": "character_change",
                "text": "Which characters' motivations or emotions need clarification to avoid contradicting established facts?",
            },
            {
                "type": "detail_gap",
                "text": "What specific details (setting, timeline, objects) should be settled before writing?",
            },
        ],
    }

    def get_agent_name(self) -> str:
        """获取智能体标识 - 返回 'writer'"""
        return "writer"

    def get_system_prompt(self) -> str:
        """获取系统提示词 - 撰稿人专用"""
        return get_writer_system_prompt(language=self.language)

    async def execute(self, project_id: str, chapter: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行撰稿 - 生成章节初稿并保存

        Main entry point for draft generation. Loads scene brief, gathers context,
        generates draft via LLM, extracts confirmations, and saves version.

        Args:
            project_id: Project identifier.
            chapter: Chapter identifier.
            context: Context dict with scene_brief, style_card, facts, etc.

        Returns:
            Dict with success status, draft object, word count, pending confirmations.
        """
        scene_brief = context.get("scene_brief")
        if not scene_brief:
            scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)

        if not scene_brief:
            return {"success": False, "error": "Scene brief not found"}

        # ============================================================================
        # Load previous chapter context / 加载前置章节信息
        # ============================================================================
        previous_summaries = context.get("previous_summaries")
        context_package = context.get("context_package")
        if previous_summaries is None and context_package:
            previous_summaries = self._build_previous_summaries_from_context(context_package)
        if previous_summaries is None:
            previous_summaries = await self._load_previous_summaries(project_id, chapter)

        # ============================================================================
        # Extract context from request / 提取上下文数据
        # ============================================================================
        style_card = context.get("style_card")
        character_cards = context.get("character_cards") or []
        world_cards = context.get("world_cards") or []
        facts = context.get("facts") or []
        text_chunks = context.get("text_chunks") or []
        working_memory = context.get("working_memory")
        unresolved_gaps = context.get("unresolved_gaps") or []
        timeline = context.get("timeline") or []
        character_states = context.get("character_states") or []
        chapter_goal = context.get("chapter_goal")
        user_answers = context.get("user_answers") or []
        user_feedback = context.get("user_feedback") or ""
        evidence_pack = context.get("evidence_pack")

        draft_content = await self._generate_draft(
            scene_brief=scene_brief,
            target_word_count=context.get("target_word_count", DEFAULT_TARGET_WORD_COUNT),
            previous_summaries=previous_summaries,
            style_card=style_card,
            character_cards=character_cards,
            world_cards=world_cards,
            facts=facts,
            text_chunks=text_chunks,
            working_memory=working_memory,
            unresolved_gaps=unresolved_gaps,
            timeline=timeline,
            character_states=character_states,
            chapter_goal=chapter_goal,
            user_answers=user_answers,
            user_feedback=user_feedback,
            evidence_pack=evidence_pack,
        )

        draft_content = normalize_prose_paragraphs(draft_content, language=self.language)
        pending_confirmations = []
        word_count = len(draft_content)

        draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version="v1",
            content=draft_content,
            word_count=word_count,
            pending_confirmations=pending_confirmations,
        )

        # Build chapter bindings to enable context linking
        try:
            from app.services.chapter_binding_service import chapter_binding_service

            await chapter_binding_service.build_bindings(project_id, chapter, force=True)
        except Exception as exc:
            logger.warning("Failed to build chapter bindings for %s:%s: %s", project_id, chapter, exc)

        return {
            "success": True,
            "draft": draft,
            "word_count": word_count,
            "pending_confirmations": pending_confirmations,
        }

    async def generate_questions(
        self, context_package: Dict[str, Any], scene_brief: Optional[SceneBrief], chapter_goal: str
    ) -> List[Dict[str, str]]:
        """
        生成写前问卷 - 引导用户确认重要细节

        Generate pre-writing questions to clarify user intent and context.
        Returns either LLM-generated questions or default template questions.

        Args:
            context_package: Dict with summary data from previous chapters.
            scene_brief: Scene brief object with chapter context.
            chapter_goal: User-provided chapter goal/objective.

        Returns:
            List of 3 question dicts with "type" and "text" keys.
        """
        brief_chapter = _get_field(scene_brief, "chapter", "")
        brief_title = _get_field(scene_brief, "title", "")
        brief_goal = _get_field(scene_brief, "goal", "")
        brief_characters = _get_field(scene_brief, "characters", [])

        characters_text = []
        for char in brief_characters or []:
            if isinstance(char, dict):
                characters_text.append(char.get("name", str(char)))
            elif hasattr(char, "name"):
                characters_text.append(char.name)
            else:
                characters_text.append(str(char))

        context_items = [
            f"Chapter: {brief_chapter}",
            f"Title: {brief_title}",
            f"Goal: {brief_goal or chapter_goal}",
            f"Characters: {', '.join(characters_text) if characters_text else 'None'}",
        ]

        if context_package:
            _wlimits = app_cfg.get("writer", {}).get("context_limits", {})
            context_items.append("事实摘要（节选，供反问参考）：")
            for key in ["summary_with_events", "summary_only", "full_facts"]:
                items = context_package.get(key, []) or []
                for item in items[: int(_wlimits.get("fact_summary_items", 2))]:
                    summary = str(item.get("summary") or "").strip()
                    events = item.get("key_events") or []
                    chapter_id = item.get("chapter") or ""
                    title = item.get("title") or ""
                    if summary or events:
                        block = [f"- {chapter_id} {title}".strip()]
                        if summary:
                            block.append(f"摘要：{summary}")
                        if events:
                            block.append(
                                "事件：" + "；".join([str(e) for e in events[: int(_wlimits.get("event_items", 4))]])
                            )
                        context_items.append("\n".join(block))
        prompt = writer_questions_prompt(context_items, language=self.language)

        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )

        raw = await self.call_llm(messages)
        data, err = parse_json_payload(raw, expected_type=list)
        if err:
            logger.warning("Writer questions parse failed: %s", err)
            logger.debug("Writer questions raw preview: %s", str(raw or "")[:200])
        if isinstance(data, list) and 1 <= len(data) <= 5:
            cleaned = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                q_type = item.get("type")
                text = item.get("text")
                if q_type and text:
                    cleaned.append({"type": q_type, "text": text})
            if cleaned:
                return cleaned

        return list(self.DEFAULT_QUESTIONS.get(self.language, self.DEFAULT_QUESTIONS["zh"]))

    async def generate_research_plan(
        self,
        chapter_goal: str,
        unresolved_gaps: List[Dict[str, Any]],
        evidence_stats: Dict[str, Any],
        round_index: int,
    ) -> Dict[str, Any]:
        """
        生成下一轮检索计划 - 生成查询关键字

        Generate a compact research plan (search queries) for the next evidence retrieval round.
        Helps system find relevant information to fill unresolved gaps.

        Args:
            chapter_goal: Chapter objective/target.
            unresolved_gaps: List of unresolved information gaps.
            evidence_stats: Statistics about current evidence pack.
            round_index: Which retrieval round this is (0-based).

        Returns:
            Dict with "queries" (list of search strings) and "note" (strategy note).
        """
        gap_texts = []
        for gap in unresolved_gaps or []:
            if not isinstance(gap, dict):
                continue
            text = str(gap.get("text") or "").strip()
            if text:
                gap_texts.append(text)
        prompt = writer_research_plan_prompt(
            chapter_goal=chapter_goal,
            gap_texts=gap_texts,
            evidence_stats=evidence_stats,
            round_index=round_index,
            language=self.language,
        )

        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=[],
        )

        raw = await self.call_llm(messages)
        data, err = parse_json_payload(raw, expected_type=dict)
        if err:
            logger.warning("Writer plan parse failed: %s", err)
            logger.debug("Writer plan raw preview: %s", str(raw or "")[:200])
        if isinstance(data, dict):
            queries = data.get("queries") or []
            if isinstance(queries, list):
                cleaned = [str(q).strip() for q in queries if str(q).strip()]
                if cleaned:
                    _wl = app_cfg.get("writer", {}).get("context_limits", {})
                    return {
                        "queries": cleaned[: int(_wl.get("query_items", 4))],
                        "note": str(data.get("note") or "").strip(),
                    }

        # Fallback: use gap texts as queries
        fallback = [t for t in gap_texts if t][:3]
        return {"queries": fallback, "note": "fallback_from_gaps"}

    async def execute_stream_draft(self, project_id: str, chapter: str, context: Dict[str, Any]):
        """
        流式生成草稿 - 逐token实时输出，不包含计划标记

        Stream draft generation to frontend in real-time (text only, no plan tags).
        Useful for long-running operations that need incremental feedback.

        Args:
            project_id: Project identifier.
            chapter: Chapter identifier.
            context: Context dict with same structure as execute().

        Yields:
            Draft text tokens as they arrive from LLM.
        """
        scene_brief = context.get("scene_brief")
        if not scene_brief:
            yield "[Error: Scene brief not found]"
            return

        messages = self._build_draft_messages(
            scene_brief=scene_brief,
            target_word_count=context.get("target_word_count", DEFAULT_TARGET_WORD_COUNT),
            previous_summaries=context.get("previous_summaries"),
            style_card=context.get("style_card"),
            character_cards=context.get("character_cards") or [],
            world_cards=context.get("world_cards") or [],
            facts=context.get("facts") or [],
            text_chunks=context.get("text_chunks") or [],
            working_memory=context.get("working_memory"),
            unresolved_gaps=context.get("unresolved_gaps") or [],
            timeline=context.get("timeline") or [],
            character_states=context.get("character_states") or [],
            chapter_goal=context.get("chapter_goal"),
            user_answers=context.get("user_answers") or [],
            user_feedback=context.get("user_feedback") or "",
            evidence_pack=context.get("evidence_pack"),
            include_plan=False,
        )

        async for chunk in self.call_llm_stream(messages):
            yield chunk

    async def _load_previous_summaries(self, project_id: str, current_chapter: str) -> List[str]:
        """加载前置章节摘要 - 从存储或构建"""
        context_package = await self.draft_storage.get_context_for_writing(project_id, current_chapter)
        return self._build_previous_summaries_from_context(context_package)

    def _build_previous_summaries_from_context(self, context_package: Dict[str, Any]) -> List[str]:
        """从结构化上下文包构建摘要块 - 支持多种摘要格式"""
        blocks: List[str] = []

        def add_block(items: List[Dict[str, Any]], fields: List[str]) -> None:
            """Helper: Add formatted summary block for each item"""
            for item in items:
                parts = [f"{item.get('chapter')}: {item.get('title')}"]
                for field in fields:
                    value = item.get(field)
                    if isinstance(value, list):
                        value = "\n".join([f"- {val}" for val in value]) or "-"
                    if value:
                        parts.append(f"{field}:\n{value}")
                blocks.append("\n".join(parts))

        add_block(context_package.get("full_facts", []), ["summary", "key_events", "open_loops"])
        add_block(context_package.get("summary_with_events", []), ["summary", "key_events"])
        add_block(context_package.get("summary_only", []), ["summary"])
        add_block(context_package.get("title_only", []), [])

        for volume in context_package.get("volume_summaries", []):
            parts = [f"{volume.get('volume_id')}: {volume.get('brief_summary')}"]
            key_themes = volume.get("key_themes") or []
            major_events = volume.get("major_events") or []
            if key_themes:
                parts.append("Key Themes:\n" + "\n".join([f"- {val}" for val in key_themes]))
            if major_events:
                parts.append("Major Events:\n" + "\n".join([f"- {val}" for val in major_events]))
            blocks.append("\n".join(parts))

        return blocks

    async def _generate_draft(
        self,
        scene_brief: Optional[SceneBrief],
        target_word_count: int,
        previous_summaries: List[str],
        style_card: Optional[StyleCard] = None,
        character_cards: List[Any] = None,
        world_cards: List[Any] = None,
        facts: List[Any] = None,
        text_chunks: List[Any] = None,
        working_memory: str = None,
        unresolved_gaps: List[Dict[str, Any]] = None,
        timeline: List[Any] = None,
        character_states: List[Any] = None,
        chapter_goal: str = None,
        user_answers: List[Dict[str, str]] = None,
        user_feedback: str = None,
        evidence_pack: Dict[str, Any] = None,
    ) -> str:
        """
        通过 LLM 生成草稿文本 - 核心生成逻辑

        Call LLM to generate draft text with all context combined.
        Extracts draft content from <draft> tags if present.

        Args:
            scene_brief: Scene brief for this chapter.
            target_word_count: Target word count for the draft.
            previous_summaries: List of previous chapter summaries for context.
            style_card: Optional style card for consistent writing style.
            character_cards: Character cards for reference.
            world_cards: World building cards for consistency.
            facts: Canon facts to maintain continuity.
            text_chunks: Related text excerpts.
            working_memory: Compact working memory state (replaces detailed cards).
            unresolved_gaps: Information gaps to handle carefully.
            timeline: Timeline context.
            character_states: Current character states/relationships.
            chapter_goal: Chapter objective.
            user_answers: Pre-writing questions answered by user.
            user_feedback: User feedback on draft.
            evidence_pack: Retrieved evidence items.

        Returns:
            Generated draft text (extracted from tags if present).
        """
        messages = self._build_draft_messages(
            scene_brief=scene_brief,
            target_word_count=target_word_count,
            previous_summaries=previous_summaries,
            style_card=style_card,
            character_cards=character_cards,
            world_cards=world_cards,
            facts=facts,
            text_chunks=text_chunks,
            working_memory=working_memory,
            unresolved_gaps=unresolved_gaps,
            timeline=timeline,
            character_states=character_states,
            chapter_goal=chapter_goal,
            user_answers=user_answers,
            user_feedback=user_feedback,
            evidence_pack=evidence_pack,
            include_plan=True,
        )

        raw_response = await self.call_llm(messages)
        draft_content = raw_response
        # Extract draft from <draft>...</draft> tags if present
        if "<draft>" in raw_response:
            start = raw_response.find("<draft>") + 7
            end = raw_response.find("</draft>")
            if end == -1:
                end = len(raw_response)
            draft_content = raw_response[start:end].strip()

        return draft_content

    def _build_draft_messages(
        self,
        scene_brief: Optional[SceneBrief],
        target_word_count: int,
        previous_summaries: List[str],
        style_card: Optional[StyleCard] = None,
        character_cards: List[Any] = None,
        world_cards: List[Any] = None,
        facts: List[Any] = None,
        text_chunks: List[Any] = None,
        working_memory: str = None,
        unresolved_gaps: List[Dict[str, Any]] = None,
        timeline: List[Any] = None,
        character_states: List[Any] = None,
        chapter_goal: str = None,
        user_answers: List[Dict[str, str]] = None,
        user_feedback: str = None,
        evidence_pack: Dict[str, Any] = None,
        include_plan: bool = True,
    ) -> List[Dict[str, str]]:
        """
        构建草稿生成的消息列表 - 带 token 预算感知

        Build messages for draft generation with token budget awareness.
        Context items are added by priority (high to low). Each section is
        budget-capped so the total stays within the model's input limit.

        Priority order (high → low):
          1. chapter_goal, scene_brief     (必选 / mandatory)
          2. working_memory                (核心上下文 / core context)
          3. unresolved_gaps               (安全约束 / safety constraint)
          4. style_card                    (文风一致性 / style consistency)
          5. text_chunks                   (原文片段 / source excerpts)
          6. evidence_pack                 (证据 / evidence)
          7. user_answers, user_feedback   (用户输入 / user input)
          8. character_cards, world_cards   (设定卡片 / setting cards)
          9. facts, character_states       (事实/状态 / facts/states)
         10. previous_summaries            (前章摘要 / chapter summaries, lowest)
        """
        # 获取输入 token 上限
        input_limit = self._get_input_token_limit()

        # 计算固定开销：系统提示词 + 用户指令
        brief_goal = _get_field(scene_brief, "goal", "")
        prompt = writer_draft_prompt(
            include_plan=include_plan,
            chapter_goal=chapter_goal or "",
            brief_goal=brief_goal or "",
            target_word_count=target_word_count,
            language=self.language,
        )
        fixed_tokens = count_tokens(prompt.system) + count_tokens(prompt.user) + 200

        # context 可用预算
        context_budget = max(0, input_limit - fixed_tokens)

        # 使用 ContextBudgetPacker 按优先级装填 context_items
        packer = _ContextBudgetPacker(context_budget)
        use_compact_context = bool(working_memory and str(working_memory).strip())

        # P1: 必选 — 章节目标 + 场景简要
        if chapter_goal:
            packer.add(
                "GOAL PRIORITY:\n- " + str(chapter_goal).strip() + "\n" "Only write content that serves the goal.",
                section="goal",
            )

        brief_text = self._format_scene_brief(scene_brief)
        packer.add(brief_text, section="scene_brief")

        # P2: 核心上下文 — working_memory
        if working_memory:
            packer.add("Working Memory:\n" + str(working_memory), section="working_memory")

        # P3: 安全约束 — 未解决缺口
        if unresolved_gaps:
            gap_text = self._format_unresolved_gaps(unresolved_gaps)
            if gap_text:
                packer.add(gap_text, section="gaps")

        # P4: 文风一致性 — 风格卡
        if style_card:
            try:
                packer.add("Style Card:\n" + str(style_card.model_dump()), section="style")
            except Exception:
                packer.add("Style Card:\n" + str(style_card), section="style")

        # P5: 原文片段
        if text_chunks:
            chunk_text = self._format_text_chunks(text_chunks)
            if chunk_text:
                packer.add(chunk_text, section="text_chunks")

        # P6: 证据包
        if evidence_pack and isinstance(evidence_pack, dict):
            evidence_text = self._format_evidence_pack(evidence_pack)
            if evidence_text:
                packer.add(evidence_text, section="evidence")

        # P7: 用户输入
        if user_answers:
            answers_text = self._format_user_answers(user_answers)
            if answers_text:
                packer.add(answers_text, section="user_answers")

        if user_feedback:
            packer.add("User Feedback:\n" + str(user_feedback), section="user_feedback")

        # P8: 设定卡片（仅在无 working_memory 时）
        _cl = app_cfg.get("writer", {}).get("context_limits", {})
        if not use_compact_context:
            if character_cards:
                cards_text = self._format_model_list(
                    "Character Cards:", character_cards[: int(_cl.get("character_cards", 10))]
                )
                packer.add(cards_text, section="character_cards")

            if world_cards:
                cards_text = self._format_model_list("World Cards:", world_cards[: int(_cl.get("world_cards", 10))])
                packer.add(cards_text, section="world_cards")

        # P9: 事实和状态（仅在无 working_memory 时）
        if not use_compact_context:
            if facts and not (evidence_pack and evidence_pack.get("items")):
                facts_text = self._format_model_list("Canon Facts:", facts[: int(_cl.get("facts", 20))])
                packer.add(facts_text, section="facts")

            if character_states:
                states_text = self._format_model_list(
                    "Character States:", character_states[: int(_cl.get("character_states", 20))]
                )
                packer.add(states_text, section="character_states")

        # P10: 前章摘要（最低优先级，最先被裁剪）
        if previous_summaries:
            packer.add(
                "Previous Chapters:\n" + "\n\n".join(previous_summaries),
                section="summaries",
            )

        if packer.dropped_sections:
            logger.warning(
                "Writer context budget exceeded: dropped sections %s " "(used %d / budget %d tokens)",
                packer.dropped_sections,
                packer.used_tokens,
                context_budget,
            )

        return self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=packer.items,
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_scene_brief(self, scene_brief: Optional[SceneBrief]) -> str:
        """Format scene brief into a single context block."""
        brief_chapter = _get_field(scene_brief, "chapter", "")
        brief_title = _get_field(scene_brief, "title", "")
        brief_goal = _get_field(scene_brief, "goal", "")
        brief_characters = _get_field(scene_brief, "characters", [])
        brief_timeline = _get_field(scene_brief, "timeline_context", {})
        brief_constraints = _get_field(scene_brief, "world_constraints", [])
        brief_style = _get_field(scene_brief, "style_reminder", "")
        brief_forbidden = _get_field(scene_brief, "forbidden", [])

        return f"""Scene Brief:
Chapter: {brief_chapter}
Title: {brief_title}
Goal: {brief_goal}

Characters:
{self._format_characters(brief_characters)}

Timeline Context:
{self._format_dict(brief_timeline)}

World Constraints:
{self._format_list(brief_constraints)}

Style Reminder: {brief_style}

FORBIDDEN:
{self._format_list(brief_forbidden)}
"""

    @staticmethod
    def _format_unresolved_gaps(unresolved_gaps: List[Dict[str, Any]]) -> str:
        lines = ["未解决缺口（不得编造，请用模糊化叙事绕过或省略）:"]
        _gap_limit = int(app_cfg.get("writer", {}).get("context_limits", {}).get("unresolved_gaps", 6))
        for gap in unresolved_gaps[:_gap_limit]:
            if not isinstance(gap, dict):
                continue
            text = str(gap.get("text") or "").strip()
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _format_text_chunks(text_chunks: List[Any]) -> str:
        lines = ["Text Chunks:"]
        for chunk in text_chunks[:6]:
            if isinstance(chunk, dict):
                chapter = chunk.get("chapter") or ""
                text = chunk.get("text") or ""
                prefix = f"[{chapter}] " if chapter else ""
                lines.append(prefix + text)
            else:
                lines.append(str(chunk))
        return "\n".join(lines)

    @staticmethod
    def _format_evidence_pack(evidence_pack: Dict[str, Any]) -> str:
        items = evidence_pack.get("items") or []
        if not items:
            return ""
        lines = ["Evidence Pack:"]
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            text = str(item.get("text") or item.get("statement") or "").strip()
            source = item.get("source") or {}
            chapter = str(source.get("chapter") or "").strip()
            prefix = f"[{item_type}]" if item_type else ""
            if chapter:
                prefix = f"{prefix}[{chapter}]" if prefix else f"[{chapter}]"
            line = f"{prefix} {text}".strip()
            if line:
                lines.append(line)
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _format_user_answers(user_answers: List[Dict[str, str]]) -> str:
        lines = ["User Answers:"]
        for answer in user_answers:
            if not isinstance(answer, dict):
                continue
            question = answer.get("question") or answer.get("text") or answer.get("type") or ""
            reply = answer.get("answer") or ""
            if question or reply:
                lines.append(f"- {question}: {reply}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _format_model_list(header: str, items: List[Any]) -> str:
        lines = [header]
        for item in items:
            try:
                lines.append(str(item.model_dump()))
            except Exception:
                lines.append(str(item))
        return "\n".join(lines)

    def _format_characters(self, characters: List[Dict]) -> str:
        if not characters:
            return "None specified"
        lines = []
        for char in characters:
            name = char.get("name", "Unknown")
            state = char.get("current_state", "Normal")
            traits = char.get("relevant_traits", "")
            lines.append(f"- {name}: {state} ({traits})")
        return "\n".join(lines)

    def _format_dict(self, data: Dict) -> str:
        if not data:
            return "None"
        return "\n".join([f"- {key}: {value}" for key, value in data.items()])

    def _format_list(self, items: List) -> str:
        if not items:
            return "None"
        return "\n".join([f"- {item}" for item in items])


class _ContextBudgetPacker:
    """
    按 token 预算装填 context items。

    Packs context items into a budget. When remaining budget is insufficient
    for the next item, that item (and its section name) is recorded as dropped.
    Items are added in caller-defined priority order.

    Attributes:
        budget: Total token budget for all context items.
        items: Successfully packed items.
        used_tokens: Tokens consumed so far.
        dropped_sections: Section names that were dropped due to budget.
    """

    __slots__ = ("budget", "items", "used_tokens", "dropped_sections")

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.items: List[str] = []
        self.used_tokens = 0
        self.dropped_sections: List[str] = []

    def add(self, text: str, section: str = "") -> bool:
        """
        尝试添加一个 context item。

        Try to add a context item within the remaining budget.
        Returns True if added, False if dropped.
        """
        if not text or not text.strip():
            return False

        tokens = estimate_tokens_fast(text)
        if self.used_tokens + tokens > self.budget:
            if section:
                self.dropped_sections.append(section)
            return False

        self.items.append(text)
        self.used_tokens += tokens
        return True
