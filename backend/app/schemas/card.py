"""
Card Data Models / 卡片数据模型
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class CharacterCard(BaseModel):
    """Character card model / 角色卡模型"""
    name: str = Field(..., description="Character name / 角色名称")
    identity: str = Field(..., description="Character identity / 角色身份")
    motivation: str = Field(..., description="Character motivation / 角色动机")
    personality: List[str] = Field(default_factory=list, description="Personality traits / 性格特点")
    speech_pattern: Optional[str] = Field(None, description="Speech pattern / 说话风格")
    relationships: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Relationships with other characters / 与其他角色的关系"
    )
    boundaries: List[str] = Field(
        default_factory=list,
        description="Behavioral boundaries / 行为边界"
    )
    arc: Optional[str] = Field(None, description="Character arc / 角色成长弧线")
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "李明",
                "identity": "22岁大学生，计算机专业",
                "motivation": "找到失踪的妹妹",
                "personality": ["内向但执着", "逻辑思维强"],
                "speech_pattern": "说话简短，常用反问句",
                "relationships": [
                    {"target": "妹妹", "relation": "亲人，极度保护欲"}
                ],
                "boundaries": ["绝不会主动伤害无辜"],
                "arc": "从封闭自我到学会信任他人"
            }
        }


class WorldCard(BaseModel):
    """World setting card model / 世界观卡模型"""
    name: str = Field(..., description="Setting name / 设定名称")
    category: str = Field(..., description="Category (e.g., location, rule, magic) / 类别")
    description: str = Field(..., description="Detailed description / 详细描述")
    rules: List[str] = Field(
        default_factory=list,
        description="Rules and constraints / 规则与约束"
    )
    immutable: bool = Field(
        False,
        description="Whether this setting is immutable / 是否为不可变设定"
    )


class StyleCard(BaseModel):
    """Writing style card model / 文风卡模型"""
    narrative_distance: str = Field(
        ...,
        description="Narrative perspective / 叙事距离"
    )
    pacing: str = Field(..., description="Story pacing / 节奏控制")
    sentence_structure: str = Field(
        ...,
        description="Sentence structure preference / 句式偏好"
    )
    vocabulary_constraints: List[str] = Field(
        default_factory=list,
        description="Forbidden or preferred words / 词汇禁用或偏好"
    )
    example_passages: List[str] = Field(
        default_factory=list,
        description="Example passages / 示例片段"
    )


class RulesCard(BaseModel):
    """Writing rules card model / 规则卡模型"""
    citation_requirements: List[str] = Field(
        default_factory=list,
        description="Citation requirements / 引用要求"
    )
    conflict_handling: List[str] = Field(
        default_factory=list,
        description="Conflict handling rules / 冲突处理规则"
    )
    forbidden_actions: List[str] = Field(
        default_factory=list,
        description="Forbidden actions / 禁止行为"
    )
    quality_standards: List[str] = Field(
        default_factory=list,
        description="Quality standards / 质量标准"
    )
