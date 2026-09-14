import asyncio
import os

import aiohttp
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
async def test_submit_timeout_is_not_retryable_and_says_the_outcome_is_unknown():
    """★ A POST whose response never came back may already have been accepted and billed.

    ``TaskManager.create`` POSTs *before* ``insert_task``, so there is no returned
    task_id and no local row — the work, if it exists, is unreachable and unnameable.
    ``retryable=True`` here told the agent that resending was safe; it is a coin flip on
    a double charge. This is the one error in the system that can answer neither "it
    took effect" nor "it did not", which is what ``outcome_unknown`` is for.
    """
    client = _client()
    session = MagicMock()
    session.request.side_effect = asyncio.TimeoutError()
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc_info:
            await client.post("/v1/images/generations", {"prompt": "x"})
    err = exc_info.value
    assert err.error_type == "timeout"
    assert err.retryable is False
    assert err.outcome_unknown is True
    assert err.to_tool_result_dict()["outcome_unknown"] is True
    assert err.to_tool_result_dict()["phase"] == "request"


@pytest.mark.asyncio
async def test_poll_timeout_stays_retryable():
    """A GET creates nothing, so asking again is free — the reason the same wall-clock
    failure is safe on a poll and not on a submit is the verb, not the timeout."""
    client = _client()
    session = MagicMock()
    session.request.side_effect = asyncio.TimeoutError()
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc_info:
            await client.get("/v1/tasks/task-1")
    assert exc_info.value.retryable is True
    assert exc_info.value.outcome_unknown is False


@pytest.mark.asyncio
async def test_connect_phase_timeout_is_retryable_even_on_a_submit():
    """DNS/TCP/TLS never completed, so the upstream received nothing whatever the verb.
    Provably nothing to double-charge — this one is safe to resend."""
    client = _client()
    session = MagicMock()
    session.request.side_effect = aiohttp.ConnectionTimeoutError()
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc_info:
            await client.post("/v1/images/generations", {"prompt": "x"})
    err = exc_info.value
    assert err.retryable is True
    assert err.outcome_unknown is False
    assert err.to_tool_result_dict()["phase"] == "connect"


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
    importable on the host, and a Cloudflare-fronted upstream (the since-retired
    `submodel` provider) brotli-encodes JSON the moment it's offered — a failing
    decode there surfaced as ClientPayloadError("400, message:\\n  Can not decode
    content-encoding: br") on healthy polls. That provider is gone; any upstream
    behind a CDN can do the same, and nothing here is big enough for brotli to
    earn its keep.
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


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,expected", [
    ("cfgpu", "cfgpu_api.http_timeout"),
    ("cfgpu-daily", "providers.cfgpu-daily.http_timeout"),
    ("comfy", "providers.comfy.http_timeout"),
])
async def test_timeout_names_the_knob_that_governs_this_provider(provider, expected):
    """Only the built-in cfgpu provider reads cfgpu_api.http_timeout.

    config.get_client prefers a provider's own http_timeout whenever it is set, so
    pointing a non-cfgpu timeout at the top-level key sends the reader to a knob
    that changes nothing for the model that just failed: they raise it, retry, time
    out identically, and stop trusting the message.
    """
    client = CFGPUClient(
        api_token="t", base_url="https://x.test", http_timeout=120,
        provider=provider, use_request_token=(provider == "cfgpu"),
    )
    session = MagicMock()
    session.request.side_effect = asyncio.TimeoutError()

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc:
            await client.post("/v1/x", json={})

    assert exc.value.error_type == "timeout"
    assert expected in exc.value.user_message
    assert f"provider {provider!r}" in exc.value.user_message
    # The provider must also ride `original`: a timeout report that does not say
    # which upstream stalled cannot be acted on from the log alone.
    assert exc.value.original["provider"] == provider
    if provider != "cfgpu":
        assert "cfgpu_api.http_timeout" not in exc.value.user_message



