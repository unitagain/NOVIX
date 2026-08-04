"""
Trace System / 追踪系统
Records and streams agent execution events for visualization
记录并推送 Agent 执行事件供可视化
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
from datetime import datetime
import asyncio
import json
import uuid
from app.utils.logger import get_logger
from app.error_contract import record_degradation

logger = get_logger(__name__)


def _json_safe_trace_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """把事件数据投影为 JSON-safe：剔除内部 `_` 前缀键（如 gateway 注入的 `_deadline`
    RequestDeadline 对象），并对残余非序列化值兜底为 str，避免订阅者 `to_json()` 崩溃。"""

    def _strip_private(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
        if isinstance(value, (list, tuple)):
            return [_strip_private(item) for item in value]
        return value

    try:
        return json.loads(json.dumps(_strip_private(data), ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"_trace_serialization": "unavailable"}


class TraceEventType(str, Enum):
    """追踪事件类型"""

    # Agent 生命周期
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"

    # 上下文工程
    CONTEXT_SELECT = "context_select"
    CONTEXT_PLAN = "context_plan"
    CONTEXT_COMPRESS = "context_compress"
    CONTEXT_ASSEMBLE = "context_assemble"
    CONTEXT_HEALTH_CHECK = "context_health_check"

    # 工具调用
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # LLM 交互
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    PROVIDER_RETRY = "provider_retry"
    PROVIDER_FAILURE = "provider_failure"
    EGRESS = "egress"
    JOB_STATE = "job_state"

    # 写入操作
    WRITE_MEMORY = "write_memory"
    WRITE_FILE = "write_file"

    # Agent 协作
    HANDOFF = "handoff"
    AGENT_TASK = "agent_task"

    # Diff 变更
    DIFF_GENERATED = "diff_generated"


@dataclass
class TraceEvent:
    """单个追踪事件"""

    id: str
    type: TraceEventType
    agent_name: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    parent_id: Optional[str] = None  # 用于事件嵌套
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    otel_trace_id: Optional[str] = None
    otel_span_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "parent_id": self.parent_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "otel_trace_id": self.otel_trace_id,
            "otel_span_id": self.otel_span_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class AgentTrace:
    """单个 Agent 的完整追踪记录"""

    agent_name: str
    session_id: str
    start_time: float
    trace_id: Optional[str] = None
    end_time: Optional[float] = None
    status: str = "running"
    events: List[TraceEvent] = field(default_factory=list)
    context_stats: Dict[str, Any] = field(
        default_factory=lambda: {"token_usage": 0, "selected_items": 0, "input_tokens": 0, "output_tokens": 0}
    )

    def add_event(self, event: TraceEvent):
        self.events.append(event)

        # Incrementally update stats based on event type
        if event.type == TraceEventType.LLM_REQUEST:
            usage = event.data.get("tokens", {})
            total = usage.get("total", 0)
            self.context_stats["token_usage"] += total
            self.context_stats["input_tokens"] += usage.get("prompt", 0)
            self.context_stats["output_tokens"] += usage.get("completion", 0)

        elif event.type == TraceEventType.CONTEXT_SELECT:
            self.context_stats["selected_items"] += event.data.get("selected", 0)
            # Context select tokens are usually input tokens
            self.context_stats["token_usage"] += event.data.get("tokens", 0)
            self.context_stats["input_tokens"] += event.data.get("tokens", 0)

        elif event.type == TraceEventType.CONTEXT_COMPRESS:
            # Compression means negative tokens (saving)
            saved = event.data.get("saved", 0)
            self.context_stats["token_usage"] -= saved
            self.context_stats["input_tokens"] -= saved

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "duration_ms": int((self.end_time - self.start_time) * 1000) if self.end_time else 0,
            "event_count": len(self.events),
            "events": [e.to_dict() for e in self.events],
            "context_stats": self.context_stats,
        }


class TraceCollector:
    """
    追踪收集器

    核心职责：
    1. 收集所有 Agent 执行事件
    2. 维护事件历史
    3. 推送实时更新给前端
    """

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self.events: List[TraceEvent] = []
        self.agent_traces: Dict[str, AgentTrace] = {}
        self.subscribers: List[Callable] = []
        self._event_counter = 0
        self._span_counter = 0
        self._trace_id = f"trace_{uuid.uuid4().hex}"
        self._span_by_event_id: Dict[str, str] = {}
        self._lock = asyncio.Lock()

        # Global stats state / 全局统计状态
        self.current_stats = {
            "token_usage": {"total": 0, "max": 16000, "breakdown": {"guiding": 0, "informational": 0, "actionable": 0}},
            "health": {"healthy": True, "issues": []},
        }

    def _generate_id(self) -> str:
        """生成事件 ID"""
        self._event_counter += 1
        return f"evt_{self._event_counter:06d}"

    def _generate_span_id(self) -> str:
        """Generate an OTel-like span id for event correlation."""

        self._span_counter += 1
        return f"{self._span_counter:016x}"

    @staticmethod
    def _scope_trace_id() -> Optional[str]:
        from app.context_engine.turn_scope import current_turn_scope

        scope = current_turn_scope()
        return scope.trace_id if scope is not None else None

    def _agent_trace_key(self, agent_name: str, trace_id: Optional[str] = None) -> str:
        return f"{trace_id or self._trace_id}:{agent_name}"

    async def record(
        self, event_type: TraceEventType, agent_name: str, data: Dict[str, Any] = None, parent_id: str = None
    ) -> TraceEvent:
        """
        记录追踪事件

        Args:
            event_type: 事件类型
            agent_name: Agent 名称
            data: 事件数据
            parent_id: 父事件 ID（用于嵌套）

        Returns:
            创建的事件
        """
        from app.context_engine.turn_scope import current_turn_scope

        turn_scope = current_turn_scope()
        async with self._lock:
            event_id = self._generate_id()
            span_id = self._generate_span_id()
            parent_span_id = self._span_by_event_id.get(parent_id) if parent_id else None
            event = TraceEvent(
                id=event_id,
                type=event_type,
                agent_name=agent_name,
                timestamp=datetime.now().timestamp(),
                data=_json_safe_trace_data(data or {}),
                parent_id=parent_id,
                trace_id=turn_scope.trace_id if turn_scope is not None else self._trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
            self._span_by_event_id[event_id] = span_id

            try:
                from app.observability.otel import telemetry

                otel_ids = telemetry.record_event(event)
                event.otel_trace_id = otel_ids.get("trace_id")
                event.otel_span_id = otel_ids.get("span_id")
            except Exception as exc:
                from app.observability.runtime_metrics import runtime_metrics

                runtime_metrics.increment("telemetry.dropped_spans")
                logger.warning("OTel event span dropped: %s", exc)

            self.events.append(event)
            if turn_scope is not None:
                turn_scope.trace_events.append(event)

            # 限制历史数量
            if len(self.events) > self.max_history:
                self.events = self.events[-self.max_history :]

            # 更新 Agent 追踪
            trace_key = self._agent_trace_key(agent_name, event.trace_id)
            if trace_key in self.agent_traces:
                self.agent_traces[trace_key].add_event(event)

            # 通知订阅者
            await self._notify_subscribers(event)

            try:
                from app.observability.runtime_metrics import runtime_metrics

                runtime_metrics.increment(f"trace.events.{event.type.value}")
                if event.duration_ms:
                    runtime_metrics.observe(f"trace.duration_ms.{event.type.value}", event.duration_ms)
            except Exception as exc:
                record_degradation("trace_runtime_metrics", exc)

            return event

    async def save_otel(self, path: str, *, events: Optional[List[TraceEvent]] = None) -> bool:
        """Export trace events using an OTLP JSON-compatible span shape."""
        try:
            from pathlib import Path
            from app.observability.runtime_metrics import export_otel_json

            export_otel_json(Path(path), events if events is not None else self.events)
            return True
        except Exception as exc:
            logger.warning("OTel trace export failed: %s", exc)
            return False

    async def start_agent_trace(self, agent_name: str, session_id: str) -> AgentTrace:
        """开始 Agent 追踪"""
        trace_id = self._scope_trace_id() or self._trace_id
        trace = AgentTrace(
            agent_name=agent_name,
            session_id=session_id,
            start_time=datetime.now().timestamp(),
            trace_id=trace_id,
        )
        self.agent_traces[self._agent_trace_key(agent_name, trace_id)] = trace

        await self.record(TraceEventType.AGENT_START, agent_name, {"session_id": session_id})

        return trace

    async def end_agent_trace(self, agent_name: str, status: str = "completed", context_stats: Dict[str, Any] = None):
        """结束 Agent 追踪"""
        key = self._agent_trace_key(agent_name, self._scope_trace_id() or self._trace_id)
        if key in self.agent_traces:
            trace = self.agent_traces[key]
            trace.end_time = datetime.now().timestamp()
            trace.status = status
            if context_stats:
                trace.context_stats = context_stats

            await self.record(
                TraceEventType.AGENT_END,
                agent_name,
                {
                    "status": status,
                    "duration_ms": int((trace.end_time - trace.start_time) * 1000),
                    "context_stats": context_stats or {},
                },
            )

    # ========== 便捷记录方法 ==========

    async def record_context_select(
        self, agent_name: str, selected_count: int, total_candidates: int, token_usage: int
    ):
        """记录上下文选取"""
        await self.record(
            TraceEventType.CONTEXT_SELECT,
            agent_name,
            {
                "selected": selected_count,
                "candidates": total_candidates,
                "tokens": token_usage,
                "ratio": f"{selected_count}/{total_candidates}",
            },
        )

        # Update global stats
        # Context select is mostly "Informational" load
        await self.update_token_stats(total_delta=token_usage, breakdown_delta={"informational": token_usage})

    async def record_context_plan(self, agent_name: str, context_plan: Dict[str, Any]):
        """记录单轮 ContextPlan，供 trace/eval 复用。"""
        data = dict(context_plan or {})
        return await self.record(TraceEventType.CONTEXT_PLAN, agent_name, data)

    async def record_context_compress(self, agent_name: str, before_tokens: int, after_tokens: int, method: str):
        """记录上下文压缩"""
        await self.record(
            TraceEventType.CONTEXT_COMPRESS,
            agent_name,
            {
                "before": before_tokens,
                "after": after_tokens,
                "saved": before_tokens - after_tokens,
                "ratio": f"{after_tokens/before_tokens:.1%}" if before_tokens > 0 else "0%",
                "method": method,
            },
        )

        # Update global stats (Compression reduces totals)
        reduction = after_tokens - before_tokens
        # Assume compression affects "Informational" mostly
        await self.update_token_stats(total_delta=reduction, breakdown_delta={"informational": reduction})

    async def record_health_check(self, agent_name: str, healthy: bool, issues: List[str], token_usage_ratio: float):
        """记录健康检查"""
        await self.record(
            TraceEventType.CONTEXT_HEALTH_CHECK,
            agent_name,
            {"healthy": healthy, "issues": issues, "token_usage": f"{token_usage_ratio:.1%}"},
        )

    async def record_tool_call(self, agent_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """记录工具调用"""
        event = await self.record(TraceEventType.TOOL_CALL, agent_name, {"tool": tool_name, "args": arguments})
        return event.id

    async def record_tool_result(
        self, agent_name: str, tool_name: str, success: bool, result: Any, parent_id: str = None
    ):
        """记录工具结果"""
        await self.record(
            TraceEventType.TOOL_RESULT,
            agent_name,
            {"tool": tool_name, "success": success, "result": str(result)[:200]},
            parent_id=parent_id,
        )

    async def record_handoff(self, from_agent: str, to_agent: str, summary: str):
        """记录 Agent 间交接"""
        await self.record(TraceEventType.HANDOFF, from_agent, {"to": to_agent, "summary": summary[:200]})

    async def record_agent_task(self, agent_name: str, task: Dict[str, Any]):
        """记录隔离 worker/AgentTask 的输入输出摘要。"""
        await self.record(TraceEventType.AGENT_TASK, agent_name, dict(task or {}))

    async def record_diff(self, agent_name: str, additions: int, deletions: int, file_ref: str = None):
        """记录 Diff 变更"""
        await self.record(
            TraceEventType.DIFF_GENERATED,
            agent_name,
            {"additions": additions, "deletions": deletions, "file_ref": file_ref},
        )

    # ========== 统计更新 ==========

    async def update_token_stats(self, total_delta: int, breakdown_delta: Dict[str, int] = None):
        """Update global token stats"""
        async with self._lock:
            # Update total
            self.current_stats["token_usage"]["total"] += total_delta

            # Update breakdown
            if breakdown_delta:
                for key, val in breakdown_delta.items():
                    if key in self.current_stats["token_usage"]["breakdown"]:
                        self.current_stats["token_usage"]["breakdown"][key] += val

            # Simple health check logic
            usage_ratio = self.current_stats["token_usage"]["total"] / self.current_stats["token_usage"]["max"]
            self.current_stats["health"]["healthy"] = usage_ratio < 0.9

            if usage_ratio >= 0.9:
                if "High Token Load" not in [i["type"] for i in self.current_stats["health"]["issues"]]:
                    self.current_stats["health"]["issues"].append(
                        {"type": "High Token Load", "message": "Token usage is approaching limit."}
                    )

    def get_current_stats(self) -> Dict[str, Any]:
        """Get current global stats"""
        return self.current_stats

    # ========== 订阅系统 ==========

    def subscribe(self, callback: Callable):
        """订阅事件更新"""
        self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        """取消订阅"""
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    async def _notify_subscribers(self, event: TraceEvent):
        """通知所有订阅者"""
        for subscriber in self.subscribers:
            try:
                if asyncio.iscoroutinefunction(subscriber):
                    await subscriber(event)
                else:
                    subscriber(event)
            except Exception as e:
                logger.warning("Subscriber error: %s", e)

    # ========== 查询方法 ==========

    def get_recent_events(self, count: int = 50) -> List[Dict]:
        """获取最近的事件"""
        return [e.to_dict() for e in self.events[-count:]]

    def event_count(self) -> int:
        """Return current event count for per-turn metric slicing."""
        return len(self.events)

    def summarize_events_since(self, index: int, *, started_at: float = None) -> Dict[str, Any]:
        """Aggregate token, latency, and trajectory metrics since an event index."""
        start = max(0, int(index or 0))
        events = self.events[start:]
        return self.summarize_events(events, started_at=started_at)

    def summarize_turn(self, scope: Any) -> Dict[str, Any]:
        """Aggregate events owned by one explicit TurnScope."""

        return self.summarize_events(
            list(getattr(scope, "trace_events", []) or []),
            started_at=float(getattr(scope, "started_at", 0.0) or 0.0),
        )

    @staticmethod
    def summarize_events(events: List[TraceEvent], *, started_at: float = None) -> Dict[str, Any]:
        """Aggregate a caller-owned event collection."""

        llm_request_tokens = 0
        llm_response_tokens = 0
        context_select_tokens = 0
        llm_latency_ms = 0
        for event in events:
            if event.type == TraceEventType.LLM_REQUEST:
                usage = event.data.get("tokens", {}) if isinstance(event.data, dict) else {}
                llm_request_tokens += int(usage.get("total") or usage.get("prompt") or 0)
                llm_latency_ms += int(event.data.get("latency_ms") or 0)
            elif event.type == TraceEventType.LLM_RESPONSE:
                usage = event.data.get("usage", {}) if isinstance(event.data, dict) else {}
                llm_response_tokens += int(
                    usage.get("total_tokens") or usage.get("total") or usage.get("prompt_tokens") or 0
                )
                llm_latency_ms += int(event.data.get("latency_ms") or 0)
            elif event.type == TraceEventType.CONTEXT_SELECT:
                context_select_tokens += int(event.data.get("tokens") or 0)
        llm_tokens = llm_response_tokens if llm_response_tokens > 0 else llm_request_tokens
        elapsed_ms = 0
        if started_at:
            elapsed_ms = int((datetime.now().timestamp() - float(started_at)) * 1000)
        return {
            "event_count": len(events),
            "tool_calls": sum(1 for event in events if event.type == TraceEventType.TOOL_CALL),
            "tool_results": sum(1 for event in events if event.type == TraceEventType.TOOL_RESULT),
            "llm_requests": sum(1 for event in events if event.type == TraceEventType.LLM_REQUEST),
            "llm_responses": sum(1 for event in events if event.type == TraceEventType.LLM_RESPONSE),
            "context_selects": sum(1 for event in events if event.type == TraceEventType.CONTEXT_SELECT),
            "tokens": llm_tokens + context_select_tokens,
            "llm_tokens": llm_tokens,
            "context_select_tokens": context_select_tokens,
            "llm_latency_ms": llm_latency_ms,
            "elapsed_ms": elapsed_ms,
        }

    def get_agent_trace(self, agent_name: str) -> Optional[Dict]:
        """获取 Agent 追踪"""
        key = self._agent_trace_key(agent_name, self._scope_trace_id() or self._trace_id)
        if key in self.agent_traces:
            return self.agent_traces[key].to_dict()
        matches = [trace for trace in self.agent_traces.values() if trace.agent_name == agent_name]
        if matches:
            return max(matches, key=lambda trace: trace.start_time).to_dict()
        return None

    def get_all_traces(self, trace_id: Optional[str] = None) -> List[Dict]:
        """获取所有 Agent 追踪"""
        return [
            trace.to_dict()
            for trace in self.agent_traces.values()
            if trace_id is None or trace.trace_id == trace_id
        ]

    def get_timeline(self, session_id: str = None) -> List[Dict]:
        """
        获取时间线视图

        返回按时间排序的事件，适合 Timeline 组件展示
        """
        events = self.events

        if session_id:
            events = [e for e in events if e.data.get("session_id") == session_id]

        return sorted([e.to_dict() for e in events], key=lambda x: x["timestamp"])

    async def save_trace(self, file_path, *, count: int = 2000, events: Optional[List[TraceEvent]] = None) -> bool:
        """Phase 15：把当前 trace（事件 + agent 追踪）落盘为 JSON，供前端展开 / eval 复用。

        best-effort：失败返回 False，不抛（trace 落盘不得影响主流程）。
        """
        try:
            from pathlib import Path
            import aiofiles

            source_events = list(events) if events is not None else self.events
            trace_ids = {event.trace_id for event in source_events if event.trace_id}
            trace_id = next(iter(trace_ids)) if len(trace_ids) == 1 else None
            payload = json.dumps(
                {
                    "events": [event.to_dict() for event in source_events[-max(1, int(count or 1)) :]],
                    "agent_traces": self.get_all_traces(trace_id=trace_id),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(payload)
            return True
        except Exception as exc:
            logger.warning("Save trace failed: %s", exc)
            return False


# 全局追踪收集器实例
trace_collector = TraceCollector()
