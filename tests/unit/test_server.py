import asyncio

import pytest

from cfgpu_mcp import server
from cfgpu_mcp.settings import Settings


def test_lifespan_is_wired_to_mcp():
    # FastMCP stores the lifespan on its low-level server.
    assert server.mcp._mcp_server.lifespan is not None


@pytest.mark.asyncio
async def test_lifespan_closes_resources_on_exit(monkeypatch):
    closed_on = []

    async def fake_close():
        closed_on.append(asyncio.get_running_loop())

    # _lifespan only closes shared resources under stdio (HTTP cleans up
    # elsewhere). Pin stdio so the ambient config.yaml's transport can't leak in.
    monkeypatch.setattr("cfgpu_mcp.config.close", fake_close)
    monkeypatch.setattr("cfgpu_mcp.config.get_settings", lambda: Settings(transport="stdio"))

    async with server._lifespan(server.mcp):
        body_loop = asyncio.get_running_loop()

    # close() runs exactly once, on the same loop as the lifespan body
    # (not a fresh loop from asyncio.run in an atexit hook).
    assert closed_on == [body_loop]
