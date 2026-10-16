from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import template as template_service
from cfgpu_mcp.tool_registry import TemplateKind, TemplateMedia


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_templates(
        query: Optional[str] = None,
        kind: Optional[TemplateKind] = None,
        media: Optional[TemplateMedia] = None,
        genres: Optional[list[str]] = None,
        scenes: Optional[list[str]] = None,
        styles: Optional[list[str]] = None,
        model: Optional[str] = None,
        limit: int = 10,
        cursor: int = 0,
    ) -> dict:
        """Search the shared library of official prompt templates, worked cases and style directions; returns cards, so call get_template for the full content."""
        try:
            return await template_service.search_templates(
                query=query,
                kind=kind,
                media=media,
                genres=genres,
                scenes=scenes,
                styles=styles,
                model=model,
                limit=limit,
                cursor=cursor,
            )
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def get_template(template_id: str, version: Optional[int] = None) -> dict:
        """Get one template's full content by id; fill each [placeholder] with the user's specifics and keep the user's own decisions."""
        try:
            return await template_service.get_template(template_id, version)
        except Exception as e:
            return tool_error_dict(e)
