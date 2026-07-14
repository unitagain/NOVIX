"""
Multi-Agent System / 多智能体系统
Agents for novel writing: Archivist, Writer, Editor
小说写作智能体：档案员、主笔、编辑
"""

from .base import BaseAgent
from .archivist import ArchivistAgent
from .writer import WriterAgent
from .editor import EditorAgent
from .agent_task import AgentTask, AgentTaskKind, AgentTaskRunner, AgentTaskStatus, MergePolicy
from .runtime_result import AgentRunResult, AgentRunStatus

__all__ = [
    "BaseAgent",
    "ArchivistAgent",
    "WriterAgent",
    "EditorAgent",
    "AgentTask",
    "AgentTaskKind",
    "AgentTaskRunner",
    "AgentTaskStatus",
    "MergePolicy",
    "AgentRunResult",
    "AgentRunStatus",
]
