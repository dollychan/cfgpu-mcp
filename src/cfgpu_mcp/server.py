from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp import config
from cfgpu_mcp.tool_registry import apply_media_annotations, apply_model_enum, get_field_descriptions
from cfgpu_mcp.tools import generate, models, tasks, understand


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


mcp = FastMCP("cfdream", lifespan=_lifespan)

generate.register(mcp)
understand.register(mcp)
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


def _inject_model_enums(server: FastMCP) -> None:
    """Pin each generation / understand tool's ``model`` parameter to the registry's
    real model ids, so MCP clients cannot hallucinate a non-existent model_id.

    FastMCP builds the schema from the wrapper's bare ``model: str | list[str]``
    signature — an open string that invites LLMs to invent plausible-looking ids
    (e.g. ``qwen-3-vl-plus``). Delegates to the shared ``apply_model_enum`` so the MCP
    (Mode A) and Anthropic / OpenAI / LangGraph (Mode B) exposures stay in lockstep.
    """
    for tool in server._tool_manager.list_tools():
        apply_model_enum(tool.parameters, tool.name)


def _inject_media_annotations(server: FastMCP) -> None:
    """Stamp the ``x-cfgpu-media`` annotation onto every material slot's schema property.

    Same reason as ``_inject_param_descriptions``: FastMCP builds the input schema from
    the wrapper's bare signature, so the Pydantic ``json_schema_extra`` that the Mode B /
    OpenAI / LangGraph schemas get for free never reaches the MCP schema. Without it an
    MCP host cannot tell which parameters carry media references, and one that maintains
    its own reference layer is left inspecting every string argument by shape.
    """
    for tool in server._tool_manager.list_tools():
        apply_media_annotations(tool.parameters, tool.name)


_inject_param_descriptions(mcp)
_inject_model_enums(mcp)
_inject_media_annotations(mcp)


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
