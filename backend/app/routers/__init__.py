"""
API Routers / API 路由
"""

from .projects import router as projects_router
from .cards import router as cards_router
from .canon import router as canon_router
from .facts import router as facts_router
from .drafts import router as drafts_router
from .session import router as session_router
from .config_llm import router as config_router
from .proxy import router as proxy_router
from .text_chunks import router as text_chunks_router
from .evidence import router as evidence_router
from .bindings import router as bindings_router
from .memory_pack import router as memory_pack_router
from .memory import router as memory_router
from .actions import router as actions_router
from .export import router as export_router
from .outline import router as outline_router

__all__ = [
    "projects_router",
    "cards_router",
    "canon_router",
    "facts_router",
    "drafts_router",
    "session_router",
    "config_router",
    "proxy_router",
    "text_chunks_router",
    "evidence_router",
    "bindings_router",
    "memory_pack_router",
    "memory_router",
    "actions_router",
    "export_router",
    "outline_router",
]
