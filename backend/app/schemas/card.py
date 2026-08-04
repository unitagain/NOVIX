"""
中文说明：卡片数据模型，定义角色/世界观/风格卡结构。

Card data models.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CharacterCard(BaseModel):
    """Character card."""

    name: str = Field(..., description="Character name")
    aliases: List[str] = Field(default_factory=list, description="Character aliases")
    description: str = Field(..., description="Character description")
    voice: Optional[str] = Field(
        default=None,
        description=(
            "角色专属口吻样本（示例对白、常用语气），与角色卡绑定，只描述「怎么说话」，"
            "不作为已发生事实 / Character-scoped voice samples: how this character speaks. "
            "Not a source of narrative facts."
        ),
    )
    stars: Optional[int] = Field(default=None, ge=1, le=3, description="Importance stars (1-3)")


class WorldCard(BaseModel):
    """World card."""

    name: str = Field(..., description="Setting name")
    description: str = Field(..., description="Setting description")
    aliases: List[str] = Field(default_factory=list, description="World aliases")
    category: Optional[str] = Field(default=None, description="World category")
    rules: List[str] = Field(default_factory=list, description="World rules")
    immutable: Optional[bool] = Field(default=None, description="Immutable flag")
    stars: Optional[int] = Field(default=None, ge=1, le=3, description="Importance stars (1-3)")


class StyleCard(BaseModel):
    """Writing style card."""

    style: str = Field(..., description="Writing style requirements")


class RelationNodePosition(BaseModel):
    """画布节点坐标 / Canvas node position（纯视图状态，丢失不影响关系语义）。"""

    x: float = Field(0.0, description="Canvas x coordinate")
    y: float = Field(0.0, description="Canvas y coordinate")


class RelationEdge(BaseModel):
    """角色关系边（作者设定层）/ Authored character relation edge.

    方向语义（不可含糊，前后端必须一致）：``from`` 是 ``to`` 的 ``relation``；
    ``appellation`` 是 ``to`` 对 ``from`` 的称呼，``reverse_appellation`` 是
    ``from`` 对 ``to`` 的称呼。两个称呼各自独立填写，不做自动互推。
    ``from``/``to`` 是 Python 关键字，字段名加后缀，序列化保持 ``from``/``to``。

    这里只做结构约束；长度、去重、端点存在性等业务规则由
    ``storage/character_relations.py`` 单一 owner 校验，避免两套规则。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, description="Stable edge id; generated server-side when omitted")
    from_character: str = Field(..., alias="from", description="Character the relation is asserted about")
    to_character: str = Field(..., alias="to", description="Character whose perspective the relation is stated from")
    relation: str = Field(..., description="Relation label: from is to's {relation}")
    appellation: Optional[str] = Field(default=None, description="How `to` addresses `from`")
    reverse_appellation: Optional[str] = Field(default=None, description="How `from` addresses `to`")


class RelationsDocument(BaseModel):
    """``cards/relations.yaml`` 全量文档 / Full relation document: authored edges + canvas layout."""

    edges: List[RelationEdge] = Field(default_factory=list, description="Authored relation edges")
    layout: Dict[str, RelationNodePosition] = Field(
        default_factory=dict, description="Canvas coordinates keyed by character name"
    )
