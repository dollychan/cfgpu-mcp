from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.tool_registry import annotate_artifact, split_structured


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def task_status(task_id: str) -> dict:
        """Query the status of an async generation task."""
        try:
            return split_structured(
                annotate_artifact(await task_service.get_status(task_id)),
                structured_keys=("usage", "payload"),
            )
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def task_wait(task_id: str, timeout: Optional[int] = None) -> dict:
        """Wait for an async generation task to complete and return the result."""
        try:
            return split_structured(
                annotate_artifact(await task_service.wait_for_task(task_id, timeout)),
                structured_keys=("usage", "payload"),
            )
        except Exception as e:
            return tool_error_dict(e)
