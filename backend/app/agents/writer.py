"""
Writer Agent / 撰稿人
Generates draft based on scene brief
根据场景简报生成草稿
"""

from typing import Dict, Any, List
from app.agents.base import BaseAgent


class WriterAgent(BaseAgent):
    """
    Writer agent responsible for generating drafts
    撰稿人，负责生成草稿
    """
    
    def get_agent_name(self) -> str:
        """Get agent name / 获取 Agent 名称"""
        return "writer"
    
    def get_system_prompt(self) -> str:
        """Get system prompt / 获取系统提示词"""
        return """You are a Writer agent for novel writing.

Your responsibilities:
1. Write draft based on scene brief
2. Follow style guidelines strictly
3. DO NOT invent new settings - mark anything uncertain as [TO_CONFIRM]
4. Respect character boundaries and timeline constraints

Core principle:
- Chapter goal comes first. Use cards/canon as constraints and a reference, not a checklist.
- Do NOT try to mention or use every card in every chapter. Only apply what is relevant to achieve the goal.
- Before writing, form a brief internal plan (3-6 beats) to reach the chapter goal, then write the prose.

Output Format:
- Write engaging narrative prose
- Mark uncertain details with [TO_CONFIRM: detail]
- Include word count at the end
- List any pending confirmations

你是一个小说撰稿人。

职责：
1. 根据场景简报撰写草稿
2. 严格遵循文风指南
3. 不得自行发明新设定 - 将不确定的内容标记为 [TO_CONFIRM]
4. 尊重角色边界和时间线约束

核心原则：
- 章节目标优先。卡片/事实表是约束与参考资料，不是每章必须覆盖的清单。
- 不要试图在一章里提到或使用所有卡片，只在与本章目标相关时才引用。
- 写作前先在脑中形成 3-6 个“推进节点/场景节拍”的简短规划，然后再写正文。

输出格式：
- 撰写引人入胜的叙事散文
- 用 [TO_CONFIRM: detail] 标记不确定的细节
- 在末尾包含字数统计
- 列出任何待确认事项"""
    
    async def execute(
        self,
        project_id: str,
        chapter: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate draft for a chapter
        为章节生成草稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            context: Context with scene_brief, target_word_count / 包含场景简报和目标字数的上下文
            
        Returns:
            Result with draft content / 包含草稿内容的结果
        """
        # Load scene brief / 加载场景简报
        scene_brief = context.get("scene_brief")
        if not scene_brief:
            scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        
        if not scene_brief:
            return {
                "success": False,
                "error": "Scene brief not found"
            }
        
        # Load previous summaries for context / 加载前文摘要作为上下文
        previous_summaries = await self._load_previous_summaries(project_id, chapter)

        style_card = context.get("style_card")
        rules_card = context.get("rules_card")
        character_cards = context.get("character_cards") or []
        world_cards = context.get("world_cards") or []
        facts = context.get("facts") or []
        timeline = context.get("timeline") or []
        character_states = context.get("character_states") or []
        chapter_goal = context.get("chapter_goal")
        
        # Generate draft / 生成草稿
        draft_content = await self._generate_draft(
            scene_brief=scene_brief,
            target_word_count=context.get("target_word_count", 3000),
            previous_summaries=previous_summaries,
            style_card=style_card,
            rules_card=rules_card,
            character_cards=character_cards,
            world_cards=world_cards,
            facts=facts,
            timeline=timeline,
            character_states=character_states,
            chapter_goal=chapter_goal,
        )
        
        # Extract pending confirmations / 提取待确认事项
        pending_confirmations = self._extract_confirmations(draft_content)
        
        # Calculate word count / 计算字数
        word_count = len(draft_content)
        
        # Save draft / 保存草稿
        draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version="v1",
            content=draft_content,
            word_count=word_count,
            pending_confirmations=pending_confirmations
        )
        
        return {
            "success": True,
            "draft": draft,
            "word_count": word_count,
            "pending_confirmations": pending_confirmations
        }
    
    async def _load_previous_summaries(
        self,
        project_id: str,
        current_chapter: str
    ) -> List[str]:
        """
        Load summaries of previous chapters
        加载前面章节的摘要
        
        Args:
            project_id: Project ID / 项目ID
            current_chapter: Current chapter ID / 当前章节ID
            
        Returns:
            List of summary texts / 摘要文本列表
        """
        # Delegate to DraftStorage distance-tiered selection (MVP-2 Week 6)
        # 委托给 DraftStorage 的按距离分级选取逻辑（MVP-2 第6周）
        return await self.draft_storage.select_previous_summaries(
            project_id=project_id,
            current_chapter=current_chapter,
        )
    
    async def _generate_draft(
        self,
        scene_brief: Any,
        target_word_count: int,
        previous_summaries: List[str],
        style_card: Any = None,
        rules_card: Any = None,
        character_cards: List[Any] = None,
        world_cards: List[Any] = None,
        facts: List[Any] = None,
        timeline: List[Any] = None,
        character_states: List[Any] = None,
        chapter_goal: str = None,
    ) -> str:
        """
        Generate draft using LLM
        使用大模型生成草稿
        
        Args:
            scene_brief: Scene brief object / 场景简报对象
            target_word_count: Target word count / 目标字数
            previous_summaries: Previous chapter summaries / 前文摘要
            
        Returns:
            Generated draft content / 生成的草稿内容
        """
        # Build context / 构建上下文
        context_items = []

        if chapter_goal:
            context_items.append(
                """GOAL PRIORITY (must follow):
Primary objective of this chapter:
- """ + str(chapter_goal).strip() + """

Write only what serves this goal. Do not force unrelated cards/facts into the chapter.
章节目标优先（必须遵循）：
- """ + str(chapter_goal).strip() + """

只写服务于该目标的内容，不要为了覆盖设定卡而硬塞无关信息。"""
            )
        
        # Add scene brief / 添加场景简报
        brief_text = f"""Scene Brief:
Chapter: {scene_brief.chapter}
Title: {scene_brief.title}
Goal: {scene_brief.goal}

Characters:
{self._format_characters(scene_brief.characters)}

Timeline Context:
{self._format_dict(scene_brief.timeline_context)}

World Constraints:
{self._format_list(scene_brief.world_constraints)}

Style Reminder: {scene_brief.style_reminder}

FORBIDDEN:
{self._format_list(scene_brief.forbidden)}"""
        context_items.append(brief_text)

        if style_card:
            try:
                context_items.append("Style Card:\n" + str(style_card.model_dump()))
            except Exception:
                context_items.append("Style Card:\n" + str(style_card))

        if rules_card:
            try:
                context_items.append("Rules Card:\n" + str(rules_card.model_dump()))
            except Exception:
                context_items.append("Rules Card:\n" + str(rules_card))

        if character_cards:
            lines = ["Character Cards:"]
            for c in character_cards[:10]:
                try:
                    lines.append(str(c.model_dump()))
                except Exception:
                    lines.append(str(c))
            context_items.append("\n".join(lines))

        if world_cards:
            lines = ["World Cards:"]
            for w in world_cards[:10]:
                try:
                    lines.append(str(w.model_dump()))
                except Exception:
                    lines.append(str(w))
            context_items.append("\n".join(lines))

        if facts:
            lines = ["Canon Facts:"]
            for f in facts[-20:]:
                try:
                    lines.append(str(f.model_dump()))
                except Exception:
                    lines.append(str(f))
            context_items.append("\n".join(lines))

        if timeline:
            lines = ["Canon Timeline:"]
            for t in timeline[-20:]:
                try:
                    lines.append(str(t.model_dump()))
                except Exception:
                    lines.append(str(t))
            context_items.append("\n".join(lines))

        if character_states:
            lines = ["Character States:"]
            for s in character_states[:20]:
                try:
                    lines.append(str(s.model_dump()))
                except Exception:
                    lines.append(str(s))
            context_items.append("\n".join(lines))
        
        # Add previous summaries / 添加前文摘要
        if previous_summaries:
            context_items.append("Previous Chapters:\n" + "\n\n".join(previous_summaries))
        
        # Build user prompt / 构建用户提示
        user_prompt = f"""Write a draft for this chapter.

Chapter goal (top priority): {chapter_goal or scene_brief.goal}

Requirements:
- Target word count: approximately {target_word_count} words (Chinese characters count as words)
- Follow the style reminder strictly
- Respect all constraints and forbidden actions
- DO NOT invent new settings without marking them [TO_CONFIRM: detail]
- Keep character behaviors consistent with their cards
- If scene brief conflicts with cards/canon, follow cards/canon
- Focus on accomplishing the chapter goal; do not force coverage of unrelated cards/facts
- Advance the plot toward the chapter goal

Write the draft in narrative prose. Be engaging and vivid.

要求：
- 目标字数：约 {target_word_count} 字
- 严格遵循文风提醒
- 尊重所有约束和禁区
- 不要自行发明新设定，如需新设定请标记 [TO_CONFIRM: detail]
- 保持角色行为与角色卡一致
- 若场景简报与卡片/事实表冲突，以卡片/事实表为准
- 聚焦完成章节目标，不要为了“覆盖设定卡”而硬塞无关信息
- 推进情节朝着章节目标发展

用叙事散文撰写草稿，要引人入胜、生动形象。"""
        
        # Call LLM / 调用大模型
        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=context_items
        )
        
        return await self.call_llm(messages)
    
    def _format_characters(self, characters: List[Dict]) -> str:
        """Format characters for display / 格式化角色信息"""
        if not characters:
            return "None specified"
        lines = []
        for char in characters:
            name = char.get("name", "Unknown")
            state = char.get("current_state", "Normal")
            traits = char.get("relevant_traits", "")
            lines.append(f"- {name}: {state} ({traits})")
        return "\n".join(lines)
    
    def _format_dict(self, d: Dict) -> str:
        """Format dictionary for display / 格式化字典"""
        if not d:
            return "None"
        return "\n".join([f"- {k}: {v}" for k, v in d.items()])
    
    def _format_list(self, lst: List) -> str:
        """Format list for display / 格式化列表"""
        if not lst:
            return "None"
        return "\n".join([f"- {item}" for item in lst])
    
    def _extract_confirmations(self, content: str) -> List[str]:
        """
        Extract pending confirmations from content
        从内容中提取待确认事项
        
        Args:
            content: Draft content / 草稿内容
            
        Returns:
            List of pending confirmations / 待确认事项列表
        """
        confirmations = []
        lines = content.split("\n")
        for line in lines:
            if "[TO_CONFIRM:" in line:
                start = line.find("[TO_CONFIRM:") + 12
                end = line.find("]", start)
                if end > start:
                    confirmations.append(line[start:end].strip())
        return confirmations
