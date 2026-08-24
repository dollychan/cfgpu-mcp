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
    assert props["prompt_extend"]["default"] is True


def test_generate_image_schema_has_model_specific():
    tools = get_anthropic_tools(tools=["generate_image"])
    props = tools[0]["input_schema"]["properties"]
    assert "model_specific" in props


def test_generate_video_schema_has_model_specific():
    tools = get_anthropic_tools(tools=["generate_video"])
    props = tools[0]["input_schema"]["properties"]
    assert "model_specific" in props


def test_generate_audio_schema_has_exact_emotion_enum():
    tools = get_anthropic_tools(tools=["generate_audio"])
    emotion = tools[0]["input_schema"]["properties"]["emotion"]
    assert set(emotion["anyOf"][0]["enum"]) == {
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
        "whisper",
    }




# ── Schema validation for newly-exposed capabilities ──────────────────────────

def test_video_duration_accepts_smart_minus_one():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(prompt="x", duration_seconds=-1)
    assert req.duration_seconds == -1


def test_video_duration_rejects_out_of_range():
    from pydantic import ValidationError
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    with pytest.raises(ValidationError):
        GenerateVideoInput(prompt="x", duration_seconds=31)
    with pytest.raises(ValidationError):
        GenerateVideoInput(prompt="x", duration_seconds=2)


def test_video_duration_allows_the_fleet_wide_maximum():
    # The schema caps at the widest model in the fleet (Seedance 2.5 at 30s);
    # narrower ceilings belong to each adapter's supports(), not here.
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    assert GenerateVideoInput(prompt="x", duration_seconds=30).duration_seconds == 30


def test_video_resolution_accepts_1080p():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(prompt="x", resolution="1080p")
    assert req.resolution == "1080p"


def test_video_resolution_accepts_seedance_2_0_4k():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(prompt="x", resolution="4k")
    assert req.resolution == "4k"


def test_video_prompt_is_optional_for_reference_driven_tasks():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    req = GenerateVideoInput(reference_audios=["https://example.com/a.mp3"])
    assert req.prompt == ""


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
    from cfgpu_mcp.tool_registry import _ARTIFACT_DONE_STATUS, annotate_artifact
    out = annotate_artifact({"urls": ["https://x"], "expires_at": None})
    assert out["artifact"] is True
    # terminal hint lets the LLM see generation is done even after MaterialsMiddleware
    # strips urls out of the content → no redundant task_status/task_wait polling.
    assert out["status"] == _ARTIFACT_DONE_STATUS


def test_annotate_artifact_flags_nested_task_result():
    from cfgpu_mcp.tool_registry import _ARTIFACT_DONE_STATUS, annotate_artifact
    out = annotate_artifact(
        {"task_id": "t", "status": "succeeded", "result": {"urls": ["https://y"]}, "error": None}
    )
    assert out["artifact"] is True
    assert out["status"] == _ARTIFACT_DONE_STATUS


def test_annotate_artifact_skips_results_without_urls():
    from cfgpu_mcp.tool_registry import _ARTIFACT_DONE_STATUS, annotate_artifact
    # empty urls, pending no-wait, running task, and error dicts get no flag — and no
    # done-status hint (the raw in-flight status must survive untouched).
    for d in (
        {"urls": [], "expires_at": None},
        {"task_id": "t", "status": "pending"},
        {"task_id": "t", "status": "running", "result": None, "error": None},
        {"error": True, "message": "oops"},
    ):
        out = annotate_artifact(d)
        assert "artifact" not in out
        assert out.get("status") != _ARTIFACT_DONE_STATUS


def test_annotate_artifact_passes_through_non_dict():
    from cfgpu_mcp.tool_registry import annotate_artifact
    assert annotate_artifact("not a dict") == "not a dict"
    assert annotate_artifact(None) is None


# ── stamp_echo (request_id + caption) ────────────────────────────────────────

def test_stamp_echo_adds_when_set():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert stamp_echo({"urls": ["x"]}, request_id="r-1") == {"urls": ["x"], "request_id": "r-1"}


def test_stamp_echo_omitted_when_none():
    from cfgpu_mcp.tool_registry import stamp_echo
    out = stamp_echo({"urls": ["x"]}, request_id=None)
    assert "request_id" not in out


def test_stamp_echo_does_not_clobber_existing():
    from cfgpu_mcp.tool_registry import stamp_echo
    out = stamp_echo({"request_id": "kept"}, request_id="other")
    assert out["request_id"] == "kept"


def test_stamp_echo_passes_through_non_dict():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert stamp_echo("not a dict", request_id="r-1") == "not a dict"


def test_stamp_echo_adds_caption_when_set():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert stamp_echo({"urls": ["x"]}, caption="角色阿雅 第一版") == {
        "urls": ["x"], "caption": "角色阿雅 第一版",
    }


def test_stamp_echo_caption_omitted_when_none():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert "caption" not in stamp_echo({"urls": ["x"]}, caption=None)


