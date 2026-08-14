import asyncio
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.client.cfgpu_client import (
    ACCEPT_ENCODING,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HTTP_TIMEOUT,
    CFGPUClient,
)
from cfgpu_mcp.errors import CFGPUError


def _client() -> CFGPUClient:
    return CFGPUClient(api_token="test-token", base_url="https://api.example.com")


def test_default_timeout_applied():
    client = _client()
    assert client._timeout.total == DEFAULT_HTTP_TIMEOUT
    assert client._timeout.connect == DEFAULT_CONNECT_TIMEOUT


def test_timeout_overridable_via_constructor():
    client = CFGPUClient(api_token="t", base_url="https://api.example.com",
                         http_timeout=5.0, connect_timeout=2.0)
    assert client._timeout.total == 5.0
    assert client._timeout.connect == 2.0


@pytest.mark.asyncio
async def test_request_timeout_maps_to_cfgpu_error():
    client = _client()
    session = MagicMock()
    session.request.side_effect = asyncio.TimeoutError()
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc_info:
            await client.post("/v1/images/generations", {"prompt": "x"})
    assert exc_info.value.error_type == "timeout"
    assert exc_info.value.retryable is True


class _FakeResp:
    status = 200
    ok = True

    async def json(self, content_type=None):
        return {"ok": True}

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_per_request_token_from_context_overrides_fallback():
    """The ContextVar token wins over the constructor/env token, and the shared
    session carries no auth header — it's injected per request."""
    from cfgpu_mcp.context import set_request_token, reset_request_token

    client = CFGPUClient(api_token="fallback-token", base_url="https://api.example.com")
    captured: dict = {}
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: (captured.update(kw), _FakeResp())[1]

    tok = set_request_token("ctx-token")
    try:
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
            await client.get("/v1/x")
    finally:
        reset_request_token(tok)

    assert captured["headers"]["Authorization"] == "Bearer ctx-token"


@pytest.mark.asyncio
async def test_accept_encoding_excludes_brotli():
    """Every request pins gzip/deflate instead of letting aiohttp negotiate.

    aiohttp advertises `br` whenever the optional Brotli package happens to be
    importable on the host, and Cloudflare (which fronts the submodel provider)
    brotli-encodes JSON the moment it's offered — a failing decode there surfaced
    as ClientPayloadError("400, message:\\n  Can not decode content-encoding: br")
    on healthy polls. Nothing here is big enough for brotli to earn its keep.
    """
    client = _client()
    captured: dict = {}
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: (captured.update(kw), _FakeResp())[1]

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        await client.get("/v1/x")

    assert captured["headers"]["Accept-Encoding"] == ACCEPT_ENCODING
    assert "br" not in captured["headers"]["Accept-Encoding"]


@pytest.mark.asyncio
async def test_request_raises_auth_when_no_token_anywhere():
    with patch.dict(os.environ, {}, clear=True):
        client = CFGPUClient(base_url="https://api.example.com")  # no token, no env, no context
        with pytest.raises(CFGPUError) as exc_info:
            await client.post("/v1/x", {"a": 1})
    assert exc_info.value.error_type == "auth"
