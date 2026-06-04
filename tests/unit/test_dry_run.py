import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from cfgpu_mcp.client.cfgpu_client import CFGPUClient


async def _make_client() -> CFGPUClient:
    return CFGPUClient(api_token="test-token", base_url="https://api.example.com")


def _mock_session(response_body: dict = {}):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json = AsyncMock(return_value=response_body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.request.return_value = mock_resp
    return mock_session


async def test_dry_run_logs_request(caplog):
    import logging
    client = await _make_client()
    with patch.dict(os.environ, {"CFGPU_DRY_RUN": "1"}):
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_mock_session()):
            with caplog.at_level(logging.INFO, logger="cfgpu_mcp.client.cfgpu_client"):
                await client.post("/v1/images/generations", {"model": "test", "prompt": "a cat"})
    assert any("DRY-RUN" in r.message for r in caplog.records)
    assert any("v1/images/generations" in r.message for r in caplog.records)


async def test_dry_run_logs_payload(caplog):
    import logging
    client = await _make_client()
    with patch.dict(os.environ, {"CFGPU_DRY_RUN": "1"}):
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_mock_session()):
            with caplog.at_level(logging.INFO, logger="cfgpu_mcp.client.cfgpu_client"):
                await client.post("/v1/video/tasks", {"model": "wan", "content": []})
    assert any("wan" in r.message for r in caplog.records)


async def test_dry_run_still_sends_request():
    client = await _make_client()
    mock_session = _mock_session({"ok": True})
    with patch.dict(os.environ, {"CFGPU_DRY_RUN": "1"}):
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.post("/v1/images/generations", {"prompt": "test"})
    mock_session.request.assert_called_once()
    assert result == {"ok": True}


async def test_no_dry_run_no_log(caplog):
    import logging
    client = await _make_client()
    env = {k: v for k, v in os.environ.items() if k != "CFGPU_DRY_RUN"}
    with patch.dict(os.environ, env, clear=True):
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_mock_session()):
            with caplog.at_level(logging.INFO, logger="cfgpu_mcp.client.cfgpu_client"):
                await client.post("/v1/images/generations", {"prompt": "test"})
    assert not any("DRY-RUN" in r.message for r in caplog.records)


async def test_dry_run_log_contains_full_url(caplog):
    import logging
    client = await _make_client()
    with patch.dict(os.environ, {"CFGPU_DRY_RUN": "1"}):
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_mock_session()):
            with caplog.at_level(logging.INFO, logger="cfgpu_mcp.client.cfgpu_client"):
                await client.post("/v1/images/generations", {"prompt": "test"})
    assert any("https://api.example.com/v1/images/generations" in r.message for r in caplog.records)
