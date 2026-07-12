"""Security response headers for the loopback desktop application."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; font-src 'self' data:; "
    "connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; "
    "object-src 'none'; frame-src 'none'; base-uri 'self'; form-action 'self'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or ())
                existing = {key.lower() for key, _value in headers}
                additions = (
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                )
                headers.extend((key, value) for key, value in additions if key not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

