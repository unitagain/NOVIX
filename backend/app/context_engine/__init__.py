"""
Context Engine Module / 上下文引擎模块
Manages context selection, compression, budgeting, and conflict detection
管理上下文选取、压缩、预算控制和冲突检测
"""

# Active exports used by the current codebase.
from .models import (
    ContextItem,
    ContextPriority,
    ContextType,
    DegradationType,
    ToolDefinition,
    ToolTrace,
    HealthCheckResult,
    AssembledContext,
    estimate_tokens,
    count_tokens_accurate,
)
from .select_engine import ContextSelectEngine
from .trace_collector import trace_collector, TraceEvent, TraceEventType

__all__ = [
    # 数据模型
    "ContextItem",
    "ContextPriority",
    "ContextType",
    "DegradationType",
    "ToolDefinition",
    "ToolTrace",
    "HealthCheckResult",
    "AssembledContext",
    "estimate_tokens",
    "count_tokens_accurate",
    "ContextSelectEngine",
    # Trace
    "trace_collector",
    "TraceEvent",
    "TraceEventType",
]
