from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp import config
from cfgpu_mcp.tool_registry import get_field_descriptions
from cfgpu_mcp.tools import generate, models, tasks

mcp = FastMCP("cfgpu")

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
    import asyncio
    import atexit
    import logging
    import os

    log_level = os.getenv("CFGPU_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, log_level, logging.WARNING)
    logging.basicConfig(level=level, force=True)

    atexit.register(lambda: asyncio.run(config.close()))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
