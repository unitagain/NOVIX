# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  档案员智能体 - 管理事实表、生成场景简要和章节摘要。
  Archivist Agent responsible for canon management, scene brief generation, and chapter summaries.
"""

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.base import BaseAgent
from app.agents._fanfiction_mixin import FanfictionMixin
from app.agents._summary_mixin import SummaryMixin
from app.schemas.draft import SceneBrief
from app.schemas.card import StyleCard
from app.prompts import (
    get_archivist_system_prompt,
    archivist_style_profile_prompt,
)
from app.config import config
from app.utils.chapter_id import ChapterIDValidator, normalize_chapter_id
from app.utils.dynamic_ranges import get_chapter_window, get_previous_chapters_limit
from app.utils.logger import get_logger
from app.utils.stopwords import get_stopwords

logger = get_logger(__name__)


class ArchivistAgent(FanfictionMixin, SummaryMixin, BaseAgent):
    """
    档案员智能体 - 维护小说世界观和事实表

    Manages canonical facts, character profiles, and world-building information.
    Generates scene briefs that guide writing and detects new setting elements.
    Ensures all generated content aligns with established story canon.

    Attributes:
        MAX_CHARACTERS: Maximum characters to include in scene brief.
        MAX_WORLD_CONSTRAINTS: Maximum world constraints to include.
        MAX_FACTS: Maximum facts to include per chapter context.
    """

    _archivist_cfg = config.get("archivist", {})
    MAX_CHARACTERS = int(_archivist_cfg.get("max_characters", 5))
    MAX_WORLD_CONSTRAINTS = int(_archivist_cfg.get("max_world_constraints", 5))
    MAX_FACTS = int(_archivist_cfg.get("max_facts", 5))

    @staticmethod
    def _get_chapter_window(window_type: str, total_chapters: int = 0) -> int:
        """
        获取章节窗口大小 - 使用共享的动态范围计算器

        Get chapter window size using shared dynamic range calculator.
        Allows flexible history window based on project size.

        Args:
            window_type: Type of window ("fact", "summary", etc.).
            total_chapters: Total number of chapters in project (for context).

        Returns:
            Window size (number of chapters to include).
        """
        return get_chapter_window(window_type, total_chapters)

    async def extract_creative_memory(
        self,
        final_draft: str,
        user_feedback: str = "",
        chapter_summary: str = "",
    ) -> List[Dict[str, Any]]:
        """Phase 10 · 从定稿 / 作者反馈提炼**跨会话**创作软知识（偏好 / 进度 / 决策）。

        返回 [{slug, description, body, type}]；提取失败或无可记返回 []（best-effort，不阻断主流程）。
        作者偏好(preference)主要来自 user_feedback；进度(progress)/决策(decision)来自章节内容与摘要。
        与 canon 的边界：只记无法从故事事实表推导的「怎么写 / 作者喜欢什么 / 进行到哪」软知识。
        """
        feedback = str(user_feedback or "").strip()
        draft = str(final_draft or "").strip()
        summary = str(chapter_summary or "").strip()
        if not feedback and not draft and not summary:
            return []

        system = (
            "你是创作记忆维护助手。从【作者反馈】与【章节内容】中，提炼真正值得**跨会话长期记住**的要点："
            "preference=作者写作偏好/风格要求（多来自反馈）；progress=项目进度/已写到哪；"
            "decision=关键创作决策（人物走向、世界规则取舍等）。"
            "只记无法从故事事实表(canon)推导的软知识，不要记一次性的具体改动。"
        )
        user = (
            f"【作者反馈】\n{feedback or '（无）'}\n\n"
            f"【章节摘要】\n{summary or '（无）'}\n\n"
            f"【章节正文节选】\n{draft[:1800]}\n\n"
            "请输出 JSON 数组，每项含 "
            '{"slug": "英文短横线标识", "description": "一行中文摘要", "body": "简要说明", '
            '"type": "preference|progress|decision"}。'
            "最多 5 条；只提炼真正值得长期记住的，没有则返回 []。"
        )
        messages = self.build_messages(system_prompt=system, user_prompt=user, context_items=[])
        data, err, _ = await self.call_llm_json(messages, expected_type=list, config_agent="archivist")
        if err or not isinstance(data, list):
            return []

        out: List[Dict[str, Any]] = []
        for item in data[:5]:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            mem_type = str(item.get("type") or "preference").strip().lower()
            out.append(
                {
                    "slug": str(item.get("slug") or description[:24]).strip(),
                    "description": description,
                    "body": str(item.get("body") or "").strip(),
                    "type": mem_type,
                }
            )
        return out

    @property
    def STOPWORDS(self) -> set:
        """获取中文停用词集合 - 用于关键词提取"""
        return get_stopwords()

    # Regex patterns for fact quality analysis
    _SIMPLE_RELATION_FACT_RE = re.compile(
        r"^(.{1,12})是(.{1,16})的(母亲|父亲|儿子|女儿|哥哥|姐姐|弟弟|妹妹|妻子|丈夫|恋人|朋友|同学|老师|学生|主人|仆人)[。.!?？]*$"
    )
    _SIMPLE_RELATION_FACT_RE_EN = re.compile(
        r"^(.+?) is (.+?)'?s? (mother|father|son|daughter|brother|sister|wife|husband|lover|friend|classmate|teacher|student|master|servant)[.!?]*$",
        re.IGNORECASE,
    )
    # Keywords indicating high-value facts for ranking
    _FACT_DENSITY_HINTS = (
        "规则",
        "禁忌",
        "代价",
        "必须",
        "不允许",
        "禁止",
        "承诺",
        "约定",
        "隐瞒",
        "秘密",
        "交易",
        "交换",
        "契约",
        "决定",
        "发现",
        "暴露",
        "背叛",
        "威胁",
        "受伤",
        "病",
        "死亡",
        "失踪",
        "获得",
        "丢失",
        "准备",
        "购买",
        "居住",
        "搬",
        "上学",
        "教育",
        "监护",
        "占有",
        "依赖",
        "恐惧",
        "愧疚",
        "同情",
        "惆怅",
    )
    _FACT_DENSITY_HINTS_EN = (
        "rule",
        "taboo",
        "cost",
        "must",
        "forbidden",
        "promise",
        "agreement",
        "secret",
        "deal",
        "betrayal",
        "threat",
        "injured",
        "dead",
        "missing",
        "obtained",
        "lost",
        "fear",
        "guilt",
        "decided",
        "discovered",
        "revealed",
        "contract",
        "obligation",
        "cannot",
        "prohibited",
        "owns",
        "lives",
        "moved",
        "dependent",
        "responsible",
    )

    def _normalize_fact_statement(self, statement: str) -> str:
        """规范化事实陈述 - 用于去重"""
        text = str(statement or "").strip()
        text = re.sub(r"\s+", "", text)
        text = text.strip("。．.！!?？")
        return text

    def _is_simple_relation_fact(self, statement: str) -> bool:
        """检测是否为简单关系事实 - 仅包含亲属关系"""
        text = self._normalize_fact_statement(statement)
        if self.language == "en":
            return bool(self._SIMPLE_RELATION_FACT_RE_EN.match(text))
        return bool(self._SIMPLE_RELATION_FACT_RE.match(text))

    def _score_fact_statement(self, statement: str) -> float:
        """
        评分事实陈述 - 评估信息价值

        Score a fact statement based on content complexity and information density.
        Higher scores indicate more valuable/complex facts.

        Args:
            statement: Fact statement text.

        Returns:
            Score between 0.0 and 5.0+ indicating fact value.
        """
        text = str(statement or "").strip()
        if not text:
            return 0.0

        # 从 config.yaml 加载评分权重，支持运行时调整 / Load scoring weights from config
        fs = config.get("archivist", {}).get("fact_scoring", {})
        length_divisor = float(fs.get("length_divisor", 18))
        length_cap = float(fs.get("length_cap", 2.0))
        punctuation_bonus = float(fs.get("punctuation_bonus", 0.7))
        numeric_bonus = float(fs.get("numeric_bonus", 0.3))
        density_hint_bonus = float(fs.get("density_hint_bonus", 0.8))
        simple_relation_penalty = float(fs.get("simple_relation_penalty", 0.6))

        score = 0.0
        # Length-based scoring: longer statements tend to be more specific
        score += min(len(text) / length_divisor, length_cap)
        # Complexity indicators: punctuation marks suggest multiple clauses
        if any(p in text for p in ("，", "；", "：", "（", "）", "(", ")")):
            score += punctuation_bonus
        # Numeric data often indicates specific, verifiable facts
        if re.search(r"\d", text):
            score += numeric_bonus
        # Presence of density hints (rules, secrets, decisions, etc.)
        hints = self._FACT_DENSITY_HINTS_EN if self.language == "en" else self._FACT_DENSITY_HINTS
        if any(h in text.lower() for h in hints):
            score += density_hint_bonus
        # Penalize simple relation facts (lower information value)
        if self._is_simple_relation_fact(text):
            score -= simple_relation_penalty
        return score

    def _select_high_value_facts(
        self,
        candidates: List[Tuple[str, float]],
        existing_statements: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        选择高价值事实 - 去重、评分、排序

        Select highest-value facts from candidates, avoiding duplicates.
        Prioritizes complex facts over simple relations.

        Args:
            candidates: List of (statement, confidence) tuples.
            existing_statements: Statements to avoid duplicating.
            limit: Maximum facts to return.

        Returns:
            List of (statement, confidence) tuples, sorted by value.
        """
        existing_norm = {self._normalize_fact_statement(s) for s in (existing_statements or []) if str(s or "").strip()}

        uniq: List[Tuple[str, float]] = []
        seen = set(existing_norm)
        for raw_statement, confidence in candidates or []:
            statement = str(raw_statement or "").strip()
            if not statement:
                continue
            normalized = self._normalize_fact_statement(statement)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            uniq.append((statement, float(confidence)))

        scored = [
            {
                "statement": statement,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "score": self._score_fact_statement(statement),
                "simple_relation": self._is_simple_relation_fact(statement),
            }
            for statement, confidence in uniq
            if len(str(statement or "").strip()) >= 6
        ]
        scored.sort(key=lambda x: (-x["score"], -len(x["statement"]), x["statement"]))

        primary = [item for item in scored if not item["simple_relation"]]
        secondary = [item for item in scored if item["simple_relation"]]

        selected: List[Dict[str, Any]] = []
        for item in primary:
            selected.append(item)
            if len(selected) >= int(limit):
                break

        if len(selected) < int(limit):
            max_rel = 1 if any(not s["simple_relation"] for s in selected) else int(limit)
            rel_used = 0
            for item in secondary:
                if rel_used >= max_rel:
                    break
                selected.append(item)
                rel_used += 1
                if len(selected) >= int(limit):
                    break

        return [(item["statement"], item["confidence"]) for item in selected[: int(limit)]]

    def get_agent_name(self) -> str:
        """获取智能体标识 - 返回 'archivist'"""
        return "archivist"

    def get_system_prompt(self) -> str:
        """获取系统提示词 - 档案员专用"""
        return get_archivist_system_prompt(language=self.language)

    async def execute(self, project_id: str, chapter: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行档案员任务 - 生成场景简要

        Main entry point for scene brief generation. Collects all relevant context
        (facts, characters, world-building, timeline) and generates a structured
        scene brief to guide the writer.

        Args:
            project_id: Project identifier.
            chapter: Chapter identifier.
            context: Context dict with chapter_title, chapter_goal, characters, etc.

        Returns:
            Dict with success status, scene_brief object, and conflicts list.
        """
        style_card = await self.card_storage.get_style_card(project_id)

        chapter_id = normalize_chapter_id(chapter) or chapter
        chapter_title = context.get("chapter_title", "")
        chapter_goal = context.get("chapter_goal", "")

        instruction_text = " ".join([chapter_title, chapter_goal])
        instruction_characters = await self._extract_mentions_from_texts(
            project_id=project_id,
            texts=[instruction_text],
            card_type="character",
        )
        instruction_worlds = await self._extract_mentions_from_texts(
            project_id=project_id,
            texts=[instruction_text],
            card_type="world",
        )

        # ============================================================================
        # Calculate dynamic chapter windows / 计算动态章节窗口
        # ============================================================================
        # Get total chapters for dynamic range calculation
        all_chapters = await self.draft_storage.list_chapters(project_id)
        total_chapters = len(all_chapters)
        dynamic_limit = get_previous_chapters_limit(total_chapters)

        recent_chapters = await self._get_previous_chapters(project_id, chapter_id, limit=dynamic_limit)
        fact_window = self._get_chapter_window("fact", len(recent_chapters))
        recent_fact_chapters = recent_chapters[-fact_window:] if fact_window < len(recent_chapters) else recent_chapters
        summary_chapters = recent_chapters[:-fact_window] if fact_window < len(recent_chapters) else []

        try:
            from app.services.chapter_binding_service import chapter_binding_service

            seed_names = list(dict.fromkeys([*instruction_characters, *instruction_worlds]))
            bound_chapters = await chapter_binding_service.get_chapters_for_entities(
                project_id,
                seed_names,
                limit=4,
            )
            if bound_chapters:
                recent_fact_chapters = bound_chapters
        except Exception as exc:
            logger.debug("Chapter binding lookup failed, using recent chapters: %s", exc)

        # ============================================================================
        # Build context blocks / 构建上下文块
        # ============================================================================
        summary_blocks = await self._build_summary_blocks(project_id, summary_chapters)
        timeline_events = await self.canon_storage.get_timeline_events_near_chapter(
            project_id=project_id,
            chapter=chapter_id,
            window=3,
            max_events=10,
        )

        keywords = self._extract_keywords(" ".join([instruction_text] + summary_blocks))

        chapter_texts = await self._load_chapter_texts(project_id, recent_fact_chapters)
        mentioned_characters = await self._extract_mentions_from_texts(
            project_id=project_id,
            texts=chapter_texts,
            card_type="character",
        )
        mentioned_worlds = await self._extract_mentions_from_texts(
            project_id=project_id,
            texts=chapter_texts,
            card_type="world",
        )

        character_names = context.get("characters", []) or []
        selected_character_names = self._merge_unique(
            mentioned_characters,
            instruction_characters,
            character_names,
        )
        characters = await self._build_character_context(project_id, selected_character_names)

        world_names = self._merge_unique(mentioned_worlds, instruction_worlds)
        world_constraints = await self._build_world_constraints_from_names(project_id, world_names)

        facts = await self._collect_facts_for_chapters(project_id, recent_fact_chapters)
        extra_facts = await self._select_facts_by_instruction(
            project_id=project_id,
            keywords=keywords,
            exclude_ids={fact.get("id") for fact in facts if fact.get("id")},
            max_extra=5,
        )
        facts.extend(extra_facts)

        style_reminder = self._build_style_reminder(style_card)
        timeline_context = self._build_timeline_context_from_summaries(
            chapter_goal=chapter_goal,
            summaries=summary_blocks,
            fallback_events=timeline_events,
        )

        scene_brief = SceneBrief(
            chapter=chapter_id,
            title=chapter_title or f"Chapter {chapter_id}",
            goal=chapter_goal,
            characters=characters,
            timeline_context=timeline_context,
            world_constraints=world_constraints,
            facts=[fact.get("statement") for fact in facts if fact.get("statement")],
            style_reminder=style_reminder,
            forbidden=[],
        )

        await self.draft_storage.save_scene_brief(project_id, chapter_id, scene_brief)

        return {
            "success": True,
            "scene_brief": scene_brief,
            "conflicts": [],
        }

    async def _build_character_context(self, project_id: str, names: List[str]) -> List[Dict[str, str]]:
        characters = []
        for name in names:
            card = await self.card_storage.get_character_card(project_id, name)
            if not card:
                continue
            traits = card.description
            characters.append(
                {
                    "name": card.name,
                    "relevant_traits": traits,
                }
            )
        return characters

    def _build_timeline_context_from_summaries(
        self,
        chapter_goal: str,
        summaries: List[str],
        fallback_events: List[Any],
    ) -> Dict[str, str]:
        before = summaries[-1] if summaries else ""
        if not before and fallback_events:
            event = fallback_events[-1]
            before = f"{event.time}: {event.event} @ {event.location}"
        return {
            "before": before,
            "current": chapter_goal,
            "after": "",
        }

    def _extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        candidates = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)
        keywords = []
        for token in candidates:
            if token in self.STOPWORDS:
                continue
            if token not in keywords:
                keywords.append(token)
        return keywords

    def _score_text_match(self, text: str, keywords: List[str]) -> int:
        if not text or not keywords:
            return 0
        score = 0
        for kw in keywords:
            if kw and kw in text:
                score += 1
        return score

    async def _get_previous_chapters(
        self,
        project_id: str,
        current_chapter: str,
        limit: int,
    ) -> List[str]:
        limit = max(int(limit or 0), 0)
        if limit <= 0:
            return []

        chapters = await self.draft_storage.list_chapters(project_id)
        if not chapters:
            return []

        canonical_current = str(current_chapter or "").strip()
        if canonical_current in chapters:
            index = chapters.index(canonical_current)
            return chapters[max(0, index - limit) : index]

        # 当前章节尚未创建：退化为权重比较，但保持 chapters 的既有顺序（包含自定义排序）。
        try:
            current_weight = ChapterIDValidator.calculate_weight(canonical_current)
        except Exception:
            return chapters[max(0, len(chapters) - limit) :]
        if current_weight <= 0:
            return chapters[max(0, len(chapters) - limit) :]
        previous = [ch for ch in chapters if ChapterIDValidator.calculate_weight(ch) < current_weight]
        return previous[max(0, len(previous) - limit) :]

    async def _build_summary_blocks(self, project_id: str, chapters: List[str]) -> List[str]:
        blocks: List[str] = []
        for ch in chapters:
            summary = await self.draft_storage.get_chapter_summary(project_id, ch)
            if not summary:
                continue
            title = summary.title or ch
            brief = summary.brief_summary or ""
            blocks.append(f"{ch}: {title}\n{brief}".strip())
        return blocks

    async def _load_chapter_texts(self, project_id: str, chapters: List[str]) -> List[str]:
        # 限制并发数，避免大量章节同时打开文件句柄
        # Limit concurrency to prevent too many open file handles.
        sem = asyncio.Semaphore(20)

        async def _load_one(ch: str) -> str:
            async with sem:
                final = await self.draft_storage.get_final_draft(project_id, ch)
                if final:
                    return final
                versions = await self.draft_storage.list_draft_versions(project_id, ch)
                if not versions:
                    return ""
                draft = await self.draft_storage.get_draft(project_id, ch, versions[-1])
                return draft.content if draft else ""

        return list(await asyncio.gather(*[_load_one(ch) for ch in chapters]))

    async def _extract_mentions_from_texts(
        self,
        project_id: str,
        texts: List[str],
        card_type: str,
    ) -> List[str]:
        names = []
        if card_type == "character":
            names = await self.card_storage.list_character_cards(project_id)
        elif card_type == "world":
            names = await self.card_storage.list_world_cards(project_id)
        if not names:
            return []

        mentioned = []
        for name in names:
            for text in texts:
                if name and text and name in text:
                    mentioned.append(name)
                    break
        return mentioned

    def _merge_unique(self, *groups: List[str]) -> List[str]:
        merged: List[str] = []
        for group in groups:
            for name in group or []:
                if name and name not in merged:
                    merged.append(name)
        return merged

    async def _build_world_constraints_from_names(
        self,
        project_id: str,
        names: List[str],
    ) -> List[str]:
        constraints = []
        for name in names:
            card = await self.card_storage.get_world_card(project_id, name)
            if not card:
                continue
            description = card.description or ""
            constraints.append(f"{card.name}: {description}".strip())
        return constraints

    async def _collect_facts_for_chapters(
        self,
        project_id: str,
        chapters: List[str],
    ) -> List[Dict[str, Any]]:
        if not chapters:
            return []
        chapter_set = {normalize_chapter_id(ch) for ch in chapters if ch}
        facts = await self.canon_storage.get_eligible_facts_raw(project_id)
        selected = []
        for fact in facts:
            raw_chapter = fact.get("introduced_in") or fact.get("source") or ""
            fact_chapter = normalize_chapter_id(raw_chapter)
            if fact_chapter in chapter_set:
                selected.append(fact)
        return selected

    async def _select_facts_by_instruction(
        self,
        project_id: str,
        keywords: List[str],
        exclude_ids: set,
        max_extra: int,
    ) -> List[Dict[str, Any]]:
        if not keywords:
            return []
        facts = await self.canon_storage.get_eligible_facts_raw(project_id)
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for fact in facts:
            if fact.get("id") in exclude_ids:
                continue
            statement = str(fact.get("statement") or fact.get("content") or "")
            score = self._score_text_match(statement, keywords)
            if score > 0:
                scored.append((score, fact))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [fact for _, fact in scored[:max_extra]]

    def _build_style_reminder(self, style_card: Optional[StyleCard]) -> str:
        if not style_card:
            return ""
        style_text = getattr(style_card, "style", "") or ""
        return style_text.strip()

    def _sample_text_for_style_profile(self, sample_text: str, max_chars: int = 20000) -> str:
        """
        采样文风提炼用的文本片段。

        目的：
        - 避免超长正文导致中段信息被截断
        - 让文风提炼同时“看到”开头/中段/结尾，提升稳定性
        """
        text = str(sample_text or "").strip()
        if not text:
            return ""
        if max_chars <= 0 or len(text) <= max_chars:
            return text

        head_len = int(max_chars * 0.35)
        tail_len = int(max_chars * 0.35)
        mid_len = max_chars - head_len - tail_len

        head = text[:head_len]
        tail = text[-tail_len:] if tail_len > 0 else ""

        mid_start = max(0, (len(text) // 2) - (mid_len // 2))
        mid = text[mid_start : mid_start + mid_len] if mid_len > 0 else ""

        parts = [p for p in [head, mid, tail] if p]
        return "\n\n……\n\n".join(parts)

    async def extract_style_profile(self, sample_text: str) -> str:
        """Extract writing style guidance from sample text."""
        sampled = self._sample_text_for_style_profile(sample_text, max_chars=20000)
        prompt = archivist_style_profile_prompt(sample_text=sampled, language=self.language)
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )
        response = await self.call_llm(messages)
        return str(response or "").strip()
