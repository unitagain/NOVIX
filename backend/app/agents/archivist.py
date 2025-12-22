"""
Archivist Agent / 资料管理员
Manages cards, canon (facts/timeline/character states), and generates scene briefs
管理卡片、事实表（事实/时间线/角色状态）并生成场景简报
"""

import yaml
from typing import Dict, Any, List, Optional
from app.agents.base import BaseAgent
from app.schemas.draft import SceneBrief, ChapterSummary
from app.schemas.canon import Fact, TimelineEvent, CharacterState


class ArchivistAgent(BaseAgent):
    """
    Archivist agent responsible for managing facts and generating scene briefs
    资料管理员，负责管理事实真相并生成场景简报
    """
    
    def get_agent_name(self) -> str:
        """Get agent name / 获取 Agent 名称"""
        return "archivist"
    
    def get_system_prompt(self) -> str:
        """Get system prompt / 获取系统提示词"""
        return """You are an Archivist agent for novel writing.

Your responsibilities:
1. Maintain facts, timeline, and character states
2. Generate scene briefs for writers based on chapter goals
3. Detect conflicts between new content and existing canon
4. Ensure consistency across the story

Core principle:
- Chapter goal is the primary driver. Cards/canon are constraints and a knowledge base.
- Do NOT try to cover every card in every chapter. Select only what is relevant to achieve this chapter's goal.
- Prefer clarity and actionability over completeness.

Output Format:
- Generate scene briefs in YAML format
- Include relevant context only (not exhaustive): characters, timeline, world constraints, style reminders, and forbidden actions
- Flag any conflicts or inconsistencies

你是一个小说写作的资料管理员。

职责：
1. 维护事实表、时间线和角色状态
2. 根据章节目标生成场景简报
3. 检测新内容与现有设定的冲突
4. 确保故事的一致性

核心原则：
- 章节目标优先。卡片/事实表是约束与知识库。
- 不要试图在一章里覆盖所有设定卡，只挑选与本章目标强相关的部分。
- 追求可执行性与清晰度，不追求面面俱到。

输出格式：
- 以 YAML 格式生成场景简报
- 仅包含本章需要的相关上下文：角色、时间线、世界观约束、文风提醒和禁区（不必穷举）
- 标记任何冲突或不一致"""
    
    async def execute(
        self,
        project_id: str,
        chapter: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate scene brief for a chapter
        为章节生成场景简报
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            context: Context with chapter_goal, chapter_title / 包含章节目标和标题的上下文
            
        Returns:
            Result with scene_brief / 包含场景简报的结果
        """
        # Load relevant cards / 加载相关卡片
        style_card = await self.card_storage.get_style_card(project_id)
        rules_card = await self.card_storage.get_rules_card(project_id)
        
        # Get character names from context or load all / 从上下文获取角色名称或加载全部
        character_names = context.get("characters", [])
        if not character_names:
            character_names = await self.card_storage.list_character_cards(project_id)
        
        characters = []
        for name in character_names[:5]:  # Limit to 5 characters / 限制为5个角色
            card = await self.card_storage.get_character_card(project_id, name)
            if card:
                # Get current state / 获取当前状态
                state = await self.canon_storage.get_character_state(project_id, name)
                characters.append({
                    "card": card,
                    "state": state
                })
        
        # Load timeline events near current chapter (MVP-2 Week 6)
        # 加载邻近章节的时间线事件（MVP-2 第6周）
        recent_events = await self.canon_storage.get_timeline_events_near_chapter(
            project_id=project_id,
            chapter=chapter,
            window=3,
            max_events=10,
        )
        
        # Load recent facts / 加载最近的事实
        facts = await self.canon_storage.get_all_facts(project_id)
        recent_facts = facts[-10:] if facts else []  # Last 10 facts / 最近10条事实
        
        # Generate scene brief using LLM / 使用大模型生成场景简报
        scene_brief_content = await self._generate_scene_brief(
            chapter=chapter,
            chapter_goal=context.get("chapter_goal", ""),
            chapter_title=context.get("chapter_title", ""),
            characters=characters,
            timeline_events=recent_events,
            facts=recent_facts,
            style_card=style_card,
            rules_card=rules_card
        )
        
        # Parse and save scene brief / 解析并保存场景简报
        scene_brief = self._parse_scene_brief(scene_brief_content, chapter)
        await self.draft_storage.save_scene_brief(project_id, chapter, scene_brief)
        
        return {
            "success": True,
            "scene_brief": scene_brief,
            "conflicts": []  # TODO: Implement conflict detection / 待实现：冲突检测
        }
    
    async def _generate_scene_brief(
        self,
        chapter: str,
        chapter_goal: str,
        chapter_title: str,
        characters: List[Dict],
        timeline_events: List,
        facts: List,
        style_card: Any,
        rules_card: Any
    ) -> str:
        """
        Generate scene brief using LLM
        使用大模型生成场景简报
        
        Args:
            chapter: Chapter ID / 章节ID
            chapter_goal: Chapter goal / 章节目标
            chapter_title: Chapter title / 章节标题
            characters: Character info / 角色信息
            timeline_events: Timeline events / 时间线事件
            facts: Recent facts / 最近的事实
            style_card: Style card / 文风卡
            rules_card: Rules card / 规则卡
            
        Returns:
            Generated scene brief in YAML format / YAML格式的场景简报
        """
        # Build context items / 构建上下文项
        context_items = []
        
        # Add characters / 添加角色
        if characters:
            char_info = ["Characters:"]
            for char in characters:
                card = char["card"]
                state = char.get("state")
                char_info.append(f"\n- {card.name}")
                char_info.append(f"  Identity: {card.identity}")
                char_info.append(f"  Motivation: {card.motivation}")
                char_info.append(f"  Boundaries: {', '.join(card.boundaries)}")
                if state:
                    char_info.append(f"  Current State: {state.emotional_state or 'Normal'}")
                    char_info.append(f"  Location: {state.location or 'Unknown'}")
            context_items.append("".join(char_info))
        
        # Add timeline / 添加时间线
        if timeline_events:
            timeline_info = ["Recent Timeline:"]
            for event in timeline_events:
                timeline_info.append(
                    f"\n- {event.time}: {event.event} at {event.location}"
                )
            context_items.append("".join(timeline_info))
        
        # Add facts / 添加事实
        if facts:
            facts_info = ["Recent Facts:"]
            for fact in facts:
                facts_info.append(f"\n- {fact.statement}")
            context_items.append("".join(facts_info))
        
        # Add style / 添加文风
        if style_card:
            style_info = f"""Writing Style:
- Narrative Distance: {style_card.narrative_distance}
- Pacing: {style_card.pacing}
- Sentence Structure: {style_card.sentence_structure}"""
            context_items.append(style_info)
        
        # Add rules / 添加规则
        if rules_card and rules_card.forbidden_actions:
            rules_info = "Forbidden Actions:\n" + "\n".join(
                [f"- {action}" for action in rules_card.forbidden_actions]
            )
            context_items.append(rules_info)
        
        # Build user prompt / 构建用户提示
        user_prompt = f"""Generate a scene brief for:
Chapter: {chapter}
Title: {chapter_title}
Goal: {chapter_goal}

Goal-first requirements:
- The scene brief must be a plan to accomplish the chapter goal.
- Only include characters that must appear or be referenced to achieve the goal.
- Only include world_constraints/forbidden items that are actually relevant to this chapter.
- Avoid dumping all facts/constraints; select the minimal set that prevents mistakes.
- If a useful card/canon detail is missing, include it as [TO_CONFIRM: ...] in the most appropriate field.

章节目标优先要求：
- 场景简报必须服务于章节目标（是一份可执行的行动计划）。
- 只列出本章必须出场/被提及的角色。
- world_constraints/forbidden 只写与本章相关的约束/禁区，不要把所有设定都塞进来。
- 不要倾倒所有事实与约束；只选“防止写错、能推进目标”的最小集合。
- 如果关键设定缺失，请在合适字段用 [TO_CONFIRM: ...] 标记。

Output the scene brief in YAML format with these fields:
```yaml
chapter: {chapter}
title: {chapter_title}
goal: {chapter_goal}
characters:
  - name: <character_name>
    current_state: <state_description>
    relevant_traits: <traits>
timeline_context:
  before: <previous_event>
  current: <current_time>
  after: <upcoming_hints>
world_constraints:
  - <constraint1>
  - <constraint2>
style_reminder: <style_note>
forbidden:
  - <forbidden_action1>
  - <forbidden_action2>
```

Generate the YAML only, no additional text.
生成 YAML 内容，不要额外的文字。"""
        
        # Call LLM / 调用大模型
        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=context_items
        )
        
        response = await self.call_llm(messages)
        
        # Extract YAML from response / 从响应中提取 YAML
        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        
        return response
    
    def _parse_scene_brief(self, yaml_content: str, chapter: str) -> SceneBrief:
        """
        Parse YAML content to SceneBrief object
        解析 YAML 内容为 SceneBrief 对象
        
        Args:
            yaml_content: YAML string / YAML字符串
            chapter: Chapter ID / 章节ID
            
        Returns:
            SceneBrief object / SceneBrief 对象
        """
        try:
            data = yaml.safe_load(yaml_content)
            # Ensure required fields / 确保必需字段
            data["chapter"] = chapter
            data["title"] = data.get("title", "")
            data["goal"] = data.get("goal", "")
            data["characters"] = data.get("characters", [])
            data["timeline_context"] = data.get("timeline_context", {})
            data["world_constraints"] = data.get("world_constraints", [])
            data["style_reminder"] = data.get("style_reminder", "")
            data["forbidden"] = data.get("forbidden", [])
            
            return SceneBrief(**data)
        except Exception as e:
            # Fallback: create basic scene brief / 回退：创建基本场景简报
            print(f"[Archivist] Failed to parse scene brief: {e}")
            return SceneBrief(
                chapter=chapter,
                title="",
                goal="",
                characters=[],
                timeline_context={},
                world_constraints=[],
                style_reminder="",
                forbidden=[]
            )

    async def generate_chapter_summary(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> ChapterSummary:
        """Generate structured chapter summary / 生成结构化章节摘要

        Notes:
        - Uses LLM when available.
        - Falls back to heuristic summary in Mock mode or on parse failure.

        说明：
        - 优先使用大模型生成结构化摘要。
        - Mock 模式或解析失败时，回退到本地启发式摘要，保证流程可跑通。
        """

        # Decide provider / 判断当前使用的供应商
        provider = self.gateway.get_provider_for_agent(self.get_agent_name())
        if provider == "mock":
            return self._fallback_chapter_summary(chapter, chapter_title, final_draft)

        yaml_content = await self._generate_chapter_summary_yaml(
            chapter=chapter,
            chapter_title=chapter_title,
            final_draft=final_draft,
        )

        summary = self._parse_chapter_summary(yaml_content, chapter, chapter_title, final_draft)
        return summary


    async def extract_canon_updates(
        self,
        project_id: str,
        chapter: str,
        final_draft: str,
    ) -> Dict[str, Any]:
        """Extract canon updates from final draft / 从成稿中抽取 Canon 更新

        Output:
        - facts: List[Fact]
        - timeline_events: List[TimelineEvent]
        - character_states: List[CharacterState]

        输出：
        - facts：新增事实列表
        - timeline_events：新增时间线事件
        - character_states：角色状态更新（可多角色）

        Notes:
        - Designed for MVP-2 Week 5 (facts/events/state extraction).
        - Mock provider returns empty updates.
        - Any parse failure returns empty updates.

        说明：
        - 对应 MVP-2 第5周（事实/事件抽取 + 角色状态更新）。
        - Mock 模式返回空更新。
        - 解析失败返回空更新，确保流程不中断。
        """

        provider = self.gateway.get_provider_for_agent(self.get_agent_name())
        if provider == "mock":
            return {
                "facts": [],
                "timeline_events": [],
                "character_states": [],
            }

        try:
            yaml_content = await self._generate_canon_updates_yaml(chapter=chapter, final_draft=final_draft)
            return await self._parse_canon_updates_yaml(
                project_id=project_id,
                chapter=chapter,
                yaml_content=yaml_content,
            )
        except Exception:
            return {
                "facts": [],
                "timeline_events": [],
                "character_states": [],
            }


    async def _generate_canon_updates_yaml(self, chapter: str, final_draft: str) -> str:
        """Generate canon updates YAML via LLM / 通过大模型生成 Canon 更新 YAML"""

        user_prompt = f"""Extract canon updates from the final draft.

Chapter: {chapter}

Output YAML only, matching this schema:
```yaml
facts:
  - statement: <fact statement>
    confidence: <0.0-1.0>
timeline_events:
  - time: <time description>
    event: <event description>
    participants: [<name1>, <name2>]
    location: <location>
character_states:
  - character: <character name>
    goals: [<goal1>]
    injuries: [<injury1>]
    inventory: [<item1>]
    relationships: {{ <other>: <relation> }}
    location: <current location>
    emotional_state: <emotion>
```

Rules:
- Include only updates that can be inferred from the draft.
- If an item is unknown, use empty string / empty list.

规则：
- 只抽取能从文本中推断的内容。
- 不确定则填空字符串/空列表。

Final Draft:
""" + final_draft

        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=None,
        )
        response = await self.call_llm(messages)

        # Extract YAML fenced block if exists / 如存在代码块则提取
        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()

        return response


    async def _parse_canon_updates_yaml(
        self,
        project_id: str,
        chapter: str,
        yaml_content: str,
    ) -> Dict[str, Any]:
        """Parse canon update YAML / 解析 Canon 更新 YAML"""

        data = yaml.safe_load(yaml_content) or {}

        # Pre-calc next fact id / 预计算下一个 Fact ID
        existing_facts = await self.canon_storage.get_all_facts(project_id)
        next_fact_index = len(existing_facts) + 1

        facts: List[Fact] = []
        for item in data.get("facts", []) or []:
            statement = ""
            confidence = 1.0
            if isinstance(item, str):
                statement = item
            elif isinstance(item, dict):
                statement = str(item.get("statement", "") or "")
                conf_raw = item.get("confidence")
                try:
                    confidence = float(conf_raw) if conf_raw is not None else 1.0
                except Exception:
                    confidence = 1.0

            if not statement.strip():
                continue

            fact_id = f"F{next_fact_index:04d}"
            next_fact_index += 1
            facts.append(
                Fact(
                    id=fact_id,
                    statement=statement.strip(),
                    source=chapter,
                    introduced_in=chapter,
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )

        timeline_events: List[TimelineEvent] = []
        for item in data.get("timeline_events", []) or []:
            if not isinstance(item, dict):
                continue
            timeline_events.append(
                TimelineEvent(
                    time=str(item.get("time", "") or ""),
                    event=str(item.get("event", "") or ""),
                    participants=list(item.get("participants", []) or []),
                    location=str(item.get("location", "") or ""),
                    source=chapter,
                )
            )

        character_states: List[CharacterState] = []
        for item in data.get("character_states", []) or []:
            if not isinstance(item, dict):
                continue
            character = str(item.get("character", "") or "").strip()
            if not character:
                continue
            character_states.append(
                CharacterState(
                    character=character,
                    goals=list(item.get("goals", []) or []),
                    injuries=list(item.get("injuries", []) or []),
                    inventory=list(item.get("inventory", []) or []),
                    relationships=dict(item.get("relationships", {}) or {}),
                    location=item.get("location"),
                    emotional_state=item.get("emotional_state"),
                    last_seen=chapter,
                )
            )

        return {
            "facts": facts,
            "timeline_events": timeline_events,
            "character_states": character_states,
        }

    async def _generate_chapter_summary_yaml(
        self,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> str:
        """Generate ChapterSummary YAML via LLM / 通过大模型生成 ChapterSummary 的 YAML"""

        # Build prompt / 构建提示词
        user_prompt = f"""Generate a structured chapter summary in YAML.

Chapter: {chapter}
Title: {chapter_title}

The YAML must match this schema exactly:
```yaml
chapter: {chapter}
title: {chapter_title}
word_count: <int>
key_events:
  - <event1>
new_facts:
  - <fact1>
character_state_changes:
  - <change1>
open_loops:
  - <loop1>
brief_summary: <one paragraph summary>
```

Constraints:
- Write concise but informative items.
- Output YAML only, no extra text.

要求：
- 输出必须是 YAML，且字段完全匹配上述 schema。
- 每一项尽量简洁但信息充分。
- 只输出 YAML，不要额外文字。

Final Draft:
""" + final_draft

        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=None,
        )

        response = await self.call_llm(messages)

        # Extract YAML fenced block if exists / 如存在代码块则提取
        if "```yaml" in response:
            yaml_start = response.find("```yaml") + 7
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()
        elif "```" in response:
            yaml_start = response.find("```") + 3
            yaml_end = response.find("```", yaml_start)
            response = response[yaml_start:yaml_end].strip()

        return response

    def _parse_chapter_summary(
        self,
        yaml_content: str,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> ChapterSummary:
        """Parse YAML into ChapterSummary / 将 YAML 解析为 ChapterSummary"""

        try:
            data = yaml.safe_load(yaml_content) or {}
            data["chapter"] = chapter
            data["title"] = data.get("title") or chapter_title

            # Ensure required fields / 确保字段存在
            data.setdefault("word_count", len(final_draft))
            data.setdefault("key_events", [])
            data.setdefault("new_facts", [])
            data.setdefault("character_state_changes", [])
            data.setdefault("open_loops", [])
            data.setdefault("brief_summary", "")

            return ChapterSummary(**data)
        except Exception:
            return self._fallback_chapter_summary(chapter, chapter_title, final_draft)

    def _fallback_chapter_summary(
        self,
        chapter: str,
        chapter_title: str,
        final_draft: str,
    ) -> ChapterSummary:
        """Fallback summary without LLM / 无大模型时的回退摘要"""

        # Heuristic brief summary / 启发式简要摘要
        brief = final_draft.strip().replace("\r\n", "\n")
        brief = brief[:400] + ("..." if len(brief) > 400 else "")

        return ChapterSummary(
            chapter=chapter,
            title=chapter_title or chapter,
            word_count=len(final_draft),
            key_events=[],
            new_facts=[],
            character_state_changes=[],
            open_loops=[],
            brief_summary=brief,
        )
