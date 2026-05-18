import pytest
from cfgpu_mcp.tool_registry import get_anthropic_tools, _REGISTRY


def test_no_filter_returns_all_six_tools():
    tools = get_anthropic_tools()
    assert len(tools) == 6


def test_task_types_image_includes_generate_image():
    tools = get_anthropic_tools(task_types=["image"])
    names = [t["name"] for t in tools]
    assert "generate_image" in names
    assert "generate_video" not in names


def test_task_types_video_includes_generate_video():
    tools = get_anthropic_tools(task_types=["video"])
    names = [t["name"] for t in tools]
    assert "generate_video" in names
    assert "generate_image" not in names


def test_task_type_filter_keeps_generic_tools():
    tools = get_anthropic_tools(task_types=["image"])
    names = [t["name"] for t in tools]
    assert "task_status" in names
    assert "task_wait" in names
    assert "list_models" in names
    assert "get_model_card" in names


def test_explicit_tools_filter():
    tools = get_anthropic_tools(tools=["generate_image", "task_wait"])
    names = [t["name"] for t in tools]
    assert names == ["generate_image", "task_wait"]


def test_task_types_and_tools_intersection():
    tools = get_anthropic_tools(task_types=["video"], tools=["generate_image", "generate_video"])
    names = [t["name"] for t in tools]
    assert names == ["generate_video"]


def test_each_tool_has_nonempty_description():
    for tool in get_anthropic_tools():
        assert tool["description"]


def test_each_tool_has_valid_input_schema():
    for tool in get_anthropic_tools():
        schema = tool["input_schema"]
        assert schema.get("type") == "object"
        assert "properties" in schema


def test_generate_video_schema_has_reference_fields():
    tools = get_anthropic_tools(tools=["generate_video"])
    props = tools[0]["input_schema"]["properties"]
    assert "reference_videos" in props
    assert "reference_audios" in props
    assert "reference_images" in props


def test_generate_image_schema_has_model_specific():
    tools = get_anthropic_tools(tools=["generate_image"])
    props = tools[0]["input_schema"]["properties"]
    assert "model_specific" in props


def test_generate_video_schema_has_model_specific():
    tools = get_anthropic_tools(tools=["generate_video"])
    props = tools[0]["input_schema"]["properties"]
    assert "model_specific" in props