def test_stamp_echo_caption_does_not_clobber_existing():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert stamp_echo({"caption": "kept"}, caption="other")["caption"] == "kept"


def test_stamp_echo_carries_both_fields_independently():
    """The two echo fields are independent: supplying one never implies the other."""
    from cfgpu_mcp.tool_registry import stamp_echo
    both = stamp_echo({"urls": ["x"]}, request_id="r-1", caption="cover v1")
    assert both == {"urls": ["x"], "request_id": "r-1", "caption": "cover v1"}
    assert "caption" not in stamp_echo({}, request_id="r-1")
    assert "request_id" not in stamp_echo({}, caption="cover v1")


def test_generate_image_schema_exposes_request_id():
    from cfgpu_mcp.tool_registry import GenerateImageInput
    props = GenerateImageInput.model_json_schema()["properties"]
    assert "request_id" in props
    assert GenerateImageInput(prompt="x").request_id is None


# ── caption field ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, required", [
    ("GenerateImageInput", {"prompt": "x"}),
    ("GenerateVideoInput", {"prompt": "x"}),
    ("GenerateAudioInput", {"text": "x"}),
])
def test_generate_schemas_expose_optional_caption(model, required):
    """All three generate tools carry the label slot; none requires it."""
    import cfgpu_mcp.tool_registry as tr
    cls = getattr(tr, model)
    assert "caption" in cls.model_json_schema()["properties"]
    assert cls(**required).caption is None


def test_understand_vision_has_no_caption():
    """understand_vision returns text, not an artifact — there is nothing to label."""
    from cfgpu_mcp.tool_registry import UnderstandVisionInput
    assert "caption" not in UnderstandVisionInput.model_fields


def test_caption_is_truncated_not_rejected():
    """An over-long label must not fail the call: the caption has no effect on the
    generated media, so costing the caller a whole turn over it would be a bad trade."""
    from cfgpu_mcp.tool_registry import CAPTION_MAX_CHARS, GenerateImageInput
    req = GenerateImageInput(prompt="x", caption="蓝" * (CAPTION_MAX_CHARS + 50))
    assert req.caption == "蓝" * CAPTION_MAX_CHARS


def test_caption_under_the_cap_is_untouched():
    from cfgpu_mcp.tool_registry import GenerateImageInput
    assert GenerateImageInput(prompt="x", caption="cover image v1").caption == "cover image v1"


# ── split_structured / reshape_vision_result ─────────────────────────────────

import json

from mcp.types import CallToolResult

from cfgpu_mcp.tool_registry import reshape_vision_result, split_structured


def test_split_structured_routes_keys_to_structured_content():
    result = {
        "urls": ["https://x"],
        "expires_at": None,
        "task_id": "cgt-1",
        "model_used": "doubao-seedance",
        "seed": 42,
        "artifact": True,
        "usage": {"totalTokens": 100},
        "payload": {"model": "doubao", "prompt": "x"},
    }
    out = split_structured(result, structured_keys=("usage", "payload"))

    assert isinstance(out, CallToolResult)
    assert out.isError is False
    # usage/payload live in structuredContent only (client-facing side channel)
    assert out.structuredContent == {"usage": {"totalTokens": 100}, "payload": {"model": "doubao", "prompt": "x"}}
    # lean content (LLM-facing) keeps everything else and excludes the split keys
    content = json.loads(out.content[0].text)
    assert content == {
        "urls": ["https://x"],
        "expires_at": None,
        "task_id": "cgt-1",
        "model_used": "doubao-seedance",
        "seed": 42,
        "artifact": True,
    }
    assert "usage" not in content and "payload" not in content


def test_split_structured_empty_structured_is_none():
    # async no-wait submit carries neither usage nor payload
    out = split_structured({"task_id": "t", "status": "pending"}, structured_keys=("usage", "payload"))
    assert isinstance(out, CallToolResult)
    assert out.structuredContent is None
    assert json.loads(out.content[0].text) == {"task_id": "t", "status": "pending"}


def test_split_structured_passes_through_error_dict():
    err = {"error": True, "error_type": "invalid_params", "message": "bad", "retryable": False}
    # error dicts stay a plain dict so the LLM still sees the full failure reason
    assert split_structured(err, structured_keys=("usage", "payload")) is err


def test_split_structured_passes_through_non_dict():
    assert split_structured("not a dict", structured_keys=("usage",)) == "not a dict"
    assert split_structured(None, structured_keys=("usage",)) is None


