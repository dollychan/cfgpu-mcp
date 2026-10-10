from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import model as model_service
from cfgpu_mcp.tool_registry import CanonicalTaskId, CapabilityMatch, MediaType, VoiceAge, VoiceGender


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_models(task_type: Optional[str] = None) -> list:
        """List every model with is_async, for operations and debugging. Each row is the same model profile list_model_profiles returns; choose models with list_model_profiles, which filters by task and adds parameter templates."""
        try:
            return await model_service.list_models(task_type)
        except Exception as e:
            return [tool_error_dict(e)]

    @mcp.tool()
    async def list_model_profiles(
        media_type: Optional[MediaType] = None,
        required_tasks: Optional[list[CanonicalTaskId]] = None,
        match: CapabilityMatch = "all",
    ) -> dict:
        """Find models by canonical capabilities; use match='all' for every required task. Video models also list inputs: accepted material slots, limits, and the audio role."""
        try:
            return await model_service.list_model_profiles(media_type, required_tasks, match)
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def list_voice_profiles(
        model_ids: Optional[list[str]] = None,
        language: Optional[str] = None,
        gender: Optional[VoiceGender] = None,
        age: Optional[VoiceAge] = None,
        query: Optional[str] = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> dict:
        """Find voices by language, gender, age, or intent; copy each result's voice into generate_audio.voice, never label."""
        try:
            return await model_service.list_voice_profiles(
                model_ids=model_ids,
                language=language,
                gender=gender,
                age=age,
                query=query,
                limit=limit,
                cursor=cursor,
            )
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def get_model_card(model_name: str) -> str:
        """Get detailed model information, parameters, and usage examples."""
        try:
            return await model_service.get_model_card(model_name)
        except Exception as e:
            d = tool_error_dict(e)
            return f"Error: {d['message']}"
