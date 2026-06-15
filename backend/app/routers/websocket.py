"""
中文说明：该模块为 WenShape 后端组成部分，详细行为见下方英文说明。

WebSocket Router
Real-time progress updates for writing sessions.
"""

import json
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage WebSocket connections by project."""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, project_id: str):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = set()
        self.active_connections[project_id].add(websocket)

    def disconnect(self, websocket: WebSocket, project_id: str):
        if project_id in self.active_connections:
            self.active_connections[project_id].discard(websocket)
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]

    async def broadcast(self, project_id: str, message: dict):
        if project_id not in self.active_connections:
            return

        json_message = json.dumps(message, ensure_ascii=False)
        disconnected = set()
        for connection in self.active_connections[project_id]:
            try:
                await connection.send_text(json_message)
            except Exception:
                disconnected.add(connection)

        for connection in disconnected:
            self.disconnect(connection, project_id)


class TraceConnectionManager:
    """Manage WebSocket connections for trace events."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return

        json_message = json.dumps(message, ensure_ascii=False)
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(json_message)
            except Exception:
                disconnected.add(connection)

        for connection in disconnected:
            self.disconnect(connection)


manager = ConnectionManager()
trace_manager = TraceConnectionManager()


@router.websocket("/ws/trace")
async def trace_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for trace events."""
    await trace_manager.connect(websocket)

    from app.context_engine.trace_collector import trace_collector, TraceEvent

    async def on_trace_event(event: TraceEvent):
        await trace_manager.broadcast(
            {
                "type": "trace_event",
                "payload": event.to_dict(),
            }
        )

        if event.type in ["llm_request", "context_select", "context_compress", "context_health_check"]:
            stats = trace_collector.get_current_stats()
            await trace_manager.broadcast(
                {
                    "type": "context_stats_update",
                    "payload": stats,
                }
            )

    trace_collector.subscribe(on_trace_event)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "message": "Connected to WenShape Trace System",
            }
        )

        for trace in trace_collector.get_all_traces():
            await websocket.send_json(
                {
                    "type": "agent_trace_update",
                    "payload": trace,
                }
            )

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        trace_manager.disconnect(websocket)
        trace_collector.unsubscribe(on_trace_event)
    except Exception as exc:
        logger.error("Trace WebSocket error: %s", exc, exc_info=True)
        trace_manager.disconnect(websocket)
        trace_collector.unsubscribe(on_trace_event)


@router.websocket("/ws/{project_id}/session")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """WebSocket endpoint for session progress updates."""
    await manager.connect(websocket, project_id)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "message": "Connected to WenShape session updates",
                "project_id": project_id,
            }
        )

        while True:
            data = await websocket.receive_text()
            await websocket.send_json(
                {
                    "type": "pong",
                    "timestamp": data,
                }
            )

    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc, exc_info=True)
        manager.disconnect(websocket, project_id)


async def broadcast_progress(project_id: str, message: dict):
    """Broadcast progress update to all clients of a project."""
    await manager.broadcast(project_id, message)


def get_connection_manager() -> ConnectionManager:
    """Return the global connection manager."""
    return manager
