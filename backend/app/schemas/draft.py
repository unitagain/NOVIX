"""
Draft and Writing Session Data Models / 草稿与写作会话数据模型
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class SceneBrief(BaseModel):
    """Scene brief model / 场景简报模型"""

    chapter: str = Field(..., description="Chapter ID / 章节ID")
    title: str = Field(..., description="Chapter title / 章节标题")
    goal: str = Field(..., description="Chapter goal / 章节目标")
    characters: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Involved characters / 涉及角色",
    )
    timeline_context: Dict[str, str] = Field(
        default_factory=dict,
        description="Timeline context / 时间线上下文",
    )
    world_constraints: List[str] = Field(
        default_factory=list,
        description="World constraints / 世界观约束",
    )
    facts: List[str] = Field(
        default_factory=list,
        description="Relevant facts",
    )
    style_reminder: str = Field(..., description="Style reminder / 文风提醒")
    forbidden: List[str] = Field(
        default_factory=list,
        description="Forbidden actions / 禁区",
    )


class Draft(BaseModel):
    """Draft model / 草稿模型"""

    chapter: str = Field(..., description="Chapter ID / 章节ID")
    version: str = Field(..., description="Draft version / 版本号")
    content: str = Field(..., description="Draft content / 草稿内容")
    word_count: int = Field(..., description="Word count / 字数")
    pending_confirmations: List[str] = Field(
        default_factory=list,
        description="Items pending confirmation / 待确认事项",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Creation timestamp / 创建时间",
    )

    def __len__(self) -> int:
        """Return length of content for len() compatibility / 支持 len()"""
        return len(self.content)

    def __str__(self) -> str:
        """Return content string for str() compatibility / 支持 str()"""
        return self.content


class Issue(BaseModel):
    """Review issue model / 审核问题模型"""

    severity: str = Field(..., description="Severity / 严重程度")
    category: str = Field(..., description="Issue category / 问题类别")
    location: str = Field(..., description="Location in text / 位置")
    problem: str = Field(..., description="Problem description / 问题描述")
    suggestion: str = Field(..., description="Suggested fix / 修改建议")


class ReviewResult(BaseModel):
    """Review result model / 审核结果模型"""

    chapter: str = Field(..., description="Chapter ID / 章节ID")
    draft_version: str = Field(..., description="Reviewed draft version / 审核版本")
    issues: List[Issue] = Field(default_factory=list, description="Issues found / 发现的问题")
    overall_assessment: str = Field(..., description="Overall assessment / 总体评价")
    can_proceed: bool = Field(..., description="Can proceed to editing / 是否可编辑")


class RevisionRecord(BaseModel):
    """Revision record model / 修订记录模型"""

    issue_id: int = Field(..., description="Issue ID / 问题ID")
    status: str = Field(..., description="Status / 状态")
    action_taken: str = Field(..., description="Action taken / 处理方式")


class ChapterSummary(BaseModel):
    """Chapter summary model / 章节摘要模型"""

    chapter: str = Field(..., description="Chapter ID / 章节ID")
    volume_id: Optional[str] = Field(default=None, description="Volume ID / 所属分卷ID")
    order_index: Optional[int] = Field(default=None, description="Order index within volume / 卷内排序序号")
    title: str = Field(default="", description="Chapter title / 章节标题")
    word_count: int = Field(default=0, description="Word count / 字数")
    key_events: List[str] = Field(default_factory=list, description="Key events / 关键事件")
    new_facts: List[str] = Field(default_factory=list, description="New facts / 新增事实")
    character_state_changes: List[str] = Field(
        default_factory=list,
        description="Character state changes / 角色状态变化",
    )
    open_loops: List[str] = Field(default_factory=list, description="Open story loops / 未解悬念")
    brief_summary: str = Field(default="", description="Brief summary / 简要概述")


class CardProposal(BaseModel):
    """Proposal for a new setting card."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Entity name")
    type: str = Field(..., description="Type: Character or World")
    description: str = Field(..., description="Proposed content")
    rationale: str = Field(..., description="Why this card is important")
    source_text: str = Field(default="", description="Quote from source text")
    confidence: float = Field(default=0.8, description="Confidence score 0.0-1.0")
