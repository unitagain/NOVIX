"""
WebSocket Router / WebSocket 路由
Real-time progress updates for writing sessions
写作会话的实时进度更新
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set
import asyncio
import json

router = APIRouter(tags=["websocket"])

# Active WebSocket connections / 活跃的 WebSocket 连接
active_connections: Dict[str, Set[WebSocket]] = {}


class ConnectionManager:
    """Manages WebSocket connections / 管理 WebSocket 连接"""
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, project_id: str):
        """
        Connect a WebSocket client
        连接 WebSocket 客户端
        
        Args:
            websocket: WebSocket connection / WebSocket 连接
            project_id: Project ID / 项目ID
        """
        await websocket.accept()
        
        if project_id not in self.active_connections:
            self.active_connections[project_id] = set()
        
        self.active_connections[project_id].add(websocket)
    
    def disconnect(self, websocket: WebSocket, project_id: str):
        """
        Disconnect a WebSocket client
        断开 WebSocket 客户端
        
        Args:
            websocket: WebSocket connection / WebSocket 连接
            project_id: Project ID / 项目ID
        """
        if project_id in self.active_connections:
            self.active_connections[project_id].discard(websocket)
            
            # Remove empty sets / 移除空集合
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]
    
    async def broadcast(self, project_id: str, message: dict):
        """
        Broadcast message to all clients for a project
        向项目的所有客户端广播消息
        
        Args:
            project_id: Project ID / 项目ID
            message: Message to broadcast / 要广播的消息
        """
        if project_id not in self.active_connections:
            return
        
        # Convert message to JSON / 将消息转换为 JSON
        json_message = json.dumps(message, ensure_ascii=False)
        
        # Send to all connected clients / 发送到所有连接的客户端
        disconnected = set()
        for connection in self.active_connections[project_id]:
            try:
                await connection.send_text(json_message)
            except Exception:
                # Mark for removal if sending fails / 如果发送失败则标记为移除
                disconnected.add(connection)
        
        # Remove disconnected clients / 移除断开的客户端
        for connection in disconnected:
            self.disconnect(connection, project_id)


# Global connection manager / 全局连接管理器
manager = ConnectionManager()


@router.websocket("/ws/{project_id}/session")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for session progress updates
    会话进度更新的 WebSocket 端点
    
    Args:
        websocket: WebSocket connection / WebSocket 连接
        project_id: Project ID / 项目ID
    """
    await manager.connect(websocket, project_id)
    
    try:
        # Send initial connection message / 发送初始连接消息
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to NOVIX session updates",
            "project_id": project_id
        })
        
        # Keep connection alive / 保持连接活跃
        while True:
            # Wait for messages from client / 等待客户端消息
            data = await websocket.receive_text()
            
            # Echo back (for heartbeat) / 回显（用于心跳）
            await websocket.send_json({
                "type": "pong",
                "timestamp": data
            })
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id)
    except Exception as e:
        print(f"[WebSocket] Error: {e}")
        manager.disconnect(websocket, project_id)


async def broadcast_progress(project_id: str, message: dict):
    """
    Broadcast progress update to all clients
    向所有客户端广播进度更新
    
    Args:
        project_id: Project ID / 项目ID
        message: Progress message / 进度消息
    """
    await manager.broadcast(project_id, message)


def get_connection_manager() -> ConnectionManager:
    """
    Get global connection manager
    获取全局连接管理器
    
    Returns:
        Connection manager instance / 连接管理器实例
    """
    return manager
