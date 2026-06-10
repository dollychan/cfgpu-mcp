from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp import config
from cfgpu_mcp.tool_registry import get_field_descriptions
from cfgpu_mcp.tools import generate, models, tasks


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Close shared resources (aiohttp session, DB) on shutdown.

    Runs inside the server's own event loop, so the aiohttp ClientSession is
    closed on the same loop it was created on. Closing it from an atexit
    callback via asyncio.run() would spin up a new loop and trip
    "Event loop is closed" warnings at exit.

    Only stdio (one session per process) closes here. Under streamable-http this
    lifespan runs per session — per request in stateless mode — so the shared
    singletons must outlive it; HTTP cleanup is done once by
    RequestContextMiddleware on process shutdown.
    """
    try:
        yield
    finally:
        if config.get_settings().transport == "stdio":
            await config.close()


mcp = FastMCP("cfgpu", lifespan=_lifespan)

generate.register(mcp)
tasks.register(mcp)
models.register(mcp)


def _inject_param_descriptions(server: FastMCP) -> None:
    """FastMCP builds each tool's input schema from the wrapper's bare signature, which
    carries no per-parameter docs. Backfill them from the Pydantic models in
    tool_registry (the single source of truth) so MCP clients see the same guidance as
    the Mode B / OpenAI / LangGraph schemas — without duplicating the text in the wrappers.
    """
    for tool in server._tool_manager.list_tools():
        props = tool.parameters.get("properties", {})
        for name, description in get_field_descriptions(tool.name).items():
            if name in props:
                props[name].setdefault("description", description)


_inject_param_descriptions(mcp)


def main() -> None:
    import logging
    import os

    log_level = os.getenv("CFGPU_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, log_level, logging.WARNING)
    logging.basicConfig(level=level, force=True)

    settings = config.get_settings()
    if settings.transport == "streamable-http":
        import uvicorn

        from cfgpu_mcp.http_app import build_http_app

        app = build_http_app(mcp, settings)
        uvicorn.run(
            app,
            host=settings.http.host,
            port=settings.http.port,
            log_level=log_level.lower(),
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