@pytest.mark.asyncio
async def test_connect_phase_timeout_points_at_connect_timeout_not_http_timeout():
    """A connect timeout is an ``asyncio.TimeoutError`` too — one handler, two causes.

    aiohttp raises ConnectionTimeoutError for DNS / TCP / TLS, i.e. before the
    upstream has seen anything. Reporting it as "增大 http_timeout" sends the
    reader to a knob that cannot move it: raise 120 → 300, retry, fail in the
    same ``connect_timeout`` seconds. Same failure as pointing a non-cfgpu
    provider at cfgpu_api.http_timeout, one layer down.
    """
    client = CFGPUClient(
        api_token="t", base_url="https://x.test", http_timeout=300, connect_timeout=10,
        provider="cfgpu-daily", use_request_token=False,
    )
    session = MagicMock()
    session.request.side_effect = aiohttp.ConnectionTimeoutError("Connection timeout to host")

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc:
            await client.post("/v1/x", json={})

    assert exc.value.error_type == "timeout"
    assert exc.value.original["phase"] == "connect"
    assert "providers.cfgpu-daily.connect_timeout" in exc.value.user_message
    # The knob that cannot work must be named only to rule it out.
    assert "增大 providers.cfgpu-daily.http_timeout 不会有任何作用" in exc.value.user_message
    # And the base_url has to be in the text: "can this box reach that host" is
    # the actual next step, and it needs the host to be checkable.
    assert "https://x.test" in exc.value.user_message


@pytest.mark.asyncio
async def test_timeout_reports_measured_elapsed_never_the_budget():
    """The elapsed seconds are measured, not read off the config.

    Printing ``timeout.total`` claimed 120s had passed when the call had in fact
    failed in 10 — the one number that would have identified the phase was the
    one number being fabricated.
    """
    client = CFGPUClient(
        api_token="t", base_url="https://x.test", http_timeout=120, connect_timeout=10,
        provider="cfgpu-daily", use_request_token=False,
    )
    session = MagicMock()
    session.request.side_effect = aiohttp.ConnectionTimeoutError("Connection timeout to host")

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc:
            await client.post("/v1/x", json={})

    assert exc.value.original["elapsed"] < 1.0
    assert "120" not in exc.value.user_message.split("connect_timeout")[0]


class _BodyErrorResp(_FakeResp):
    """HTTP 200 whose body carries an upstream error object.

    Both the submit and the poll endpoints answer this way — the status line says the
    call was served, the body says what happened to the work.
    """

    async def json(self, content_type=None):
        return {
            "id": "cgt-20260902165545-8768d",
            "error": {
                "message": (
                    "The request failed because the output video may be related to "
                    "copyright restrictions."
                )
            },
        }


@pytest.mark.asyncio
async def test_post_still_raises_on_an_http_200_body_error():
    """On a submit the body error *is* this call's failure — the job was refused."""
    client = _client()
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: _BodyErrorResp()

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError) as exc_info:
            await client.post("/v1/video/generations", {"prompt": "x"})

    assert "copyright" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_get_hands_back_an_http_200_body_error_when_asked_to():
    """The poll's opt-out: the body is the task record, so the caller judges it.

    Raising here made a dead task look like an unreachable one — ``wait()`` absorbed
    it as a transient poll failure and reported ``status: "running"`` on a task
    nothing upstream would ever advance.
    """
    client = _client()
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: _BodyErrorResp()

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        body = await client.get("/v1/video/tasks/t1", raise_on_body_error=False)

    assert "copyright" in body["error"]["message"]


@pytest.mark.asyncio
async def test_get_raises_on_a_body_error_by_default():
    """The opt-out is opt-in: only a caller that knows the body shape may take it."""
    client = _client()
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: _BodyErrorResp()

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        with pytest.raises(CFGPUError):
            await client.get("/v1/video/tasks/t1")
