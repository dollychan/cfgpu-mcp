from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import audio as audio_service
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import model as model_service
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.service import video as video_service


async def dispatch_tool(name: str, inputs: dict[str, Any]) -> Any:
    """Route tool call to the corresponding service function.

    Used in Agent direct mode (Mode B) as a drop-in replacement for MCP protocol.
    Returns an error dict instead of raising so the LLM sees the failure in tool result content.
    """
    try:
        match name:
            case "generate_image":
                return await image_service.generate_image(**inputs)
            case "generate_video":
                return await video_service.generate_video(**inputs)
            case "generate_audio":
                return await audio_service.generate_audio(**inputs)
            case "task_status":
                return await task_service.get_status(**inputs)
            case "task_wait":
                return await task_service.wait_for_task(**inputs)
            case "list_models":
                return await model_service.list_models(**inputs)
            case "get_model_card":
                return await model_service.get_model_card(**inputs)
            case _:
                raise ValueError(f"Unknown tool: {name!r}")
    except ValueError:
        raise  # unknown tool name is a programming error, not an API error
    except Exception as e:
        return tool_error_dict(e)
