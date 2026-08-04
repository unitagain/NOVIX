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

import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.base import BaseAgent
from app.agents._fanfiction_mixin import FanfictionMixin
from app.agents._summary_mixin import SummaryMixin
from app.prompts import (
    get_archivist_system_prompt,
    archivist_style_profile_prompt,
)
from app.config import config
from app.utils.dynamic_ranges import get_chapter_window
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
        data, err, _ = await self.call_llm_json(messages, expected_type=list, config_agent="writer")
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
        """Internal analysis helpers share the single Writer model binding."""
        return "writer"

    def get_system_prompt(self) -> str:
        """获取系统提示词 - 档案员专用"""
        return get_archivist_system_prompt(language=self.language)

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
