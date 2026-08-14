"""Multi-provider support: config parsing, client isolation, registry filtering.

The load-bearing property under test is D1-d: **a caller's CFGPU token must never
reach a host that is not CFGPU.** In multi-tenant HTTP mode that token arrives in
the request's Authorization header and lives in a ContextVar, and every client
used to read it. A second upstream (the co-located comfy-gateway) means the read
has to become conditional, and "conditional" is exactly the kind of thing that
silently regresses — hence the direct tests below rather than only end-to-end ones.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import cfgpu_mcp.config as cfg_module
from cfgpu_mcp.client.cfgpu_client import CFGPUClient
from cfgpu_mcp.context import reset_request_token, set_request_token
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.settings import DEFAULT_PROVIDER, load_settings


@pytest.fixture
def settings_from(monkeypatch, tmp_path):
    """Load Settings from an inline config.yaml, isolated from the repo's own."""

    def _load(body: str):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body)
        monkeypatch.setenv("CFGPU_CONFIG", str(cfg))
        return load_settings()

    return _load


# ── settings parsing ─────────────────────────────────────────────────────────


def test_cfgpu_provider_is_synthesized_from_cfgpu_api(settings_from):
    """The built-in provider is not declared; it mirrors the cfgpu_api block.

    Keeps one place to configure the CFGPU API — a `providers: cfgpu:` block
    duplicating base_url would be a second source that can disagree.
    """
    s = settings_from("cfgpu_api:\n  base_url: https://example.test/v1\n  http_timeout: 33\n")
    p = s.providers[DEFAULT_PROVIDER]
    assert p.base_url == "https://example.test/v1"
    assert p.http_timeout == 33
    assert p.auth_scheme == "bearer"
    assert p.token_env == "CFGPU_API_TOKEN"


def test_extra_provider_parsed(settings_from):
    s = settings_from(
        "providers:\n"
        "  comfy:\n"
        "    base_url: https://gw.test/v1\n"
        "    auth_scheme: raw\n"
        "    token_env: COMFY_GATEWAY_TOKEN\n"
        "    http_timeout: 60\n"
    )
    p = s.providers["comfy"]
    assert (p.base_url, p.auth_scheme, p.token_env, p.http_timeout) == (
        "https://gw.test/v1", "raw", "COMFY_GATEWAY_TOKEN", 60
    )
    assert DEFAULT_PROVIDER in s.providers  # still synthesized alongside


def test_provider_may_not_borrow_the_cfgpu_token(settings_from):
    """★ The core isolation rule, enforced where it is cheapest to enforce.

    Pointing a third-party provider at CFGPU_API_TOKEN would ship the CFGPU
    credential to a host that is not CFGPU. Nothing downstream can tell that
    apart from a legitimate configuration, so it has to fail here.
    """
    with pytest.raises(ValueError, match="CFGPU_API_TOKEN"):
        settings_from(
            "providers:\n  comfy:\n    base_url: https://gw.test/v1\n"
            "    token_env: CFGPU_API_TOKEN\n"
        )


def test_unknown_provider_key_is_rejected(settings_from):
    """A typo must not degrade into 'provider has no credential' → a 401 later."""
    with pytest.raises(ValueError, match="unknown keys"):
        settings_from("providers:\n  comfy:\n    base_url: https://gw.test/v1\n    tokenenv: X\n")


def test_provider_requires_base_url(settings_from):
    with pytest.raises(ValueError, match="base_url"):
        settings_from("providers:\n  comfy:\n    auth_scheme: raw\n    token_env: X\n")


def test_bad_auth_scheme_is_rejected(settings_from):
    with pytest.raises(ValueError, match="auth_scheme"):
        settings_from(
            "providers:\n  comfy:\n    base_url: https://gw.test/v1\n"
            "    auth_scheme: basic\n    token_env: X\n"
        )


# ── client behaviour ─────────────────────────────────────────────────────────


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


async def _capture_headers(client: CFGPUClient) -> dict:
    captured: dict = {}
    session = MagicMock()
    session.request.side_effect = lambda method, url, **kw: (captured.update(kw), _FakeResp())[1]
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        await client.get("/x")
    return captured["headers"]


@pytest.mark.asyncio
async def test_raw_auth_scheme_sends_a_bare_token():
    """comfy-gateway API.md §1: bare token, no `Bearer ` prefix."""
    client = CFGPUClient(api_token="t0k", base_url="https://gw.test/v1", auth_scheme="raw")
    assert (await _capture_headers(client))["Authorization"] == "t0k"


@pytest.mark.asyncio
async def test_bearer_remains_the_default():
    client = CFGPUClient(api_token="t0k", base_url="https://api.test/v1")
    assert (await _capture_headers(client))["Authorization"] == "Bearer t0k"


