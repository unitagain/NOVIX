#!/usr/bin/env python3
"""Run HTTP, WebSocket, CSP and authentication smoke checks against a packaged sidecar."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, token: str = "") -> tuple[int, dict[str, str], bytes]:
    headers = {"X-WenShape-Session-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


async def _websocket_check(url: str, token: str) -> bool:
    header_name = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
    options = {header_name: {"X-WenShape-Session-Token": token}, "open_timeout": 5}
    async with websockets.connect(url, **options):
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    port = _free_port()
    token = "w8-synthetic-session-token"
    with tempfile.TemporaryDirectory(prefix="wenshape-package-smoke-") as temporary:
        env = {
            **os.environ,
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "WENSHAPE_BACKEND_PORT": str(port),
            "WENSHAPE_AUTO_PORT": "0",
            "DATA_DIR": temporary,
            "WENSHAPE_DESKTOP_SESSION_TOKEN": token,
            "WENSHAPE_REQUIRE_LOCAL_AUTH": "1",
            "WENSHAPE_DESKTOP_ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
            "WENSHAPE_DESKTOP_ALLOWED_HOSTS": f"127.0.0.1:{port}",
            "PYTHONUTF8": "1",
        }
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(args.sidecar.resolve())],
            env=env,
            cwd=args.sidecar.resolve().parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            ready = False
            for _ in range(120):
                try:
                    ready = _request(f"http://127.0.0.1:{port}/health")[0] == 200
                except OSError:
                    ready = False
                if ready:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.25)
            unauthorized = _request(f"http://127.0.0.1:{port}/api/projects")[0]
            authorized = _request(f"http://127.0.0.1:{port}/api/projects", token)[0]
            root_status, root_headers, root_body = _request(f"http://127.0.0.1:{port}/", token)
            websocket_ok = False
            if ready:
                try:
                    websocket_ok = asyncio.run(_websocket_check(f"ws://127.0.0.1:{port}/ws/trace", token))
                except Exception:
                    websocket_ok = False
            normalized_headers = {str(key).lower(): value for key, value in root_headers.items()}
            csp = normalized_headers.get("content-security-policy", "")
            checks = {
                "process_ready": ready,
                "unauthorized_blocked": unauthorized in {401, 403},
                "authorized_http": authorized == 200,
                "spa_served": root_status == 200 and b"<html" in root_body.lower(),
                "csp_present": bool(csp),
                "websocket_authenticated": websocket_ok,
            }
            result = {"success": all(checks.values()), "checks": checks, "status": {"unauthorized": unauthorized, "authorized": authorized}}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
