"""Response security headers.

Pure ASGI on purpose, NOT BaseHTTPMiddleware: BaseHTTPMiddleware runs the
downstream app inside its own task group and buffers the response, which
breaks streamed file downloads (the CSV/XLSX order exports) and can swallow
background tasks. This class only rewrites the `http.response.start` message,
so it is inert for every other part of the request lifecycle.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Swagger UI / ReDoc need inline scripts and styles, so the API's own
# lock-everything-down CSP cannot apply to them. These are dev-only paths
# anyway (main.py serves them only in dev environments).
DOCS_PATHS = frozenset({"/api/docs", "/api/redoc", "/api/openapi.json"})

# This origin serves JSON and file attachments only. Nothing should ever be
# loaded from it, framed from it, or resolve a relative URL against it.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
)

# One year, subdomains included. Only sent when constructed with hsts=True:
# on plain-http localhost it would pin the browser to https for a year.
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra: list[tuple[bytes, bytes]] = list(BASE_HEADERS)
        if scope.get("path", "") not in DOCS_PATHS:
            extra.append((b"content-security-policy", API_CSP.encode()))
        if self.hsts:
            extra.append(HSTS_HEADER)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Copy rather than mutate: the response object's own header list
                # may be reused across sends (StaticFiles, cached responses).
                headers: list[tuple[bytes, bytes]] = list(message.get("headers") or [])
                # A route that deliberately set one of these wins.
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in extra if name not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
