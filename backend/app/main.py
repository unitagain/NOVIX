"""
NOVIX FastAPI Application Entry Point
FastAPI 应用入口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import (
    projects_router,
    cards_router,
    canon_router,
    drafts_router,
    session_router,
    config_router
)
from app.routers.websocket import router as websocket_router

# Create FastAPI application / 创建 FastAPI 应用
app = FastAPI(
    title="NOVIX API",
    description="Multi-Agent Novel Writing System / 多智能体小说写作系统",
    version="0.1.0",
    debug=settings.debug
)

# Configure CORS / 配置跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure properly for production / 生产环境需要配置具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers / 注册路由
app.include_router(projects_router)
app.include_router(cards_router)
app.include_router(canon_router)
app.include_router(drafts_router)
app.include_router(session_router)
app.include_router(config_router)
app.include_router(websocket_router)


@app.get("/")
async def root():
    """Root endpoint / 根路径"""
    return {
        "message": "NOVIX API is running",
        "version": "0.1.0",
        "status": "healthy",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint / 健康检查"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
