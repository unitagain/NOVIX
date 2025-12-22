"""
Session Router / 会话路由
Writing session management endpoints
写作会话管理端点
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from app.orchestrator import Orchestrator
from app.orchestrator.orchestrator import SessionStatus
from app.routers.websocket import broadcast_progress

router = APIRouter(prefix="/projects/{project_id}/session", tags=["session"])

# Global orchestrator instance / 全局调度器实例
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get or create orchestrator instance / 获取或创建调度器实例"""
    global _orchestrator

    async def _progress_callback(payload: dict) -> None:
        """Broadcast session progress to WebSocket / 将会话进度广播到 WebSocket"""
        project = payload.get("project_id")
        if not project:
            return
        await broadcast_progress(project, payload)

    if _orchestrator is None:
        _orchestrator = Orchestrator(progress_callback=_progress_callback)
    else:
        # Ensure progress callback is always set / 确保进度回调始终设置
        _orchestrator.progress_callback = _progress_callback
    return _orchestrator


class StartSessionRequest(BaseModel):
    """Request to start a writing session / 开始写作会话的请求"""
    chapter: str = Field(..., description="Chapter ID / 章节ID")
    chapter_title: str = Field(..., description="Chapter title / 章节标题")
    chapter_goal: str = Field(..., description="Chapter goal / 章节目标")
    target_word_count: int = Field(3000, description="Target word count / 目标字数")
    character_names: Optional[List[str]] = Field(None, description="Character names / 角色名称列表")


class FeedbackRequest(BaseModel):
    """Request to submit feedback / 提交反馈的请求"""
    chapter: str = Field(..., description="Chapter ID / 章节ID")
    feedback: str = Field(..., description="User feedback / 用户反馈")
    action: str = Field("revise", description="Action: 'revise' or 'confirm' / 动作")


@router.post("/start")
async def start_session(
    project_id: str,
    request: StartSessionRequest
):
    """
    Start a new writing session
    开始新的写作会话
    
    Args:
        project_id: Project ID / 项目ID
        request: Session request / 会话请求
        
    Returns:
        Session result / 会话结果
    """
    try:
        orchestrator = get_orchestrator()
        
        result = await orchestrator.start_session(
            project_id=project_id,
            chapter=request.chapter,
            chapter_title=request.chapter_title,
            chapter_goal=request.chapter_goal,
            target_word_count=request.target_word_count,
            character_names=request.character_names
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_session_status(project_id: str):
    """
    Get current session status
    获取当前会话状态
    
    Args:
        project_id: Project ID / 项目ID
        
    Returns:
        Session status / 会话状态
    """
    orchestrator = get_orchestrator()
    status = orchestrator.get_status()
    
    # Check if this is the correct project / 检查是否是正确的项目
    if status["project_id"] != project_id:
        return {
            "status": "idle",
            "message": "No active session for this project"
        }
    
    return status


@router.post("/feedback")
async def submit_feedback(
    project_id: str,
    request: FeedbackRequest
):
    """
    Submit user feedback
    提交用户反馈
    
    Args:
        project_id: Project ID / 项目ID
        request: Feedback request / 反馈请求
        
    Returns:
        Processing result / 处理结果
    """
    try:
        orchestrator = get_orchestrator()
        
        result = await orchestrator.process_feedback(
            project_id=project_id,
            chapter=request.chapter,
            feedback=request.feedback,
            action=request.action
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_session(project_id: str):
    """
    Cancel current session
    取消当前会话
    
    Args:
        project_id: Project ID / 项目ID
        
    Returns:
        Cancellation result / 取消结果
    """
    orchestrator = get_orchestrator()
    
    # Reset orchestrator state / 重置调度器状态
    orchestrator.current_status = SessionStatus.IDLE
    orchestrator.current_project_id = None
    orchestrator.current_chapter = None

    await broadcast_progress(project_id, {
        "status": SessionStatus.IDLE.value,
        "message": "Session cancelled",
        "project_id": project_id,
        "chapter": None,
        "iteration": 0
    })
    
    return {
        "success": True,
        "message": "Session cancelled"
    }
