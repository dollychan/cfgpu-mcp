from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import vision as vision_service


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def understand_vision(
        prompt: str,
        model: str | list[str] = "auto",
        images: Optional[list[str]] = None,
        video: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        return_metadata: bool = True,
        model_specific: Optional[dict] = None,
    ) -> dict:
        """Understand and reason over images and video using CFGPU vision-language models."""
        try:
            return await vision_service.understand_vision(
                prompt=prompt,
                model=model,
                images=images,
                video=video,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                return_metadata=return_metadata,
                model_specific=model_specific,
            )
        except Exception as e:
            return tool_error_dict(e)
