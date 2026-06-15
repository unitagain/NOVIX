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
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TraceEventType(str, Enum):
    """追踪事件类型"""

    # Agent 生命周期
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"

    # 上下文工程
    CONTEXT_SELECT = "context_select"
    CONTEXT_COMPRESS = "context_compress"
    CONTEXT_ASSEMBLE = "context_assemble"
    CONTEXT_HEALTH_CHECK = "context_health_check"

    # 工具调用
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # LLM 交互
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"

    # 写入操作
    WRITE_MEMORY = "write_memory"
    WRITE_FILE = "write_file"

    # Agent 协作
    HANDOFF = "handoff"

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "parent_id": self.parent_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class AgentTrace:
    """单个 Agent 的完整追踪记录"""

    agent_name: str
    session_id: str
    start_time: float
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
        async with self._lock:
            event = TraceEvent(
                id=self._generate_id(),
                type=event_type,
                agent_name=agent_name,
                timestamp=datetime.now().timestamp(),
                data=data or {},
                parent_id=parent_id,
            )

            self.events.append(event)

            # 限制历史数量
            if len(self.events) > self.max_history:
                self.events = self.events[-self.max_history :]

            # 更新 Agent 追踪
            if agent_name in self.agent_traces:
                self.agent_traces[agent_name].add_event(event)

            # 通知订阅者
            await self._notify_subscribers(event)

            return event

    async def start_agent_trace(self, agent_name: str, session_id: str) -> AgentTrace:
        """开始 Agent 追踪"""
        trace = AgentTrace(agent_name=agent_name, session_id=session_id, start_time=datetime.now().timestamp())
        self.agent_traces[agent_name] = trace

        await self.record(TraceEventType.AGENT_START, agent_name, {"session_id": session_id})

        return trace

    async def end_agent_trace(self, agent_name: str, status: str = "completed", context_stats: Dict[str, Any] = None):
        """结束 Agent 追踪"""
        if agent_name in self.agent_traces:
            trace = self.agent_traces[agent_name]
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

    def get_agent_trace(self, agent_name: str) -> Optional[Dict]:
        """获取 Agent 追踪"""
        if agent_name in self.agent_traces:
            return self.agent_traces[agent_name].to_dict()
        return None

    def get_all_traces(self) -> List[Dict]:
        """获取所有 Agent 追踪"""
        return [t.to_dict() for t in self.agent_traces.values()]

    def get_timeline(self, session_id: str = None) -> List[Dict]:
        """
        获取时间线视图

        返回按时间排序的事件，适合 Timeline 组件展示
        """
        events = self.events

        if session_id:
            events = [e for e in events if e.data.get("session_id") == session_id]

        return sorted([e.to_dict() for e in events], key=lambda x: x["timestamp"])


# 全局追踪收集器实例
trace_collector = TraceCollector()
