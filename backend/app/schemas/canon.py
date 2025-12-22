"""
Canon Data Models / 事实表数据模型
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class Fact(BaseModel):
    """Fact model / 事实模型"""
    id: str = Field(..., description="Fact ID / 事实ID")
    statement: str = Field(..., description="Fact statement / 事实陈述")
    source: str = Field(..., description="Source chapter / 来源章节")
    introduced_in: str = Field(..., description="Chapter where introduced / 引入章节")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence level / 可信度")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "F001",
                "statement": "李明的妹妹在三年前失踪",
                "source": "ch01",
                "introduced_in": "ch01",
                "confidence": 1.0
            }
        }


class TimelineEvent(BaseModel):
    """Timeline event model / 时间线事件模型"""
    time: str = Field(..., description="Event time / 事件时间")
    event: str = Field(..., description="Event description / 事件描述")
    participants: List[str] = Field(..., description="Participants / 参与者")
    location: str = Field(..., description="Location / 地点")
    source: str = Field(..., description="Source chapter / 来源章节")
    
    class Config:
        json_schema_extra = {
            "example": {
                "time": "三年前夏天",
                "event": "妹妹失踪",
                "participants": ["妹妹"],
                "location": "老家",
                "source": "ch01"
            }
        }


class CharacterState(BaseModel):
    """Character state model / 角色状态模型"""
    character: str = Field(..., description="Character name / 角色名称")
    goals: List[str] = Field(default_factory=list, description="Current goals / 当前目标")
    injuries: List[str] = Field(default_factory=list, description="Injuries / 伤势")
    inventory: List[str] = Field(default_factory=list, description="Inventory / 持有物")
    relationships: dict = Field(default_factory=dict, description="Relationships / 关系")
    location: Optional[str] = Field(None, description="Current location / 当前位置")
    emotional_state: Optional[str] = Field(None, description="Emotional state / 情绪状态")
    last_seen: str = Field(..., description="Last seen in chapter / 最后出现章节")
