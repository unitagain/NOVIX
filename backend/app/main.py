"""
WenShape FastAPI Application Entry Point
FastAPI 应用入口
"""

import os
import sys
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.config import config, settings
from app.utils.logger import get_logger
from app.llm_gateway.errors import LLMError
from app.error_contract import error_envelope, normalize_error_code, status_code_for
from app.security.local_auth import LocalAuthMiddleware
from app.security.headers import SecurityHeadersMiddleware
from app.observability.otel import OpenTelemetryMiddleware
from app.routers import (
    projects_router,
    cards_router,
    canon_router,
    facts_router,
    drafts_router,
    session_router,
    config_router,
    proxy_router,
    text_chunks_router,
    evidence_router,
    bindings_router,
    memory_pack_router,
    memory_router,
    actions_router,
    export_router,
)
from app.routers.fanfiction import router as fanfiction_router
from app.routers.websocket import router as websocket_router
from app.routers.volumes import router as volumes_router

logger = get_logger(__name__)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifespan hooks."""
    from app.jobs.runtime import start_task_worker, stop_task_worker

    from app.observability.otel import telemetry
    from app.control_plane.runtime import get_control_store

    telemetry.configure(
        settings.data_dir,
        exporter_mode=str((config.get("observability", {}) or {}).get("otel_exporter") or "file"),
    )
    get_control_store().recover_abandoned_project_writes()
    await run_startup_tasks()
    await start_task_worker()
    try:
        yield
    finally:
        await stop_task_worker()
        telemetry.force_flush()
        telemetry.shutdown()


# Create FastAPI application / 创建 FastAPI 应用
app = FastAPI(
    title="WenShape API",
    description="Multi-Agent Novel Writing System / 多智能体小说写作系统",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Global exception handler — returns structured error details to clients
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions and return a structured error response."""
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )

    envelope = error_envelope(exc, request_id=request_id)
    payload = envelope.to_dict()
    if isinstance(exc, LLMError):
        payload["provider"] = exc.provider
        payload["reason"] = normalize_error_code(exc.reason, "provider_error")
    return JSONResponse(
        status_code=status_code_for(exc),
        content=payload,
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    errors = [{"type": item.get("type"), "loc": item.get("loc")} for item in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error", "detail": "Request validation failed", "errors": errors, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


# Configure CORS / 配置跨域
# Production-ready CORS configuration
# For Frozen (EXE) mode: Allow all localhost addresses with any port
# This is safe because the app only binds to 127.0.0.1 (loopback)


def is_localhost(origin: str) -> bool:
    """Check if origin is a localhost address (safe for desktop apps)"""
    try:
        return bool(re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", origin))
    except (TypeError, re.error):
        return False


class LoopbackOriginsMatcher:
    """CORS middleware that accepts any localhost origin for desktop apps"""

    def __init__(self):
        self.allow_origins = [
            "http://localhost:3000",  # Dev: Vite dev server
            "http://localhost:8000",  # Prod: Standard port
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
        ]
        # For dynamic ports in Frozen mode, we'll handle in __call__
        self.is_frozen = getattr(sys, "frozen", False)

    def __call__(self, origin: str) -> bool:
        # Hardcoded origins (for non-dynamic setup)
        if origin in self.allow_origins:
            return True
        # Dynamic port support: Allow any localhost address
        if self.is_frozen and is_localhost(origin):
            return True
        return False


loopback_matcher = LoopbackOriginsMatcher()


def configured_cors_origins() -> list[str]:
    configured = [item.strip().rstrip("/") for item in os.getenv("WENSHAPE_DESKTOP_ALLOWED_ORIGINS", "").split(",") if item.strip()]
    if configured:
        return configured
    return loopback_matcher.allow_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LocalAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(OpenTelemetryMiddleware)

# Register routers / 注册路由
# Strategy: Dual Mount
# Mount at root "/" for Dev mode (where Vite proxy strips /api)
# Mount at "/api" for Prod/EXE mode (where frontend calls /api directly)
routers = [
    projects_router,
    cards_router,
    canon_router,
    facts_router,
    drafts_router,
    session_router,
    config_router,
    websocket_router,
    fanfiction_router,
    proxy_router,
    volumes_router,
    text_chunks_router,
    evidence_router,
    bindings_router,
    memory_pack_router,
    memory_router,
    actions_router,
    export_router,
]

for router in routers:
    app.include_router(router)  # Dev: http://localhost:8000/projects
    app.include_router(router, prefix="/api")  # Prod: http://localhost:8000/api/projects


@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint / 健康检查"""
    desktop_token = os.getenv("WENSHAPE_DESKTOP_SESSION_TOKEN", "").strip()
    supplied_token = request.headers.get("x-wenshape-session-token", "")
    if desktop_token and not (supplied_token and secrets.compare_digest(supplied_token, desktop_token)):
        return {"status": "ok", "version": app.version}

    from pathlib import Path

    data_dir = Path(settings.data_dir) if hasattr(settings, "data_dir") else Path("data")
    storage_ok = data_dir.exists() if data_dir else False

    # Phase 9/13：暴露结构化输出成功率 + prompt caching 命中率指标，供运维 / 前端监控。
    from app.utils.json_metrics import json_metrics_snapshot
    from app.utils.cache_metrics import cache_metrics_snapshot
    from app.jobs.runtime import get_task_queue, task_worker_status
    from app.observability.otel import telemetry
    from app.observability.runtime_metrics import runtime_metrics
    from app.observability.slo import SLOEvaluator
    from app.control_plane.runtime import get_control_store

    return {
        "status": "ok",
        "version": app.version,
        "storage_accessible": storage_ok,
        "json_metrics": json_metrics_snapshot(),
        "cache_metrics": cache_metrics_snapshot(),
        "runtime_metrics": runtime_metrics.snapshot(),
        "task_queue": get_task_queue().health(),
        "task_worker": task_worker_status(),
        "control_plane": get_control_store().health(),
        "telemetry": telemetry.snapshot(),
        "slo": SLOEvaluator(
            targets=((config.get("observability", {}) or {}).get("slo", {}) or {}).get("targets") or {}
        ).evaluate(
            window_seconds=float(
                ((config.get("observability", {}) or {}).get("slo", {}) or {}).get("window_seconds") or 300
            ),
            queue=get_task_queue().health(),
        ),
    }


async def run_startup_tasks():
    """Startup event handler / 启动事件处理"""
    import sys
    import webbrowser
    import asyncio
    import subprocess

    # Auto-open browser in a separate task (non-blocking)
    # Crucial: Any exception here must not crash the server
    if getattr(sys, "frozen", False) and not os.getenv("WENSHAPE_DESKTOP_SHELL"):
        # Packaged mode: open a loopback URL (0.0.0.0 is only a bind address)
        url = f"http://127.0.0.1:{settings.port}"
        logger.info(f"Auto-opening browser at {url}")

        async def open_browser_safely():
            """Try to open browser with fallback strategies for frozen mode"""
            await asyncio.sleep(1.5)  # Wait for server to be fully ready

            try:
                # Strategy 1: Standard webbrowser module (most reliable)
                webbrowser.open(url)
                logger.debug("Browser opened successfully via webbrowser module")
                return
            except Exception as e:
                logger.debug(f"Standard webbrowser.open failed: {e}")

            # Strategy 2: Platform-specific fallback for frozen mode
            try:
                import platform

                system = platform.system()

                if system == "Windows":
                    # Windows: Use cmd /c start
                    subprocess.Popen(f"start {url}", shell=True)
                    logger.debug("Browser opened via Windows start command")
                    return
                elif system == "Darwin":
                    # macOS: Use open command
                    subprocess.Popen(["open", url])
                    logger.debug("Browser opened via macOS open command")
                    return
                else:
                    # Linux/Others: Try xdg-open
                    subprocess.Popen(["xdg-open", url])
                    logger.debug("Browser opened via xdg-open")
                    return
            except Exception as e:
                logger.warning(f"Platform-specific browser launch failed: {e}")

            # Strategy 3: Graceful degradation
            # If all browser opening attempts fail, just log it and continue
            # User can manually open the browser and navigate to the URL
            logger.warning(
                f"Could not auto-open browser. Please manually visit: {url}\n" f"浏览器无法自动打开，请手动访问：{url}"
            )

        # Create task but don't await - let it run in background
        # If it raises, we catch and log it, server continues running
        try:
            asyncio.create_task(open_browser_safely())
        except Exception as e:
            logger.error(f"Failed to create browser-opening task: {e}", exc_info=True)
            # Still don't crash - just continue running the server


# --- Static Files / SPA Support (Added for Packaging) ---
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Check where static files are located
if getattr(sys, "frozen", False):
    # Running as PyInstaller Bundle
    base_path = getattr(sys, "_MEIPASS", Path(sys.executable).parent)
    static_dir = Path(base_path) / "static"
else:
    # Dev: Look for backend/static if it exists (for testing build script without freezing)
    static_dir = Path(__file__).parent.parent / "static"

if static_dir.exists():
    logger.info(f"Serving static files from: {static_dir}")

    # 1. Mount assets (css, js, images)
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    # 2. Serve Index at Root
    @app.get("/")
    async def serve_root():
        return FileResponse(static_dir / "index.html")

    # 3. Catch-all for SPA routes (Serve index.html)
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Safety: If request asks for /api/..., and we reached here, it's a 404.
        # Don't return HTML, otherwise frontend crashes (SyntaxError).
        if full_path.startswith("api/") or full_path.startswith("api"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="API Endpoint Not Found")

        # Check if file exists in static (e.g. favicon.ico)
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        # Otherwise serve index.html for SPA routing
        return FileResponse(static_dir / "index.html")

else:
    logger.warning("Static directory not found. Running in API-only mode (Dev)")


if __name__ == "__main__":
    import uvicorn
    import multiprocessing
    import os
    import socket

    # Critical for Windows EXE to prevent infinite spawn loop
    multiprocessing.freeze_support()

    # Determine execution mode
    is_frozen = getattr(sys, "frozen", False)

    # Packaged desktop app should bind to loopback by default to avoid confusing URLs (0.0.0.0)
    # and reduce unnecessary firewall prompts.
    bind_host = "127.0.0.1" if is_frozen else settings.host

    def _port_available(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, int(port)))
                return True
        except OSError:
            return False

    def _pick_port(host: str, preferred: int, max_tries: int = 20) -> int:
        base = int(preferred or 0)
        if base <= 0:
            return 8000
        for port in range(base, base + max_tries):
            if _port_available(host, port):
                return port
        return base

    auto_port = is_frozen or (str(os.getenv("WENSHAPE_AUTO_PORT", "")).strip().lower() in {"1", "true", "yes", "on"})
    host_for_check = bind_host
    chosen_port = settings.port
    if auto_port and not _port_available(host_for_check, chosen_port):
        new_port = _pick_port(host_for_check, chosen_port + 1)
        if new_port != chosen_port:
            logger.warning(f"Port {chosen_port} is in use. Switching to available port {new_port}.")
            chosen_port = new_port
            settings.port = chosen_port

    if is_frozen:
        # Prod/EXE: Run directly with app instance, NO RELOAD
        # Reloading in frozen mode causes infinite subprocess spawning
        logger.info("Running in Frozen (EXE) Mode")

        def _is_address_in_use(error: OSError) -> bool:
            # Windows: WinError 10048; Linux/macOS: errno 98/48
            winerror = getattr(error, "winerror", None)
            if winerror == 10048:
                return True
            if getattr(error, "errno", None) in {48, 98}:
                return True
            text = str(error).lower()
            return "address already in use" in text or "only one usage" in text

        attempt_port = chosen_port
        while True:
            try:
                settings.port = attempt_port
                uvicorn.run(
                    app,  # Pass app instance directly, not string
                    host=bind_host,
                    port=attempt_port,
                    reload=False,
                    log_level="info",
                )
                break
            except OSError as exc:
                if auto_port and _is_address_in_use(exc):
                    next_port = _pick_port(host_for_check, attempt_port + 1)
                    if next_port != attempt_port:
                        logger.warning(
                            "Port %s is in use. Switching to available port %s.",
                            attempt_port,
                            next_port,
                        )
                        attempt_port = next_port
                        continue

                # When launched by double-click, the console may close immediately.
                # Provide a clear error message instead of "flash exit".
                logger.error("Failed to start server on %s:%s: %s", bind_host, attempt_port, exc, exc_info=True)
                print("")
                print("========================================")
                print(" WenShape 启动失败 / Startup Failed")
                print("----------------------------------------")
                print(f"原因 / Reason: {exc}")
                print("建议 / Suggestion:")
                print("  1) 关闭占用端口的程序（常见：8000）。")
                print("  2) 重新运行后会自动尝试切换端口。")
                print("========================================")
                print("")
                try:
                    input("按回车键退出...")
                except (EOFError, OSError):
                    pass
                raise
    else:
        # Dev: Run with reload
        logger.info("Running in Dev Mode")
        uvicorn.run("app.main:app", host=settings.host, port=chosen_port, reload=settings.debug)
