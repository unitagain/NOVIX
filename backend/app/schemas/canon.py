"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

Canon data models.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Fact(BaseModel):
    """Structured canonical fact extracted from chapters."""

    id: str = Field(..., description="Fact ID")
    statement: str = Field(..., description="Fact statement")
    source: str = Field(..., description="Source chapter")
    introduced_in: str = Field(..., description="Chapter where the fact was introduced")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score")
    status: str = Field(
        "confirmed",
        description=(
            "Fact lifecycle status: confirmed（作者确认 / 旧数据兼容）| needs_review（AI 抽取待确认）"
            "| inferred | rejected。"
        ),
    )
    context_prefix: str = Field(
        "",
        description=(
            "Contextual Retrieval 情境前缀：定位本条事实的场景/涉及者，仅用于检索索引"
            "（与 statement 拼接后参与词法+语义打分），不用于展示。"
        ),
    )
    source_type: str = Field("internal", description="Source class: internal | external | crawler | search")
    trust_label: str = Field("trusted", description="Trust label: trusted | untrusted")
    source_refs: List[str] = Field(default_factory=list, description="Persisted provenance references")
    evidence_refs: List[str] = Field(default_factory=list, description="Supporting evidence references")
    confidence_method: str = Field("declared", description="How confidence was established")
    confirmed_by: str = Field("", description="Actor that explicitly confirmed the fact")
    updated_at: str = Field("", description="Last lifecycle update timestamp")
    schema_version: int = Field(2, ge=1, description="Canon fact schema version")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "F001",
                "statement": "Li Ming's younger sister disappeared three years ago.",
                "source": "ch01",
                "introduced_in": "ch01",
                "confidence": 1.0,
            }
        }
    )


class TimelineEvent(BaseModel):
    """Timeline event extracted from narrative progression."""

    time: str = Field(..., description="Event time")
    event: str = Field(..., description="Event description")
    participants: List[str] = Field(..., description="Participants")
    location: str = Field(..., description="Location")
    source: str = Field(..., description="Source chapter")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "time": "Summer three years ago",
                "event": "Younger sister disappeared",
                "participants": ["younger sister"],
                "location": "hometown",
                "source": "ch01",
            }
        }
    )


class CharacterState(BaseModel):
    """Current structured state for a character."""

    character: str = Field(..., description="Character name")
    goals: List[str] = Field(default_factory=list, description="Current goals")
    injuries: List[str] = Field(default_factory=list, description="Injuries")
    inventory: List[str] = Field(default_factory=list, description="Inventory")
    relationships: dict = Field(default_factory=dict, description="Relationships")
    location: Optional[str] = Field(None, description="Current location")
    emotional_state: Optional[str] = Field(None, description="Emotional state")
    last_seen: str = Field(..., description="Last seen chapter")
