"""
Context Engine Models / 上下文引擎数据模型
Core data structures for the context engineering system
上下文工程系统的核心数据结构
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime


class ContextPriority(Enum):
    """
    上下文优先级 - 决定加载顺序和保留策略
    Context priority - determines loading order and retention policy

    CRITICAL: 必须包含，不可压缩 (e.g., style card, current task)
    HIGH: 优先包含，可少量压缩 (e.g., relevant character cards)
    MEDIUM: 按需包含，可压缩 (e.g., world cards, timeline)
    LOW: 可选包含，可大量压缩或省略 (e.g., distant history)
    """

    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class ContextType(str, Enum):
    """上下文类型分类"""

    # 指导性上下文 / Guiding Context
    SYSTEM_PROMPT = "system_prompt"
    TASK_INSTRUCTION = "task_instruction"
    OUTPUT_SCHEMA = "output_schema"
    STYLE_CARD = "style_card"

    # 信息性上下文 / Informational Context
    CHARACTER_CARD = "character_card"
    WORLD_CARD = "world_card"
    FACT = "fact"
    TIMELINE_EVENT = "timeline_event"
    CHAPTER_SUMMARY = "chapter_summary"
    SCENE_BRIEF = "scene_brief"
    DRAFT = "draft"
    TEXT_CHUNK = "text_chunk"

    # 行动性上下文 / Actionable Context
    TOOL_DEFINITION = "tool_definition"
    TOOL_TRACE = "tool_trace"


class DegradationType(str, Enum):
    """上下文退化类型"""

    POISONING = "poisoning"  # 污染：幻觉进入上下文
    DISTRACTION = "distraction"  # 干扰：上下文溢出导致降智
    CONFUSION = "confusion"  # 混淆：冗余/不相关信息
    CLASH = "clash"  # 冲突：信息自相矛盾


@dataclass
class ContextItem:
    """
    单个上下文项
    A single context item with metadata for intelligent management
    """

    id: str
    type: ContextType
    content: str
    priority: ContextPriority = ContextPriority.MEDIUM
    relevance_score: float = 1.0
    token_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Auto-calculate token count if not provided"""
        if self.token_count == 0 and self.content:
            self.token_count = estimate_tokens(self.content)

    def compressed(self, ratio: float = 0.5, query: Optional[str] = None) -> "ContextItem":
        """
        返回压缩版本
        Return a compressed version of this item

        使用智能压缩，保留关键信息
        """
        if ratio >= 1.0:
            return self

        from app.context_engine.smart_compressor import smart_compress

        compressed_content, stats = smart_compress(
            self.content,
            target_ratio=ratio,
            query=query,
            preserve_structure=True,
        )

        if not stats.get("compressed"):
            return self

        return ContextItem(
            id=self.id,
            type=self.type,
            content=compressed_content,
            priority=self.priority,
            relevance_score=self.relevance_score,
            token_count=estimate_tokens(compressed_content),
            metadata={
                **self.metadata,
                "compressed": True,
                "compression": stats.get("method", "smart_compress"),
                "original_tokens": self.token_count,
                "compression_ratio": stats.get("ratio", ratio),
            },
            created_at=self.created_at,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "id": self.id,
            "type": self.type.value,
            "content": self.content,
            "priority": self.priority.value,
            "relevance_score": self.relevance_score,
            "token_count": self.token_count,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ToolDefinition:
    """
    工具定义 - 告诉 Agent 能做什么
    Tool definition - tells the Agent what actions are available
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def to_function_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format"""
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def to_context_string(self) -> str:
        """Convert to string for context inclusion"""
        return f"**{self.name}**: {self.description}"


@dataclass
class ToolTrace:
    """
    工具调用追踪 - 记录做过什么
    Tool call trace - records what has been done
    """

    tool_name: str
    arguments: Dict[str, Any]
    result: Any
    success: bool
    timestamp: float
    duration_ms: int = 0
    error: Optional[str] = None

    def to_context_string(self) -> str:
        """Convert to string for context inclusion"""
        status = "✓" if self.success else "✗"
        result_preview = str(self.result)[:100] if self.result else ""
        return f"{status} {self.tool_name}({self.arguments}) → {result_preview}"


@dataclass
class HealthCheckResult:
    """
    上下文健康检查结果
    Result of context health check
    """

    healthy: bool = True
    issues: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    token_usage: Dict[str, int] = field(default_factory=dict)
    degradation_risks: List[DegradationType] = field(default_factory=list)


@dataclass
class AssembledContext:
    """
    组装完成的上下文
    Fully assembled context ready for LLM call
    """

    system: str  # 指导性上下文
    informational: str  # 信息性上下文
    actionable: str  # 行动性上下文
    health: HealthCheckResult  # 健康检查结果
    items: List[ContextItem]  # 原始上下文项（用于追踪）

    @property
    def total_tokens(self) -> int:
        return sum(item.token_count for item in self.items)

    def to_messages(self) -> List[Dict[str, str]]:
        """Convert to LLM message format"""
        messages = [{"role": "system", "content": self.system}]

        if self.informational:
            messages.append({"role": "user", "content": f"## 参考信息\n{self.informational}"})

        if self.actionable:
            messages.append({"role": "user", "content": f"## 可用工具\n{self.actionable}"})

        return messages


def estimate_tokens(text: str) -> int:
    """
    估算文本的 Token 数量
    Estimate token count for text

    使用 token_counter 模块的精确计数
    """
    from app.context_engine.token_counter import count_tokens

    return count_tokens(text)


def count_tokens_accurate(text: str) -> int:
    """
    更精确的 Token 计数
    More accurate token counting
    """
    from app.context_engine.token_counter import count_tokens

    return count_tokens(text)
