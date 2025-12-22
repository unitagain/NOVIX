"""
Context Selector / 上下文选择器
Selects relevant context items for each agent task
为每个Agent任务选择相关的上下文项
"""

from typing import List, Dict, Any, Optional
from app.storage import CardStorage, CanonStorage, DraftStorage


class ContextSelector:
    """
    Selects relevant context items based on task requirements
    根据任务需求选择相关的上下文项
    """
    
    def __init__(
        self,
        card_storage: CardStorage,
        canon_storage: CanonStorage,
        draft_storage: DraftStorage
    ):
        """
        Initialize context selector
        
        Args:
            card_storage: Card storage instance / 卡片存储实例
            canon_storage: Canon storage instance / 事实表存储实例
            draft_storage: Draft storage instance / 草稿存储实例
        """
        self.card_storage = card_storage
        self.canon_storage = canon_storage
        self.draft_storage = draft_storage
    
    async def select_for_chapter(
        self,
        project_id: str,
        chapter: str,
        character_names: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Select all relevant context for a chapter
        为章节选择所有相关上下文
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            character_names: Optional list of character names / 可选的角色名称列表
            
        Returns:
            Dictionary with selected context / 包含选中上下文的字典
        """
        context = {}
        
        # Load fixed cards (always included) / 加载固定卡片（始终包含）
        context["style_card"] = await self.card_storage.get_style_card(project_id)
        context["rules_card"] = await self.card_storage.get_rules_card(project_id)
        
        # Load character cards (by need) / 按需加载角色卡
        if character_names:
            character_cards = []
            for name in character_names:
                card = await self.card_storage.get_character_card(project_id, name)
                if card:
                    character_cards.append(card)
            context["character_cards"] = character_cards
        else:
            # Load all if not specified / 如果未指定则加载全部
            char_names = await self.card_storage.list_character_cards(project_id)
            character_cards = []
            for name in char_names:
                card = await self.card_storage.get_character_card(project_id, name)
                if card:
                    character_cards.append(card)
            context["character_cards"] = character_cards
        
        # Load world cards / 加载世界观卡
        world_card_names = await self.card_storage.list_world_cards(project_id)
        world_cards = []
        for name in world_card_names:
            card = await self.card_storage.get_world_card(project_id, name)
            if card:
                world_cards.append(card)
        context["world_cards"] = world_cards
        
        # Load canon / 加载事实表
        context["facts"] = await self.canon_storage.get_all_facts(project_id)
        context["timeline"] = await self.canon_storage.get_all_timeline_events(project_id)
        
        # Load character states / 加载角色状态
        context["character_states"] = await self.canon_storage.get_all_character_states(project_id)
        
        # Load previous summaries / 加载前文摘要
        context["previous_summaries"] = await self._load_previous_summaries(
            project_id,
            chapter
        )
        
        return context
    
    async def _load_previous_summaries(
        self,
        project_id: str,
        current_chapter: str,
        count: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Load summaries of previous chapters
        加载前面章节的摘要
        
        Args:
            project_id: Project ID / 项目ID
            current_chapter: Current chapter ID / 当前章节ID
            count: Number of previous chapters to load / 加载的前面章节数量
            
        Returns:
            List of chapter summaries / 章节摘要列表
        """
        try:
            current_num = int(current_chapter.replace("ch", ""))
        except:
            return []
        
        summaries = []
        for i in range(max(1, current_num - count), current_num):
            chapter_id = f"ch{i:02d}"
            summary = await self.draft_storage.get_chapter_summary(project_id, chapter_id)
            if summary:
                summaries.append({
                    "chapter": chapter_id,
                    "title": summary.title,
                    "summary": summary.brief_summary,
                    "key_events": summary.key_events
                })
        
        return summaries
    
    def filter_by_relevance(
        self,
        items: List[Any],
        query: str,
        max_items: int = 10
    ) -> List[Any]:
        """
        Filter items by relevance to query (simple keyword matching)
        按与查询的相关性过滤项（简单的关键词匹配）
        
        Args:
            items: List of items to filter / 要过滤的项列表
            query: Query string / 查询字符串
            max_items: Maximum number of items to return / 返回的最大项数
            
        Returns:
            Filtered items / 过滤后的项
        """
        # TODO: Implement more sophisticated relevance scoring
        # 待实现：更复杂的相关性评分
        # For now, just return the most recent items
        # 目前只返回最近的项
        return items[-max_items:] if len(items) > max_items else items
