from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.service import model as model_service


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_models(task_type: Optional[str] = None) -> list:
        """List available CFGPU models with their capabilities and identifiers."""
        return await model_service.list_models(task_type)

    @mcp.tool()
    async def get_model_card(model_name: str) -> str:
        """Get detailed model information, parameters, and usage examples."""
        return await model_service.get_model_card(model_name)
