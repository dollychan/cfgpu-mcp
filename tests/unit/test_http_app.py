import pytest

from cfgpu_mcp.context import get_request_token
from cfgpu_mcp.http_app import RequestContextMiddleware, _token_from_scope


def test_token_from_scope_parses_bearer():
    scope = {"headers": [(b"authorization", b"Bearer abc123")]}
    assert _token_from_scope(scope) == "abc123"


def test_token_from_scope_bare_value_and_missing():
    assert _token_from_scope({"headers": [(b"authorization", b"rawtoken")]}) == "rawtoken"
    assert _token_from_scope({"headers": [(b"content-type", b"application/json")]}) is None
    assert _token_from_scope({"headers": []}) is None


@pytest.mark.asyncio
async def test_middleware_binds_token_during_http_call_and_resets_after():
    seen = {}

    async def inner(scope, receive, send):
        seen["token"] = get_request_token()

    mw = RequestContextMiddleware(inner)
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer tok-xyz")]}

    async def receive():
        return {}

    async def send(message):
        pass

    await mw(scope, receive, send)
    assert seen["token"] == "tok-xyz"        # endpoint task saw the token
    assert get_request_token() is None        # reset after the request


@pytest.mark.asyncio
async def test_middleware_closes_resources_on_lifespan_shutdown(monkeypatch):
    calls = {"n": 0}

    async def fake_close():
        calls["n"] += 1

    monkeypatch.setattr("cfgpu_mcp.config.close", fake_close)

    async def inner(scope, receive, send):
        # emulate the inner app completing its shutdown
        await send({"type": "lifespan.shutdown.complete"})

    mw = RequestContextMiddleware(inner)
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "lifespan.shutdown"}

    await mw({"type": "lifespan"}, receive, send)

    assert calls["n"] == 1                              # cleanup ran once
    assert sent == [{"type": "lifespan.shutdown.complete"}]  # and was forwarded
