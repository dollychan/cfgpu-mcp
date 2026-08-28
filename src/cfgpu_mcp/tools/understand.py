from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import vision as vision_service
from cfgpu_mcp.tool_registry import RegionSpec, reshape_vision_result, split_structured


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def understand_vision(
        prompt: str,
        model: str | list[str] = "auto",
        images: Optional[list[str]] = None,
        video: Optional[str] = None,
        regions: Optional[list[RegionSpec]] = None,
        image_refs: Optional[list[str]] = None,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        return_metadata: bool = True,
        model_specific: Optional[dict] = None,
        validate_only: bool = False,
    ) -> dict:
        """Understand and reason over images and video using CFGPU vision-language models."""
        try:
            result = await vision_service.understand_vision(
                prompt=prompt,
                model=model,
                images=images,
                video=video,
                regions=regions,
                image_refs=image_refs,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                return_metadata=return_metadata,
                model_specific=model_specific,
                validate_only=validate_only,
            )
        except Exception as e:
            return tool_error_dict(e)
        # Lean LLM-facing content: {id, model, message}. The chain-of-thought,
        # token usage, and echoed request payload are client-only — routed to
        # structuredContent so they never bloat (and get truncated out of) the
        # model's tool result. See tool_registry.split_structured.
        return split_structured(
            reshape_vision_result(result),
            structured_keys=("reasoning_content", "usage", "payload"),
        )
