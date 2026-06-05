import asyncio
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.client.cfgpu_client import (
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


def test_timeout_overridable_via_env():
    with patch.dict(os.environ, {"CFGPU_HTTP_TIMEOUT": "5", "CFGPU_CONNECT_TIMEOUT": "2"}):
        client = _client()
    assert client._timeout.total == 5.0
    assert client._timeout.connect == 2.0


def test_invalid_timeout_env_falls_back_to_default():
    with patch.dict(os.environ, {"CFGPU_HTTP_TIMEOUT": "not-a-number"}):
        client = _client()
    assert client._timeout.total == DEFAULT_HTTP_TIMEOUT


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
