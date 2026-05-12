from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.service import task as task_service


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def task_status(task_id: str) -> dict:
        """Query the status of an async generation task."""
        return await task_service.get_status(task_id)

    @mcp.tool()
    async def task_wait(task_id: str, timeout: Optional[int] = None) -> dict:
        """Wait for an async generation task to complete and return the result."""
        return await task_service.wait_for_task(task_id, timeout)
