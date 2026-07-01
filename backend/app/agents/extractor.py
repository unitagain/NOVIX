# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  提取器智能体 - 从 Wiki 文本转换为结构化卡片提议。
  Extractor Agent converts Wiki/Fandom content into structured Card Proposal objects.
"""

from typing import Dict, Any, List
from app.agents.base import BaseAgent
from app.prompts import EXTRACTOR_SYSTEM_PROMPT, extractor_cards_prompt
from app.schemas.draft import CardProposal
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ExtractorAgent(BaseAgent):
    """
    提取器智能体 - 从 Wiki 内容提取卡片

    Agent responsible for converting Wiki/Fandom content into structured
    Card Proposal objects for character and world-building cards.
    Used for fanfiction/同人创作 features to bootstrap project data from
    external sources (Wikipedia, Fandom, etc.).
    """

    def get_agent_name(self) -> str:
        """获取智能体标识 - 返回 'extractor'"""
        return "extractor"

    async def execute(self, project_id: str, chapter: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行提取任务 - extract_cards 的包装器

        Main entry point for card extraction from Wiki content.

        Args:
            project_id: Project identifier (for future multi-project support).
            chapter: Unused for this agent (kept for interface consistency).
            context: Dict with title, content, max_cards keys.

        Returns:
            Dict with "proposals" list of CardProposal objects.
        """
        title = context.get("title", "Unknown")
        content = context.get("content", "")
        max_cards = context.get("max_cards", 20)

        proposals = await self.extract_cards(title, content, max_cards)
        return {"proposals": proposals}

    def get_system_prompt(self) -> str:
        """获取系统提示词 - 提取器专用"""
        return EXTRACTOR_SYSTEM_PROMPT

    async def extract_cards(self, title: str, content: str, max_cards: int = 20) -> List[CardProposal]:
        """
        从 Wiki 内容提取卡片提议 - 核心提取逻辑

        Extract card proposals (characters, locations, events) from Wiki content.
        Uses LLM to intelligently identify key entities and their attributes.

        Args:
            title: Title of the Wiki page.
            content: Full text content of the Wiki page.
            max_cards: Maximum number of card proposals to extract.

        Returns:
            List of CardProposal objects with name, type, description, confidence, etc.
        """
        prompt = extractor_cards_prompt(title=title, content=content, max_cards=max_cards)

        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
        )

        proposals = []
        # Parse JSON response containing card proposals (Phase 9: 结构化输出 response_format + 成功率指标)
        data, err, _ = await self.call_llm_json(messages, expected_type=list)
        if err:
            logger.warning("Extractor parse failed: %s", err)
            return proposals

        # Build CardProposal objects from response data
        for item in data:
            if not isinstance(item, dict):
                continue
            if not item.get("name") or not item.get("type"):
                continue
            # Normalize confidence to 0.0-1.0 range
            try:
                confidence = float(item.get("confidence", 0.8))
            except Exception:
                confidence = 0.8
            item["confidence"] = max(0.0, min(1.0, confidence))
            proposals.append(CardProposal(**item))

        return proposals
