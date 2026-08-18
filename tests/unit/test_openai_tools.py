import json
import pytest
from unittest.mock import AsyncMock, patch

from cfgpu_mcp.agent.openai_tools import get_openai_tools, openai_dispatch_tool


# ── schema structure ─────────────────────────────────────────────────────────

def test_no_filter_returns_all_tools():
    tools = get_openai_tools()
    assert len(tools) == 8
    assert "understand_vision" in {t["function"]["name"] for t in tools}


def test_each_tool_has_type_function():
    for tool in get_openai_tools():
        assert tool["type"] == "function"


def test_each_tool_has_nonempty_name():
    names = [t["function"]["name"] for t in get_openai_tools()]
    assert len(names) == len(set(names)), "tool names must be unique"
    for name in names:
        assert name


def test_each_tool_has_nonempty_description():
    for tool in get_openai_tools():
        assert tool["function"]["description"]


def test_parameters_is_valid_json_schema():
    for tool in get_openai_tools():
        params = tool["function"]["parameters"]
        assert params.get("type") == "object"
        assert "properties" in params


def test_parameters_has_no_top_level_title():
    for tool in get_openai_tools():
        assert "title" not in tool["function"]["parameters"]


def test_parameters_has_no_top_level_description():
    # description lives on function, not duplicated inside parameters
    for tool in get_openai_tools():
        assert "description" not in tool["function"]["parameters"]


def test_generate_image_prompt_is_required():
    tools = get_openai_tools(tools=["generate_image"])
    params = tools[0]["function"]["parameters"]
    assert "prompt" in params["required"]


def test_generate_image_has_model_specific():
    tools = get_openai_tools(tools=["generate_image"])
    assert "model_specific" in tools[0]["function"]["parameters"]["properties"]


def test_generate_video_has_reference_fields():
    tools = get_openai_tools(tools=["generate_video"])
    props = tools[0]["function"]["parameters"]["properties"]
    assert "reference_videos" in props
    assert "reference_audios" in props
    assert "reference_images" in props
    assert props["prompt_extend"]["default"] is True


# ── filtering ─────────────────────────────────────────────────────────────────

def test_task_types_image_excludes_generate_video():
    names = [t["function"]["name"] for t in get_openai_tools(task_types=["image"])]
    assert "generate_image" in names
    assert "generate_video" not in names


def test_task_types_video_excludes_generate_image():
    names = [t["function"]["name"] for t in get_openai_tools(task_types=["video"])]
    assert "generate_video" in names
    assert "generate_image" not in names


def test_task_type_filter_keeps_generic_tools():
    names = [t["function"]["name"] for t in get_openai_tools(task_types=["image"])]
    for generic in ("task_status", "task_wait", "list_models", "get_model_card"):
        assert generic in names


def test_explicit_tools_filter():
    tools = get_openai_tools(tools=["generate_image", "task_wait"])
    names = [t["function"]["name"] for t in tools]
    assert names == ["generate_image", "task_wait"]


def test_task_types_and_tools_intersection():
    tools = get_openai_tools(task_types=["video"], tools=["generate_image", "generate_video"])
    names = [t["function"]["name"] for t in tools]
    assert names == ["generate_video"]


def test_empty_explicit_allowlist_returns_nothing():
    assert get_openai_tools(tools=[]) == []


# ── openai_dispatch_tool ──────────────────────────────────────────────────────

async def test_dispatch_with_dict_arguments():
    with patch("cfgpu_mcp.agent.openai_tools.dispatch_tool", new_callable=AsyncMock) as mock:
        mock.return_value = {"urls": ["https://example.com/img.jpg"]}
        result = await openai_dispatch_tool("generate_image", {"prompt": "a cat"})
        mock.assert_awaited_once_with("generate_image", {"prompt": "a cat"})
        assert result["urls"]


async def test_dispatch_with_json_string_arguments():
    args_str = json.dumps({"prompt": "a cat", "model": "auto"})
    with patch("cfgpu_mcp.agent.openai_tools.dispatch_tool", new_callable=AsyncMock) as mock:
        mock.return_value = {"urls": []}
        await openai_dispatch_tool("generate_image", args_str)
        _, called_args = mock.call_args
        # dispatch_tool called with parsed dict, not the raw string
        assert isinstance(mock.call_args.args[1], dict)
        assert mock.call_args.args[1]["prompt"] == "a cat"


async def test_dispatch_routes_to_correct_tool_name():
    with patch("cfgpu_mcp.agent.openai_tools.dispatch_tool", new_callable=AsyncMock) as mock:
        mock.return_value = {}
        await openai_dispatch_tool("list_models", {})
        assert mock.call_args.args[0] == "list_models"


async def test_dispatch_unknown_tool_raises_value_error():
    with pytest.raises(ValueError, match="Unknown tool"):
        await openai_dispatch_tool("nonexistent_tool", {})


async def test_dispatch_propagates_result():
    expected = {"urls": ["https://cdn.example.com/video.mp4"], "expires_at": "2026-05-13T00:00:00Z"}
    with patch("cfgpu_mcp.agent.openai_tools.dispatch_tool", new_callable=AsyncMock) as mock:
        mock.return_value = expected
        result = await openai_dispatch_tool("generate_video", {"prompt": "waves"})
        assert result == expected
