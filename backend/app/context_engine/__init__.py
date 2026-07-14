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
from .context_plan import ContextPlanV2, build_context_plan_v2
from .turn_scope import TurnScope, bind_turn_scope, current_turn_scope, ensure_turn_scope, new_turn_scope
from .context_assembly import ContextAssemblyPlan, build_context_assembly_plan
from .contextual_prefix import build_contextual_prefix, ensure_contextual_prefix, prefix_coverage
from .procedural_knowledge import ProceduralSkill, plan_skill_loadout
from .reranker import OnnxCrossEncoderReranker, RerankerBackend, create_reranker_backend
from .trace_collector import trace_collector, TraceEvent, TraceEventType
from .tool_registry import ToolSpec, list_tool_specs, tool_loadout_for_route, tool_loadout_summary
from .compact_artifact import CompactArtifactV2, CompactVerifier
from .memory_record import MemoryGraphState, MemoryRecordV2, build_memory_graph, normalize_memory_status
from .source_snapshot import SourceDescriptor, SourceRegistry
from .token_accounting import (
    TokenAccounting,
    count_provider_payload,
    count_text_tokens,
    token_estimator_calibration,
)
from .tool_artifact import ToolArtifactStore, ToolExecutionResult, ToolExecutionStatus

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
    "ContextPlanV2",
    "build_context_plan_v2",
    "TurnScope",
    "bind_turn_scope",
    "current_turn_scope",
    "ensure_turn_scope",
    "new_turn_scope",
    "ContextAssemblyPlan",
    "build_context_assembly_plan",
    "build_contextual_prefix",
    "ensure_contextual_prefix",
    "prefix_coverage",
    "ProceduralSkill",
    "plan_skill_loadout",
    "RerankerBackend",
    "OnnxCrossEncoderReranker",
    "create_reranker_backend",
    "ToolSpec",
    "list_tool_specs",
    "tool_loadout_for_route",
    "tool_loadout_summary",
    "CompactArtifactV2",
    "CompactVerifier",
    "MemoryRecordV2",
    "MemoryGraphState",
    "build_memory_graph",
    "normalize_memory_status",
    "SourceDescriptor",
    "SourceRegistry",
    "TokenAccounting",
    "count_provider_payload",
    "count_text_tokens",
    "token_estimator_calibration",
    "ToolArtifactStore",
    "ToolExecutionResult",
    "ToolExecutionStatus",
    # Trace
    "trace_collector",
    "TraceEvent",
    "TraceEventType",
]
