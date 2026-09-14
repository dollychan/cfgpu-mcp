"""Request-scoped context.

In streamable-HTTP multi-tenant mode each request carries its own CFGPU API
token (from the ``Authorization`` header). We thread it through a ContextVar
rather than every function signature: the request boundary calls
``set_request_token`` and the HTTP client reads it at the very end.

ContextVars are isolated per asyncio task, so concurrent requests never see each
other's token. In stdio mode nothing sets it and the client falls back to the
``CFGPU_API_TOKEN`` environment variable.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_token: ContextVar[str | None] = ContextVar("cfgpu_api_token", default=None)


def set_request_token(token: str | None) -> Token:
    """Bind the CFGPU API token for the current request/task. Returns a reset Token."""
    return _request_token.set(token)


def reset_request_token(token: Token) -> None:
    """Restore the previous token binding (pair with set_request_token)."""
    _request_token.reset(token)


def get_request_token() -> str | None:
    """Current request's token, or None when unset (stdio / outside a request)."""
    return _request_token.get()
