"""Loopback sidecar authentication and origin/host validation."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send


SESSION_HEADER = b"x-wenshape-session-token"


def _split_env(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


@dataclass(frozen=True)
class LocalAuthPolicy:
    token: str
    allowed_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    exempt_paths: tuple[str, ...] = ("/health",)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @classmethod
    def from_env(cls) -> "LocalAuthPolicy":
        token = os.getenv("WENSHAPE_DESKTOP_SESSION_TOKEN", "").strip()
        required = os.getenv("WENSHAPE_REQUIRE_LOCAL_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
        if required and not token:
            raise RuntimeError("local_auth_token_required")
        return cls(
            token=token,
            allowed_origins=_split_env("WENSHAPE_DESKTOP_ALLOWED_ORIGINS"),
            allowed_hosts=_split_env("WENSHAPE_DESKTOP_ALLOWED_HOSTS"),
        )


class LocalAuthMiddleware:
    """Authenticate every sidecar request when the desktop session token is configured."""

    def __init__(self, app: ASGIApp, policy: LocalAuthPolicy | None = None):
        self.app = app
        self.policy = policy or LocalAuthPolicy.from_env()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self.policy.enabled:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if scope["type"] == "http" and path in self.policy.exempt_paths:
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope.get("headers") or ())
        if not self._host_allowed(headers) or not self._origin_allowed(headers) or not self._token_valid(headers):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401, "reason": "unauthorized"})
            else:
                await _send_json(send, 401, {"code": "local_auth_required", "detail": "Unauthorized"})
            return

        await self.app(scope, receive, send)

    def _token_valid(self, headers: dict[bytes, bytes]) -> bool:
        supplied = headers.get(SESSION_HEADER, b"").decode("utf-8", errors="ignore")
        return bool(supplied) and secrets.compare_digest(supplied, self.policy.token)

    def _origin_allowed(self, headers: dict[bytes, bytes]) -> bool:
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/")
        if not origin:
            return True
        allowed = {item.rstrip("/") for item in self.policy.allowed_origins}
        return bool(allowed) and origin in allowed

    def _host_allowed(self, headers: dict[bytes, bytes]) -> bool:
        if not self.policy.allowed_hosts:
            return True
        host = headers.get(b"host", b"").decode("latin-1").lower()
        return host in {item.lower() for item in self.policy.allowed_hosts}


def _header_map(rows: Iterable[tuple[bytes, bytes]]) -> dict[bytes, bytes]:
    return {str(key.decode("latin-1")).lower().encode("latin-1"): value for key, value in rows}


async def _send_json(send: Send, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
