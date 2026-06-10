"""Streamable-HTTP ASGI assembly for the cfgpu MCP server.

Wraps FastMCP's ``streamable_http_app()`` with a single pure-ASGI middleware
that handles the two things multi-tenant HTTP needs and stdio does not:

1. **Per-request token** — read the ``Authorization`` header and bind it to the
   request-scoped ContextVar so the HTTP client injects the caller's own CFGPU
   token. A *pure ASGI* middleware (not Starlette's ``BaseHTTPMiddleware``) is
   required: BaseHTTPMiddleware runs the endpoint in a separate task, so a
   ContextVar set in its ``dispatch`` would not propagate to the tool call.

2. **Process-level cleanup** — ``streamable_http_app()`` overrides the Starlette
   lifespan with the session manager's, and the FastMCP constructor lifespan
   runs per session (per request in stateless mode), so shared singletons must
   *not* be closed there. We close them once here, when the inner app signals
   ``lifespan.shutdown.complete``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp import config
from cfgpu_mcp.context import reset_request_token, set_request_token

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cfgpu_mcp.settings import Settings


def _token_from_scope(scope: dict) -> str | None:
    """Extract a bearer token from the ASGI scope's Authorization header."""
    for key, value in scope.get("headers") or []:
        if key == b"authorization":
            val = value.decode("latin-1").strip()
            if val[:7].lower() == "bearer ":
                return val[7:].strip() or None
            return val or None
    return None


class RequestContextMiddleware:
    """Pure-ASGI middleware: token binding (http) + resource cleanup (lifespan)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            token = set_request_token(_token_from_scope(scope))
            try:
                await self.app(scope, receive, send)
            finally:
                reset_request_token(token)
            return

        if scope["type"] == "lifespan":
            async def send_wrapper(message):
                if message["type"] == "lifespan.shutdown.complete":
                    await config.close()
                await send(message)

            await self.app(scope, receive, send_wrapper)
            return

        await self.app(scope, receive, send)


def build_http_app(mcp: "FastMCP", settings: "Settings"):
    """Configure FastMCP HTTP settings and return the wrapped ASGI app."""
    mcp.settings.stateless_http = settings.http.stateless
    mcp.settings.host = settings.http.host
    mcp.settings.port = settings.http.port
    return RequestContextMiddleware(mcp.streamable_http_app())
