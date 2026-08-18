import pytest
from unittest.mock import patch

langchain_core = pytest.importorskip("langchain_core", reason="langchain-core not installed")

from langchain_core.tools import StructuredTool

from cfgpu_mcp.agent.langgraph_tools import get_langgraph_tools
from cfgpu_mcp.tool_registry import (
    _TOOL_TASK_TYPE,
    GenerateAudioInput,
    GenerateImageInput,
    GenerateVideoInput,
    UnderstandVisionInput,
    TaskStatusInput,
    TaskWaitInput,
    ListModelsInput,
    GetModelCardInput,
)


_EXPECTED_SCHEMAS = {
    "generate_image": GenerateImageInput,
    "generate_video": GenerateVideoInput,
    "generate_audio": GenerateAudioInput,
    "understand_vision": UnderstandVisionInput,
    "task_status":    TaskStatusInput,
    "task_wait":      TaskWaitInput,
    "list_models":    ListModelsInput,
    "get_model_card": GetModelCardInput,
}


def _json_schema(tool) -> dict:
    """Model-bearing tools carry ``args_schema`` as a stamped JSON-schema *dict* (so the
    dynamic cfgpu_model_id enum can be injected); the rest keep the Pydantic class."""
    schema = tool.args_schema
    return schema if isinstance(schema, dict) else schema.model_json_schema()


# ── return type and count ─────────────────────────────────────────────────────

def test_no_filter_returns_all_tools():
    assert len(get_langgraph_tools()) == len(_EXPECTED_SCHEMAS)


def test_returns_structured_tool_instances():
    for tool in get_langgraph_tools():
        assert isinstance(tool, StructuredTool)


# ── tool metadata ─────────────────────────────────────────────────────────────

def test_tool_names_are_unique_and_nonempty():
    names = [t.name for t in get_langgraph_tools()]
    assert len(names) == len(set(names))
    for name in names:
        assert name


def test_each_tool_has_nonempty_description():
    for tool in get_langgraph_tools():
        assert tool.description


def test_tool_names_match_registry():
    names = {t.name for t in get_langgraph_tools()}
    assert names == set(_EXPECTED_SCHEMAS.keys())


# ── args_schema comes from tool_registry (class for non-model tools, stamped
#    JSON-schema dict for model-bearing tools so the model enum can be injected) ──

def test_args_schema_matches_pydantic_model():
    for tool in get_langgraph_tools():
        expected_cls = _EXPECTED_SCHEMAS[tool.name]
        if tool.name in _TOOL_TASK_TYPE:
            # Model-bearing tools pass a stamped JSON-schema dict, not the class. It
            # still derives from the Pydantic model — same property set — plus a
            # ``model`` enum the raw class schema cannot carry.
            assert isinstance(tool.args_schema, dict), tool.name
            assert set(tool.args_schema["properties"]) == set(
                expected_cls.model_json_schema()["properties"]
            ), tool.name
        else:
            assert tool.args_schema is expected_cls, (
                f"{tool.name}: expected args_schema={expected_cls.__name__}, "
                f"got {tool.args_schema}"
            )


def test_generate_image_schema_requires_prompt():
    tool = next(t for t in get_langgraph_tools() if t.name == "generate_image")
    schema = _json_schema(tool)
    assert "prompt" in schema.get("required", [])


def test_generate_video_schema_has_reference_fields():
    tool = next(t for t in get_langgraph_tools() if t.name == "generate_video")
    props = _json_schema(tool)["properties"]
    assert "reference_videos" in props
    assert "reference_audios" in props
    assert props["prompt_extend"]["default"] is True


# ── coroutine is async ────────────────────────────────────────────────────────

def test_tools_are_async():
    import asyncio
    for tool in get_langgraph_tools():
        assert asyncio.iscoroutinefunction(tool.coroutine), (
            f"{tool.name} coroutine must be async"
        )


def test_sync_func_is_none_for_async_tools():
    # StructuredTool created with coroutine= should not have a sync func
    for tool in get_langgraph_tools():
        assert tool.func is None, f"{tool.name} should not have a sync func"


# ── coroutine points to the correct service function ─────────────────────────

def test_generate_image_coroutine_is_image_service():
    from cfgpu_mcp.service import image as svc
    tool = next(t for t in get_langgraph_tools() if t.name == "generate_image")
    assert tool.coroutine is svc.generate_image


def test_generate_video_coroutine_is_video_service():
    from cfgpu_mcp.service import video as svc
    tool = next(t for t in get_langgraph_tools() if t.name == "generate_video")
    assert tool.coroutine is svc.generate_video


def test_task_status_coroutine_is_task_get_status():
    from cfgpu_mcp.service import task as svc
    tool = next(t for t in get_langgraph_tools() if t.name == "task_status")
    assert tool.coroutine is svc.get_status


def test_list_models_coroutine_is_model_service():
    from cfgpu_mcp.service import model as svc
    tool = next(t for t in get_langgraph_tools() if t.name == "list_models")
    assert tool.coroutine is svc.list_models


# ── filtering ─────────────────────────────────────────────────────────────────

def test_task_types_image_excludes_generate_video():
    names = [t.name for t in get_langgraph_tools(task_types=["image"])]
    assert "generate_image" in names
    assert "generate_video" not in names


def test_task_types_video_excludes_generate_image():
    names = [t.name for t in get_langgraph_tools(task_types=["video"])]
    assert "generate_video" in names
    assert "generate_image" not in names


def test_task_type_filter_keeps_generic_tools():
    names = [t.name for t in get_langgraph_tools(task_types=["image"])]
    for generic in ("task_status", "task_wait", "list_models", "get_model_card"):
        assert generic in names


def test_explicit_tools_filter():
    tools = get_langgraph_tools(tools=["generate_image", "task_wait"])
    names = [t.name for t in tools]
    assert names == ["generate_image", "task_wait"]


def test_task_types_and_tools_intersection():
    tools = get_langgraph_tools(task_types=["video"], tools=["generate_image", "generate_video"])
    names = [t.name for t in tools]
    assert names == ["generate_video"]


def test_empty_explicit_allowlist_returns_nothing():
    assert get_langgraph_tools(tools=[]) == []


# ── import error when langchain-core missing ──────────────────────────────────

def test_import_error_message_is_informative():
    with patch.dict("sys.modules", {"langchain_core": None, "langchain_core.tools": None}):
        import importlib
        import cfgpu_mcp.agent.langgraph_tools as lg_mod
        importlib.reload(lg_mod)
        with pytest.raises(ImportError, match="langchain-core"):
            lg_mod.get_langgraph_tools()
