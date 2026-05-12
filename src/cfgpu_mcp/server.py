from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp import config
from cfgpu_mcp.tools import generate, models, tasks

mcp = FastMCP("cfgpu-mcp")

generate.register(mcp)
tasks.register(mcp)
models.register(mcp)


def main() -> None:
    import asyncio
    import atexit

    atexit.register(lambda: asyncio.get_event_loop().run_until_complete(config.close()))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
