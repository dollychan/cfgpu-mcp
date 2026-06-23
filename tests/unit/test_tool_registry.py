import pytest
from cfgpu_mcp.tool_registry import get_anthropic_tools, _REGISTRY


def test_no_filter_returns_all_tools():
    tools = get_anthropic_tools()
    names = {t["name"] for t in tools}
    assert len(tools) == 8
    assert "understand_vision" in names


def test_task_types_understand_includes_understand_vision():
    tools = get_anthropic_tools(task_types=["understand"])
    names = [t["name"] for t in tools]
    assert "understand_vision" in names
    assert "generate_image" not in names
    assert "generate_video" not in names


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




# ── Schema validation for newly-exposed capabilities ──────────────────────────

def test_video_duration_accepts_smart_minus_one():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(prompt="x", duration_seconds=-1)
    assert req.duration_seconds == -1


def test_video_duration_rejects_out_of_range():
    from pydantic import ValidationError
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    with pytest.raises(ValidationError):
        GenerateVideoInput(prompt="x", duration_seconds=16)
    with pytest.raises(ValidationError):
        GenerateVideoInput(prompt="x", duration_seconds=2)


def test_video_resolution_accepts_1080p():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(prompt="x", resolution="1080p")
    assert req.resolution == "1080p"


def test_image_n_accepts_group_size():
    from cfgpu_mcp.tool_registry import GenerateImageInput
    req = GenerateImageInput(prompt="x", n=15)
    assert req.n == 15


def test_image_n_rejects_out_of_range():
    from pydantic import ValidationError
    from cfgpu_mcp.tool_registry import GenerateImageInput
    with pytest.raises(ValidationError):
        GenerateImageInput(prompt="x", n=0)
    with pytest.raises(ValidationError):
        GenerateImageInput(prompt="x", n=16)


def test_image_schema_exposes_n():
    schema = next(t for t in get_anthropic_tools() if t["name"] == "generate_image")
    assert "n" in schema["input_schema"]["properties"]


def test_annotate_artifact_flags_top_level_urls():
    from cfgpu_mcp.tool_registry import annotate_artifact
    out = annotate_artifact({"urls": ["https://x"], "expires_at": None})
    assert out["artifact"] is True


def test_annotate_artifact_flags_nested_task_result():
    from cfgpu_mcp.tool_registry import annotate_artifact
    out = annotate_artifact(
        {"task_id": "t", "status": "succeeded", "result": {"urls": ["https://y"]}, "error": None}
    )
    assert out["artifact"] is True


def test_annotate_artifact_skips_results_without_urls():
    from cfgpu_mcp.tool_registry import annotate_artifact
    # empty urls, pending no-wait, running task, and error dicts get no flag
    for d in (
        {"urls": [], "expires_at": None},
        {"task_id": "t", "status": "pending"},
        {"task_id": "t", "status": "running", "result": None, "error": None},
        {"error": True, "message": "oops"},
    ):
        assert "artifact" not in annotate_artifact(d)


def test_annotate_artifact_passes_through_non_dict():
    from cfgpu_mcp.tool_registry import annotate_artifact
    assert annotate_artifact("not a dict") == "not a dict"
    assert annotate_artifact(None) is None