@pytest.mark.asyncio
async def test_non_cfgpu_provider_ignores_the_request_token():
    """★ The tenant's CFGPU token must not be forwarded to another host.

    A caller's token is in the ContextVar for the whole request, including the
    part that talks to the gateway. Without use_request_token=False the gateway
    would receive it verbatim — and would have no way to know it shouldn't have.
    """
    client = CFGPUClient(
        api_token="gateway-secret",
        base_url="https://gw.test/v1",
        auth_scheme="raw",
        use_request_token=False,
        token_env="COMFY_GATEWAY_TOKEN",
        provider="comfy",
    )
    tok = set_request_token("tenant-cfgpu-token")
    try:
        headers = await _capture_headers(client)
    finally:
        reset_request_token(tok)
    assert headers["Authorization"] == "gateway-secret"
    assert "tenant-cfgpu-token" not in headers["Authorization"]


@pytest.mark.asyncio
async def test_non_cfgpu_provider_does_not_fall_back_to_cfgpu_env():
    """Not even the *env* CFGPU token leaks: the fallback reads token_env only."""
    with patch.dict(os.environ, {"CFGPU_API_TOKEN": "cfgpu-env-token"}, clear=True):
        client = CFGPUClient(
            base_url="https://gw.test/v1",
            token_env="COMFY_GATEWAY_TOKEN",
            use_request_token=False,
            provider="comfy",
        )
        assert client._token is None
        with pytest.raises(CFGPUError) as e:
            await client.post("/x", {})
    assert e.value.error_type == "auth"
    assert "COMFY_GATEWAY_TOKEN" in e.value.user_message


# ── config.get_client / client_for ───────────────────────────────────────────


@pytest.fixture
def wired(settings_from):
    """Point config's singletons at a config.yaml declaring a comfy provider."""
    settings = settings_from(
        "providers:\n  comfy:\n    base_url: https://gw.test/v1\n"
        "    auth_scheme: raw\n    token_env: COMFY_GATEWAY_TOKEN\n"
    )
    saved_settings, saved_clients = cfg_module._settings, dict(cfg_module._clients)
    cfg_module._settings = settings
    cfg_module._clients.clear()
    yield settings
    cfg_module._settings = saved_settings
    cfg_module._clients.clear()
    cfg_module._clients.update(saved_clients)


def test_get_client_is_per_provider(wired):
    a, b = cfg_module.get_client(), cfg_module.get_client("comfy")
    assert a is not b
    assert a is cfg_module.get_client()          # cached, one pool each
    assert b is cfg_module.get_client("comfy")
    assert b._auth_scheme == "raw"
    assert b._use_request_token is False
    assert a._use_request_token is True


def test_unconfigured_provider_names_the_fix(wired):
    with pytest.raises(CFGPUError) as e:
        cfg_module.get_client("nope")
    assert "providers:" in e.value.user_message


def test_client_for_routes_by_adapter_provider(wired):
    comfy_adapter = MagicMock(provider="comfy")
    cfgpu_adapter = MagicMock(provider=DEFAULT_PROVIDER)
    assert cfg_module.client_for(comfy_adapter) is cfg_module.get_client("comfy")
    assert cfg_module.client_for(cfgpu_adapter) is cfg_module.get_client()


# ── registry filtering ───────────────────────────────────────────────────────


def _registry(available):
    from pathlib import Path

    from cfgpu_mcp.adapters.registry import AdapterRegistry

    import cfgpu_mcp.adapters  # noqa: F401  (triggers @register_python_adapter)

    r = AdapterRegistry(
        model_dir=Path(cfg_module.__file__).parent / "models",
        available_providers=available,
    )
    r.load()
    return r


def test_models_of_an_unconfigured_provider_are_not_registered():
    """★ Not offered rather than offered-and-broken.

    If it registered, the model would show up in list_models and in the tool's
    model enum, and model="auto" could route a real generation to a host we have
    neither a URL nor a credential for — failing only after the caller committed.
    """
    r = _registry({DEFAULT_PROVIDER})
    with pytest.raises(KeyError):
        r.get("cfdream/minimax-h3")


def test_models_register_once_their_provider_is_configured():
    r = _registry({DEFAULT_PROVIDER, "comfy"})
    assert r.get("cfdream/minimax-h3").provider == "comfy"
    # The variant inherits provider through the extends merge — if it didn't, it
    # would be filtered out here while its parent survived.
    assert r.get("cfdream/minimax-h3-r2v").provider == "comfy"


def test_existing_models_default_to_the_cfgpu_provider():
    assert _registry({DEFAULT_PROVIDER}).get("wan-video").provider == DEFAULT_PROVIDER