def test_task_result_end_to_end_annotate_then_split():
    # task_status/task_wait success: flat NormalizedResult + payload, same as generate.
    from cfgpu_mcp.tool_registry import _ARTIFACT_DONE_STATUS, annotate_artifact
    service_result = {
        "urls": ["https://cdn.cfgpu.com/vid.mp4"],
        "expires_at": None,
        "task_id": "cgt-1",
        "model_used": "wan-2-0",
        "seed": 7,
        "usage": {"totalTokens": 100},
        "payload": {"model": "wan", "prompt": "x"},
    }
    out = split_structured(annotate_artifact(service_result), structured_keys=("usage", "payload"))

    assert isinstance(out, CallToolResult)
    content = json.loads(out.content[0].text)
    # urls + terminal status hint stay LLM-facing; usage/payload routed to side channel
    assert content["artifact"] is True
    assert content["status"] == _ARTIFACT_DONE_STATUS
    assert content["urls"] == ["https://cdn.cfgpu.com/vid.mp4"]
    assert "usage" not in content and "payload" not in content
    assert set(out.structuredContent) == {"usage", "payload"}


def test_task_result_pending_passes_through_split_without_flag():
    # in-flight poll: no urls → no artifact flag, no done hint, empty side channel.
    from cfgpu_mcp.tool_registry import _ARTIFACT_DONE_STATUS, annotate_artifact
    out = split_structured(
        annotate_artifact({"task_id": "cgt-1", "status": "running"}),
        structured_keys=("usage", "payload"),
    )
    assert isinstance(out, CallToolResult)
    assert out.structuredContent is None
    content = json.loads(out.content[0].text)
    assert content == {"task_id": "cgt-1", "status": "running"}
    assert content["status"] != _ARTIFACT_DONE_STATUS


def test_reshape_vision_result_hoists_message_and_splits_reasoning():
    result = {
        "id": "chatcmpl-1",
        "model": "qwen3.6-plus",
        "message": {"role": "assistant", "content": "The video is...", "reasoning_content": "Let's analyze..."},
        "usage": {"prompt_tokens": 7749},
        "payload": {"model": "qwen", "messages": []},
    }
    reshaped = reshape_vision_result(result)
    assert reshaped["message"] == "The video is..."
    assert reshaped["reasoning_content"] == "Let's analyze..."
    assert reshaped["model"] == "qwen3.6-plus"


def test_reshape_vision_result_non_thinking_model_has_null_reasoning():
    result = {"id": "c", "model": "qwen-vl", "message": {"role": "assistant", "content": "answer"}}
    reshaped = reshape_vision_result(result)
    assert reshaped["message"] == "answer"
    assert reshaped["reasoning_content"] is None


def test_reshape_vision_result_passes_through_error_and_non_dict():
    err = {"error": True, "message": "oops"}
    assert reshape_vision_result(err) is err
    assert reshape_vision_result("x") == "x"


def test_understand_vision_end_to_end_split():
    # The full understand_vision MCP shape: reshape then split.
    service_result = {
        "id": "chatcmpl-1",
        "model": "qwen3.6-plus",
        "message": {"role": "assistant", "content": "answer text", "reasoning_content": "thinking trace"},
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "payload": {"model": "qwen", "messages": [{"role": "user", "content": "..."}], "stream": False},
    }
    out = split_structured(
        reshape_vision_result(service_result),
        structured_keys=("reasoning_content", "usage", "payload"),
    )
    assert isinstance(out, CallToolResult)
    # LLM-facing content is lean: only id, model, message (the answer text)
    assert json.loads(out.content[0].text) == {
        "id": "chatcmpl-1",
        "model": "qwen3.6-plus",
        "message": "answer text",
    }
    # reasoning_content, usage, payload are client-only
    assert set(out.structuredContent) == {"reasoning_content", "usage", "payload"}
    assert out.structuredContent["reasoning_content"] == "thinking trace"


# ── partial group failures reach the caller ─────────────────────────────────


def test_partial_errors_ride_the_llm_facing_content():
    """`partial_errors` explains why the artifact list is shorter than requested, so it
    belongs next to `urls` rather than in the client-only side channel — the model is the
    one that has to decide whether to rewrite the prompt or retry."""
    from cfgpu_mcp.tool_registry import NormalizedResult

    result = NormalizedResult(
        urls=["https://cdn/a.jpg"], expires_at=None, task_id=None, model_used="m",
        seed=None, usage=None,
        partial_errors=[{"index": 1, "code": "content_blocked", "message": "审核不通过"}],
    )
    assert result.to_dict()["partial_errors"][0]["code"] == "content_blocked"
    # and it survives return_metadata=False, which drops metadata but not the reason
    from cfgpu_mcp.tool_registry import lean_result

    assert "partial_errors" in lean_result(result.to_dict(), payload={})


def test_a_partial_result_is_not_announced_as_plain_success():
    """A bare 'Success.' reads as the summary and would be believed. The qualifier has to
    sit next to the shortened url list, not only in a separate field."""
    from cfgpu_mcp.tool_registry import annotate_artifact

    full = annotate_artifact({"urls": ["https://cdn/a.jpg"]})
    partial = annotate_artifact({
        "urls": ["https://cdn/a.jpg"],
        "partial_errors": [{"index": 1, "code": "content_blocked", "message": "x"}],
    })
    assert full["artifact"] is True and partial["artifact"] is True
    assert full["status"].startswith("Success.")
    assert partial["status"].startswith("Partial success.")
    assert "partial_errors" in partial["status"]
