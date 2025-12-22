"""
Reviewer Agent / 审稿人
Reviews drafts and identifies issues
审核草稿并识别问题
"""

import yaml
from typing import Dict, Any, List
from app.agents.base import BaseAgent
from app.schemas.draft import ReviewResult, Issue


class ReviewerAgent(BaseAgent):
    """
    Reviewer agent responsible for reviewing drafts
    审稿人，负责审核草稿
    """
    
    def get_agent_name(self) -> str:
        """Get agent name / 获取 Agent 名称"""
        return "reviewer"
    
    def get_system_prompt(self) -> str:
        """Get system prompt / 获取系统提示词"""
        return """You are a Reviewer agent for novel writing.

Your responsibilities:
1. Review drafts for logical consistency
2. Check character behavior against character cards
3. Verify facts against canon (facts, timeline)
4. Check adherence to style guidelines
5. Identify forbidden action violations

Issue Severity Levels:
- critical: Must fix (contradicts canon, violates boundaries)
- moderate: Should fix (style deviation, minor inconsistency)
- minor: Optional fix (enhancement suggestion)

Output Format: YAML with structured issues list

你是一个小说审稿人。

职责：
1. 审核草稿的逻辑一致性
2. 检查角色行为是否符合角色卡
3. 验证事实是否与事实表一致
4. 检查是否遵循文风指南
5. 识别违反禁区的行为

问题严重程度：
- critical（严重）：必须修复（与设定矛盾、违反边界）
- moderate（中等）：应当修复（文风偏离、轻微不一致）
- minor（轻微）：可选修复（改进建议）

输出格式：YAML 格式的结构化问题列表"""
    
    async def execute(
        self,
        project_id: str,
        chapter: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Review draft for a chapter
        审核章节草稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            context: Context with draft_version / 包含草稿版本的上下文
            
        Returns:
            Result with review / 包含审稿结果的结果
        """
        # Load draft / 加载草稿
        draft_version = context.get("draft_version", "v1")
        draft = await self.draft_storage.get_draft(project_id, chapter, draft_version)
        
        if not draft:
            return {
                "success": False,
                "error": f"Draft {draft_version} not found"
            }
        
        # Load scene brief / 加载场景简报
        scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        
        # Load relevant cards / 加载相关卡片
        style_card = await self.card_storage.get_style_card(project_id)
        
        # Load character cards mentioned in scene brief / 加载场景简报中提到的角色卡
        character_cards = []
        if scene_brief:
            for char_info in scene_brief.characters:
                char_name = char_info.get("name")
                if char_name:
                    card = await self.card_storage.get_character_card(project_id, char_name)
                    if card:
                        character_cards.append(card)
        
        # Load facts and timeline / 加载事实和时间线
        facts = await self.canon_storage.get_all_facts(project_id)
        timeline_events = await self.canon_storage.get_all_timeline_events(project_id)
        
        # Generate review / 生成审稿意见
        review_content = await self._generate_review(
            draft=draft,
            scene_brief=scene_brief,
            character_cards=character_cards,
            facts=facts,
            timeline_events=timeline_events,
            style_card=style_card
        )
        
        # Parse review / 解析审稿意见
        review_result = self._parse_review(review_content, chapter, draft_version)
        
        # Save review / 保存审稿意见
        await self.draft_storage.save_review(project_id, chapter, review_result)
        
        return {
            "success": True,
            "review": review_result,
            "can_proceed": review_result.can_proceed
        }
    
    async def _generate_review(
        self,
        draft: Any,
        scene_brief: Any,
        character_cards: List[Any],
        facts: List[Any],
        timeline_events: List[Any],
        style_card: Any
    ) -> str:
        """
        Generate review using LLM
        使用大模型生成审稿意见
        
        Args:
            draft: Draft object / 草稿对象
            scene_brief: Scene brief / 场景简报
            character_cards: Character cards / 角色卡列表
            facts: Facts list / 事实列表
            timeline_events: Timeline events / 时间线事件列表
            style_card: Style card / 文风卡
            
        Returns:
            Generated review in YAML format / YAML格式的审稿意见
        """
        # Build context / 构建上下文
        context_items = []
        
        # Add draft content / 添加草稿内容
        context_items.append(f"Draft Content:\n{draft.content}")
        
        # Add scene brief / 添加场景简报
        if scene_brief:
            context_items.append(f"""Scene Brief:
Goal: {scene_brief.goal}
Forbidden: {', '.join(scene_brief.forbidden)}
Style Reminder: {scene_brief.style_reminder}""")
        
        # Add character boundaries / 添加角色边界
        if character_cards:
            char_info = ["Character Boundaries:"]
            for card in character_cards:
                char_info.append(f"\n{card.name}:")
                char_info.append(f"  Boundaries: {', '.join(card.boundaries)}")
                char_info.append(f"  Personality: {', '.join(card.personality)}")
            context_items.append("".join(char_info))
        
        # Add recent facts / 添加最近的事实
        if facts:
            recent_facts = facts[-10:]  # Last 10 facts / 最近10条
            facts_info = ["Recent Facts:"]
            for fact in recent_facts:
                facts_info.append(f"\n- {fact.statement}")
            context_items.append("".join(facts_info))
        
        # Add style requirements / 添加文风要求
        if style_card:
            context_items.append(f"""Style Requirements:
- Pacing: {style_card.pacing}
- Sentence Structure: {style_card.sentence_structure}""")
        
        # Build user prompt / 构建用户提示
        user_prompt = """Review this draft and identify issues.

Check for:
1. Logical consistency and plot holes
2. Character behavior consistency with character cards
3. Fact contradictions with established canon
4. Style guideline adherence
5. Forbidden action violations

Output in YAML format:
```yaml
issues:
  - severity: critical|moderate|minor
    category: logic|character|fact|style|forbidden
    location: "paragraph X" or "line Y"
    problem: "description of the problem"
    suggestion: "how to fix it"

overall_assessment: "brief overall evaluation"
can_proceed: true|false
```

Be thorough but concise. Focus on real issues, not nitpicks.

审核这篇草稿并识别问题。

检查项：
1. 逻辑一致性和情节漏洞
2. 角色行为是否与角色卡一致
3. 事实是否与已有设定矛盾
4. 是否遵循文风指南
5. 是否违反禁区

以 YAML 格式输出，要全面但简洁，关注真正的问题。"""
        
        # Call LLM / 调用大模型
        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=context_items
        )
        
        return await self.call_llm(messages)
    
    def _parse_review(
        self,
        yaml_content: str,
        chapter: str,
        draft_version: str
    ) -> ReviewResult:
        """
        Parse YAML review content
        解析 YAML 审稿内容
        
        Args:
            yaml_content: YAML string / YAML字符串
            chapter: Chapter ID / 章节ID
            draft_version: Draft version / 草稿版本
            
        Returns:
            ReviewResult object / ReviewResult 对象
        """
        try:
            # Extract YAML from response / 从响应中提取 YAML
            if "```yaml" in yaml_content:
                yaml_start = yaml_content.find("```yaml") + 7
                yaml_end = yaml_content.find("```", yaml_start)
                yaml_content = yaml_content[yaml_start:yaml_end].strip()
            elif "```" in yaml_content:
                yaml_start = yaml_content.find("```") + 3
                yaml_end = yaml_content.find("```", yaml_start)
                yaml_content = yaml_content[yaml_start:yaml_end].strip()
            
            data = yaml.safe_load(yaml_content)
            
            # Parse issues / 解析问题
            issues = []
            for issue_data in data.get("issues", []):
                issues.append(Issue(
                    severity=issue_data.get("severity", "moderate"),
                    category=issue_data.get("category", "other"),
                    location=issue_data.get("location", ""),
                    problem=issue_data.get("problem", ""),
                    suggestion=issue_data.get("suggestion", "")
                ))
            
            return ReviewResult(
                chapter=chapter,
                draft_version=draft_version,
                issues=issues,
                overall_assessment=data.get("overall_assessment", ""),
                can_proceed=data.get("can_proceed", True)
            )
        except Exception as e:
            # Fallback: create basic review / 回退：创建基本审稿结果
            print(f"[Reviewer] Failed to parse review: {e}")
            return ReviewResult(
                chapter=chapter,
                draft_version=draft_version,
                issues=[],
                overall_assessment="Review parsing failed, manual check recommended.",
                can_proceed=True
            )
