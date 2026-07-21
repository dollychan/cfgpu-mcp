from __future__ import annotations

import json
from typing import Any

from cfgpu_mcp.tool_registry import _REGISTRY, _TOOL_TASK_TYPE, apply_model_enum
from cfgpu_mcp.agent.dispatcher import dispatch_tool


def get_openai_tools(
    task_types: list[str] | None = None,
    tools: list[str] | None = None,
) -> list[dict]:
    """Return tool schemas in OpenAI API format.

    Args:
        task_types: ["image"], ["video"], or None for all
        tools: explicit allowlist of tool names, or None for all
    """
    result = []
    for name, model_cls in _REGISTRY:
        if tools is not None and name not in tools:
            continue
        tool_type = _TOOL_TASK_TYPE.get(name)
        if task_types is not None and tool_type and tool_type not in task_types:
            continue
        schema = apply_model_enum(model_cls.model_json_schema(), name)
        schema.pop("title", None)
        schema.pop("description", None)
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (model_cls.__doc__ or "").strip(),
                "parameters": schema,
            },
        })
    return result


async def openai_dispatch_tool(
    name: str,
    arguments: str | dict[str, Any],
) -> Any:
    """Dispatch an OpenAI tool_call to the corresponding service function.

    Args:
        name: tool_call.function.name from OpenAI response
        arguments: tool_call.function.arguments (JSON string or already-parsed dict)
    """
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    return await dispatch_tool(name, arguments)
