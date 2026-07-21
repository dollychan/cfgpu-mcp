from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.tool_registry import _REGISTRY, _TOOL_TASK_TYPE, apply_model_enum

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool


def _get_service_coroutines() -> dict[str, Any]:
    from cfgpu_mcp.service import audio as audio_svc
    from cfgpu_mcp.service import image as image_svc
    from cfgpu_mcp.service import model as model_svc
    from cfgpu_mcp.service import task as task_svc
    from cfgpu_mcp.service import video as video_svc
    from cfgpu_mcp.service import vision as vision_svc

    return {
        "generate_image": image_svc.generate_image,
        "generate_video": video_svc.generate_video,
        "generate_audio": audio_svc.generate_audio,
        "understand_vision": vision_svc.understand_vision,
        "task_status":    task_svc.get_status,
        "task_wait":      task_svc.wait_for_task,
        "list_models":    model_svc.list_models,
        "get_model_card": model_svc.get_model_card,
    }


def get_langgraph_tools(
    task_types: list[str] | None = None,
    tools: list[str] | None = None,
) -> list["StructuredTool"]:
    """Return tools as LangChain StructuredTool objects for use with LangGraph.

    Each tool's args_schema is the corresponding Pydantic model from tool_registry,
    so schema definitions stay in a single place.

    Args:
        task_types: ["image"], ["video"], or None for all
        tools: explicit allowlist of tool names, or None for all

    Requires:
        pip install langchain-core
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as e:
        raise ImportError(
            "langchain-core is required for LangGraph integration: "
            "pip install langchain-core"
        ) from e

    coroutines = _get_service_coroutines()
    result = []
    for name, model_cls in _REGISTRY:
        if tools is not None and name not in tools:
            continue
        tool_type = _TOOL_TASK_TYPE.get(name)
        if task_types is not None and tool_type and tool_type not in task_types:
            continue
        # Model-bearing tools pass a stamped JSON-schema dict so the dynamic
        # cfgpu_model_id enum reaches the bound LLM (a Pydantic class cannot carry the
        # registry-driven enum). The service layer re-validates against the Pydantic
        # model, so dropping LangChain-level class validation here is safe. Non-model
        # tools keep the Pydantic class unchanged.
        if tool_type is not None:
            args_schema: Any = apply_model_enum(model_cls.model_json_schema(), name)
        else:
            args_schema = model_cls
        result.append(
            StructuredTool.from_function(
                coroutine=coroutines[name],
                name=name,
                description=(model_cls.__doc__ or "").strip(),
                args_schema=args_schema,
            )
        )
    return result
