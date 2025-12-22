"""
Card Storage / 卡片存储
Manage character cards, world cards, style cards, and rules cards
管理角色卡、世界观卡、文风卡和规则卡
"""

from pathlib import Path
from typing import List, Optional
from app.storage.base import BaseStorage
from app.schemas.card import CharacterCard, WorldCard, StyleCard, RulesCard


class CardStorage(BaseStorage):
    """Storage operations for cards / 卡片存储操作"""
    
    async def get_character_card(
        self,
        project_id: str,
        character_name: str
    ) -> Optional[CharacterCard]:
        """
        Get a character card / 获取角色卡
        
        Args:
            project_id: Project ID / 项目ID
            character_name: Character name / 角色名称
            
        Returns:
            Character card or None / 角色卡或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "characters" / f"{character_name}.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return CharacterCard(**data)
    
    async def save_character_card(
        self,
        project_id: str,
        card: CharacterCard
    ) -> None:
        """
        Save a character card / 保存角色卡
        
        Args:
            project_id: Project ID / 项目ID
            card: Character card / 角色卡
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "characters" / f"{card.name}.yaml"
        )
        
        await self.write_yaml(file_path, card.model_dump())
    
    async def list_character_cards(self, project_id: str) -> List[str]:
        """
        List all character card names / 列出所有角色卡名称
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            List of character names / 角色名称列表
        """
        cards_dir = (
            self.get_project_path(project_id) /
            "cards" / "characters"
        )
        
        if not cards_dir.exists():
            return []
        
        return [
            f.stem for f in cards_dir.glob("*.yaml")
        ]
    
    async def delete_character_card(
        self,
        project_id: str,
        character_name: str
    ) -> bool:
        """
        Delete a character card / 删除角色卡
        
        Args:
            project_id: Project ID / 项目ID
            character_name: Character name / 角色名称
            
        Returns:
            True if deleted, False if not found / 是否删除成功
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "characters" / f"{character_name}.yaml"
        )
        
        if file_path.exists():
            file_path.unlink()
            return True
        return False
    
    async def get_world_card(
        self,
        project_id: str,
        card_name: str
    ) -> Optional[WorldCard]:
        """
        Get a world card / 获取世界观卡
        
        Args:
            project_id: Project ID / 项目ID
            card_name: Card name / 卡片名称
            
        Returns:
            World card or None / 世界观卡或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "world" / f"{card_name}.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return WorldCard(**data)
    
    async def save_world_card(
        self,
        project_id: str,
        card: WorldCard
    ) -> None:
        """
        Save a world card / 保存世界观卡
        
        Args:
            project_id: Project ID / 项目ID
            card: World card / 世界观卡
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "world" / f"{card.name}.yaml"
        )
        
        await self.write_yaml(file_path, card.model_dump())
    
    async def list_world_cards(self, project_id: str) -> List[str]:
        """
        List all world card names / 列出所有世界观卡名称
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            List of card names / 卡片名称列表
        """
        cards_dir = (
            self.get_project_path(project_id) /
            "cards" / "world"
        )
        
        if not cards_dir.exists():
            return []
        
        return [f.stem for f in cards_dir.glob("*.yaml")]
    
    async def get_style_card(self, project_id: str) -> Optional[StyleCard]:
        """
        Get style card / 获取文风卡
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            Style card or None / 文风卡或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "style.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return StyleCard(**data)
    
    async def save_style_card(
        self,
        project_id: str,
        card: StyleCard
    ) -> None:
        """
        Save style card / 保存文风卡
        
        Args:
            project_id: Project ID / 项目ID
            card: Style card / 文风卡
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "style.yaml"
        )
        
        await self.write_yaml(file_path, card.model_dump())
    
    async def get_rules_card(self, project_id: str) -> Optional[RulesCard]:
        """
        Get rules card / 获取规则卡
        
        Args:
            project_id: Project ID / 项目ID
            
        Returns:
            Rules card or None / 规则卡或None
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "rules.yaml"
        )
        
        if not file_path.exists():
            return None
        
        data = await self.read_yaml(file_path)
        return RulesCard(**data)
    
    async def save_rules_card(
        self,
        project_id: str,
        card: RulesCard
    ) -> None:
        """
        Save rules card / 保存规则卡
        
        Args:
            project_id: Project ID / 项目ID
            card: Rules card / 规则卡
        """
        file_path = (
            self.get_project_path(project_id) /
            "cards" / "rules.yaml"
        )
        
        await self.write_yaml(file_path, card.model_dump())
