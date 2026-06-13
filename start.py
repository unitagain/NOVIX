#!/usr/bin/env python3
"""One-click local launcher for WenShape development."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Tuple


def _pick_free_port(host: str, preferred: int, max_tries: int = 30) -> int:
    preferred = int(preferred or 0)
    for port in range(max(preferred, 1), max(preferred, 1) + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                return port
        except OSError:
            continue
    return preferred or 0


def pick_ports() -> Tuple[int, int]:
    host = "127.0.0.1"
    backend_preferred = int(os.environ.get("WENSHAPE_BACKEND_PORT") or os.environ.get("PORT") or 8000)
    frontend_preferred = int(os.environ.get("WENSHAPE_FRONTEND_PORT") or os.environ.get("VITE_DEV_PORT") or 3000)
    backend_port = _pick_free_port(host, backend_preferred) or backend_preferred
    frontend_port = _pick_free_port(host, frontend_preferred) or frontend_preferred
    return backend_port, frontend_port


def check_python() -> bool:
    """Ensure Python 3.10-3.12 is available."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print("[ERROR] Python 3.10-3.12 is required")
        return False
    if version.major == 3 and version.minor > 12:
        print("[ERROR] Python 3.13+ is not supported yet, please use Python 3.12")
        return False
    print(f"[OK] Python {version.major}.{version.minor}.{version.micro}")
    return True


def check_node() -> bool:
    """Ensure Node.js 18+ is available."""
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("[ERROR] Node.js is not installed or not in PATH")
        return False

    version_str = result.stdout.strip()
    version_parts = version_str.lstrip("v").split(".")
    major = int(version_parts[0])
    if major < 18:
        print(f"[ERROR] Node.js 18+ is required, found {version_str}")
        return False

    print(f"[OK] Node.js {version_str}")
    return True


def _ensure_default_env(backend_dir: Path) -> None:
    """Create a minimal `.env` on first run for local development."""
    env_path = backend_dir / ".env"
    if env_path.exists():
        return

    env_path.write_text(
        "\n".join(
            [
                "# Auto-generated on first run",
                "HOST=127.0.0.1",
                "DEBUG=True",
                "",
                "# Fill your provider keys below. See .env.example for details.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def start_backend(backend_port: int) -> int:
    """Start the backend service."""
    print("\n[1/2] Starting backend service...")
    backend_dir = Path(__file__).resolve().parent / "backend"
    env = dict(os.environ)
    env["PORT"] = str(backend_port)
    env["WENSHAPE_BACKEND_PORT"] = str(backend_port)
    env["WENSHAPE_AUTO_PORT"] = "1"

    _ensure_default_env(backend_dir)

    if platform.system() == "Windows":
        subprocess.Popen(
            ["cmd", "/k", "run.bat"],
            cwd=str(backend_dir),
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen(
            ["bash", "run.sh"],
            cwd=str(backend_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print(f"  Backend: http://localhost:{backend_port}")
    return backend_port


def wait_for_backend_ready(host: str, port: int, timeout_s: int = 180) -> bool:
    """Wait until the backend `/health` endpoint becomes reachable."""
    url = f"http://{host}:{port}/health"
    deadline = time.monotonic() + max(1, int(timeout_s))

    print(f"  Waiting for backend readiness: {url}")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if getattr(response, "status", 200) == 200:
                    print("  Backend is ready.")
                    return True
        except Exception:
            time.sleep(1)

    print("  Backend readiness timed out. Starting frontend anyway.")
    return False


def start_frontend(backend_port: int, frontend_port: int) -> int:
    """Start the frontend service."""
    print("[2/2] Starting frontend service...")
    frontend_dir = Path(__file__).resolve().parent / "frontend"
    env = dict(os.environ)
    env["VITE_DEV_PORT"] = str(frontend_port)
    env["WENSHAPE_FRONTEND_PORT"] = str(frontend_port)
    env["VITE_BACKEND_PORT"] = str(backend_port)
    env["WENSHAPE_BACKEND_PORT"] = str(backend_port)
    env["VITE_BACKEND_URL"] = env.get("VITE_BACKEND_URL") or f"http://localhost:{backend_port}"

    if platform.system() == "Windows":
        subprocess.Popen(
            ["cmd", "/k", "run.bat"],
            cwd=str(frontend_dir),
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen(
            ["bash", "run.sh"],
            cwd=str(frontend_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print(f"  Frontend: http://localhost:{frontend_port}")
    return frontend_port


def main() -> None:
    print("=" * 50)
    print("  WenShape - Context-Aware Novel Writing System")
    print("=" * 50)
    print()

    print("Checking requirements...")
    if not check_python():
        sys.exit(1)
    if not check_node():
        sys.exit(1)

    print()

    try:
        backend_port, frontend_port = pick_ports()
        backend_port = start_backend(backend_port)
        wait_for_backend_ready("127.0.0.1", backend_port)
        frontend_port = start_frontend(backend_port, frontend_port)

        print()
        print("=" * 50)
        print("  Services started successfully!")
        print("=" * 50)
        print()
        print("Access URLs:")
        print(f"  Frontend:   http://localhost:{frontend_port}")
        print(f"  Backend:    http://localhost:{backend_port}")
        print(f"  API Docs:   http://localhost:{backend_port}/docs")
        print()
        print("Tip: close the spawned service windows to stop the services.")
        print()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
    except Exception as exc:
        print(f"[ERROR] Failed to start services: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
