"""The whole loop: generate_video → comfy-gateway → poll → artifact.

Everything else in this change is tested a piece at a time. This one runs the
real registry, the real adapter, the real TaskManager and the real
``config.client_for``, and stubs only the HTTP session — so it is the test that
would catch a wiring mistake between those pieces, which is exactly the class of
bug that unit tests of each piece cannot see.

What it pins that nothing else does:

- the request goes to the **gateway's** base_url, not CFGPU's
- with a bare ``Authorization`` (comfy-gateway API.md §1), from the gateway's own
  env var — while a tenant's CFGPU token sits in the ContextVar untouched
- the poll URL is built from the gateway's ``poll_endpoint``
- ``seed`` and ``usage`` survive all the way out to the caller's result
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

import cfgpu_mcp.config as cfg_module
from cfgpu_mcp.context import reset_request_token, set_request_token
from cfgpu_mcp.service import video as video_service
from cfgpu_mcp.settings import ProviderSettings, load_settings

GATEWAY = "https://gw.test/v1"
GATEWAY_TOKEN = "gateway-server-credential"
TENANT_TOKEN = "a-tenant-cfgpu-token"

_SUCCESS = {
    "id": "gw-task-1",
    "status": "succeeded",
    "data": [{"url": "https://oss.test/h3/gw-task-1/out.mp4?sig=x"}],
    "expires_at": "2026-08-14T11:20:00Z",
    "seed": 4667556858703757508,
    "usage": {"gpu_seconds": 96.3, "width": 864, "height": 480,
              "length": 124, "fps": 24, "actual_duration": 5.167},
}


class _FakeResp:
    ok = True
    status = 200

    def __init__(self, body):
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
async def loop_env(monkeypatch, tmp_path):
    """Real registry + real service path, with the gateway's HTTP session stubbed."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.db import _CREATE_TABLE
    from cfgpu_mcp.client.repository import SqliteTaskRepository

    await db.execute(_CREATE_TABLE)
    await db.commit()

    monkeypatch.setenv("COMFY_GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.setenv("CFGPU_CONFIG", str(tmp_path / "absent.yaml"))
    settings = load_settings()
    settings.providers["comfy"] = ProviderSettings(
        name="comfy", base_url=GATEWAY, auth_scheme="raw", token_env="COMFY_GATEWAY_TOKEN",
    )

    saved = (cfg_module._settings, cfg_module._registry, dict(cfg_module._clients))
    cfg_module._settings = settings
    cfg_module._registry = None      # rebuilt against the settings above
    cfg_module._clients.clear()

    calls: list[tuple[str, str, dict]] = []

    def _request(method, url, **kw):
        calls.append((method, url, kw))
        return _FakeResp({"id": "gw-task-1", "status": "pending"} if method == "POST" else _SUCCESS)

    session = MagicMock()
    session.request.side_effect = _request

    with (
        patch("cfgpu_mcp.config.get_task_repository",
              AsyncMock(return_value=SqliteTaskRepository(db))),
        patch.object(type(cfg_module.get_client("comfy")), "_get_session",
                     new_callable=AsyncMock, return_value=session),
        # The adapter's real base_interval is 5s; the schedule isn't under test here.
        patch("cfgpu_mcp.task_manager.asyncio.sleep", new_callable=AsyncMock),
    ):
        yield calls

    cfg_module._settings, cfg_module._registry, restored = saved
    cfg_module._clients.clear()
    cfg_module._clients.update(restored)
    await db.close()


@pytest.mark.asyncio
async def test_generate_video_round_trips_through_the_gateway(loop_env):
    tok = set_request_token(TENANT_TOKEN)   # as multi-tenant HTTP mode would
    try:
        result = await video_service.generate_video(
            prompt="waves crashing at dusk",
            model="cfdream/minimax-h3",
            resolution="480p",
            duration_seconds=5,
        )
    finally:
        reset_request_token(tok)

    assert result["urls"] == ["https://oss.test/h3/gw-task-1/out.mp4?sig=x"]
    # ★ seed and the effective geometry reach the caller — the two things a
    #   payload_mapping-only adapter would have dropped on the success path.
    assert result["seed"] == 4667556858703757508
    assert result["usage"]["actual_duration"] == 5.167
    assert result["model_used"] == "cfdream/minimax-h3"   # public id, never adapter_id
    assert result["expires_at"].startswith("2026-08-14T11:20")

    post_method, post_url, post_kw = loop_env[0]
    assert (post_method, post_url) == ("POST", f"{GATEWAY}/video/generations")
    assert post_kw["json"]["model"] == "cfdream/minimax-h3"
    assert post_kw["json"]["duration_seconds"] == 5

    poll_method, poll_url, _ = loop_env[1]
    assert (poll_method, poll_url) == ("GET", f"{GATEWAY}/video/tasks/gw-task-1")


@pytest.mark.asyncio
async def test_the_gateway_never_sees_the_tenants_cfgpu_token(loop_env):
    """★ D1-d, end to end rather than at the client's unit boundary.

    The tenant's token is live in the ContextVar for the whole call. If any part
    of this path used the default client, or built one that reads the ContextVar,
    the gateway would receive a credential issued for CFGPU — and would have no
    way to know it shouldn't have.
    """
    tok = set_request_token(TENANT_TOKEN)
    try:
        await video_service.generate_video(
            prompt="x", model="cfdream/minimax-h3", resolution="480p",
        )
    finally:
        reset_request_token(tok)

    for _method, _url, kw in loop_env:
        auth = kw["headers"]["Authorization"]
        assert auth == GATEWAY_TOKEN          # bare, not "Bearer …" (API.md §1)
        assert TENANT_TOKEN not in auth


@pytest.mark.asyncio
async def test_default_cfgpu_models_still_use_the_cfgpu_client(loop_env):
    """The comfy client must not become everyone's client. Same registry, same
    process — a model with no `provider:` still resolves to the CFGPU upstream."""
    registry = cfg_module.get_registry()
    assert cfg_module.client_for(registry.get("wan-video")) is cfg_module.get_client()
    assert cfg_module.get_client()._use_request_token is True
    assert cfg_module.get_client("comfy") is not cfg_module.get_client()


def test_env_var_is_the_only_place_the_gateway_token_may_live():
    """A gateway token in config.yaml would be a secret in a tracked file."""
    example = (os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) + "/config.example.yaml"
    with open(example) as f:
        text = f.read()
    assert "token_env: COMFY_GATEWAY_TOKEN" in text
    assert "token:" not in text.replace("token_env:", "")
