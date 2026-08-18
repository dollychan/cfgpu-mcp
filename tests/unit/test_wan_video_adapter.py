import pytest
from cfgpu_mcp.adapters.wan_video import (
    WanVideoAdapter,
    WanVideoR2VAdapter,
    WanVideoT2VAdapter,
    WanVideoEditAdapter,
    Wan26VideoT2VAdapter,
    Wan26VideoI2VAdapter,
    Wan26VideoR2VAdapter,
)
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _cfg(adapter_id, cfgpu_model_id, caps, timeout=400):
    return {
        "adapter_id": adapter_id,
        "display_name": adapter_id,
        "cfgpu_model_id": cfgpu_model_id,
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": set(caps),
        "cost_tier": 3,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": timeout},
    }


def _make_adapter() -> WanVideoAdapter:
    config = {
        "adapter_id": "wan-2-7-i2v",
        "display_name": "万相 2.7 (图生视频)",
        "cfgpu_model_id": "wan2.7-i2v",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"image_to_video"},
        "cost_tier": 3,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 400},
    }
    return WanVideoAdapter.from_config(config)


def _make_r2v_adapter() -> WanVideoR2VAdapter:
    config = {
        "adapter_id": "wan-2-7-r2v",
        "display_name": "万相 2.7 (参考生视频)",
        "cfgpu_model_id": "wan2.7-r2v",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"multi_modal_reference"},
        "cost_tier": 3,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 500},
    }
    return WanVideoR2VAdapter.from_config(config)


def test_payload_nested_envelope():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="一只猫在草地上奔跑",
        first_frame="https://example.com/cat.jpg",
        resolution="720p",
        duration_seconds=5,
    )
    payload = adapter.build_payload(req)
    assert payload["model"] == "wan2.7-i2v"
    assert payload["input"]["prompt"] == "一只猫在草地上奔跑"
    assert payload["input"]["media"] == [
        {"type": "first_frame", "url": "https://example.com/cat.jpg"}
    ]
    assert payload["parameters"] == {
        "resolution": "720P",
        "prompt_extend": True,
        "watermark": False,
        "duration": 5,
    }


def test_i2v_parameters_omit_ratio_but_map_prompt_extend_and_watermark():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://example.com/f.jpg",
        aspect_ratio="9:16",
        prompt_extend=False,
        watermark=True,
        duration_seconds=15,
    )

    assert adapter.build_payload(req)["parameters"] == {
        "resolution": "720P",
        "prompt_extend": False,
        "watermark": True,
        "duration": 15,
    }


def test_model_specific_merges_at_top_level():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://example.com/f.jpg",
        model_specific={"parameters": {"resolution": "1080P", "duration": 8}},
    )
    payload = adapter.build_payload(req)
    assert payload["parameters"] == {"resolution": "1080P", "duration": 8}


def test_parse_response_reads_output_video_url():
    # Poll response is the DashScope output-nested envelope (camelCase, UPPERCASE status).
    adapter = _make_adapter()
    resp = {
        "model": "wan2.7-i2v",
        "output": {
            "taskId": "cgt-123",
            "taskStatus": "SUCCEEDED",
            "videoUrl": "https://cdn/v.mp4",
            "seed": 42,
        },
        "usage": {"duration": 5, "outputVideoDuration": 5, "sr": 1080, "ratio": "9:16"},
    }
    assert adapter.extract_status(resp) == "succeeded"
    result = adapter.parse_response(resp)
    assert result.urls == ["https://cdn/v.mp4"]
    assert result.task_id == "cgt-123"
    assert result.seed == 42
    assert result.aspect_ratio == "9:16"
    assert result.usage == {"duration": 5, "outputVideoDuration": 5, "sr": 1080, "ratio": "9:16"}


def test_parse_response_real_poll_payload():
    """Pins the authoritative 万相 poll response (see models/wan-2-7-t2v/card.md).

    The billing-relevant values arrive as a ready-made per-second `usage`
    (`duration` + `sr`), so unlike Kling this family needs no synthesis — the object
    is passed through untouched.
    """
    adapter = _make_adapter()
    resp = {
        "requestId": "c6b9559f-4c28-98b0-86ea-2ed499172652",
        "model": "wan2.7-t2v",
        "output": {
            "taskId": "36598b68-c4f5-423c-92a1-2d144692c1d0",
            "taskStatus": "SUCCEEDED",
            "submitTime": "2026-06-30 18:07:20.235",
            "scheduledTime": "2026-06-30 18:07:20.275",
            "endTime": "2026-06-30 18:10:08.044",
            "origPrompt": "一段紧张刺激的侦探追查故事...",
            "videoUrl": "https://dashscope-a717.oss-accelerate.aliyuncs.com/1d/84/v.mp4?Expires=1782900606",
        },
        "usage": {
            "duration": 5,
            "inputVideoDuration": 0,
            "outputVideoDuration": 5,
            "videoCount": 1,
            "sr": 720,
            "ratio": "16:9",
        },
    }
    assert adapter.extract_status(resp) == "succeeded"
    result = adapter.parse_response(resp)
    assert result.urls == [resp["output"]["videoUrl"]]
    assert result.task_id == "36598b68-c4f5-423c-92a1-2d144692c1d0"
    assert result.usage == resp["usage"]          # passed through verbatim
    assert result.usage["duration"] == 5          # 计费时长（秒）
    assert result.usage["sr"] == 720              # 分辨率档位（短边）
    assert result.aspect_ratio == "16:9"          # from usage.ratio
    assert result.seed is None                    # this envelope carries no seed


def test_extract_task_id_from_create_response():
    # Create response uses snake_case keys nested under output.
    adapter = _make_adapter()
    create = {"output": {"task_status": "PENDING", "task_id": "cgt-123"}, "request_id": "r1"}
    assert adapter.extract_task_id(create) == "cgt-123"


def test_requires_first_frame():
    adapter = _make_adapter()
    ok, reason = adapter.supports(GenerateVideoInput(prompt="text only"))
    assert not ok
    assert "first_frame" in reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first_frame": "https://f", "last_frame": "https://l"},
        {"first_frame": "https://f", "reference_images": ["https://r"]},
        {"first_frame": "https://f", "reference_videos": ["https://v"]},
        {"first_frame": "https://f", "duration_seconds": -1},
    ],
)
def test_unsupported_scenes_rejected(kwargs):
    adapter = _make_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert not ok


def test_simple_i2v_supported():
    adapter = _make_adapter()
    ok, reason = adapter.supports(
        GenerateVideoInput(prompt="x", first_frame="https://f", duration_seconds=5)
    )
    assert ok, reason


# ── R2V (reference-to-video) ─────────────────────────────────────────────────


def test_r2v_media_videos_then_images_in_order():
    adapter = _make_r2v_adapter()
    req = GenerateVideoInput(
        prompt="视频2抱着图片3...",
        reference_videos=["https://v1.mp4", "https://v2.mp4"],
        reference_images=["https://i3.png"],
        resolution="720p",
        duration_seconds=5,
    )
    payload = adapter.build_payload(req)
    assert payload["model"] == "wan2.7-r2v"
    assert payload["input"]["media"] == [
        {"type": "reference_video", "url": "https://v1.mp4"},
        {"type": "reference_video", "url": "https://v2.mp4"},
        {"type": "reference_image", "url": "https://i3.png"},
    ]
    assert payload["parameters"] == {
        "resolution": "720P",
        "ratio": "16:9",
        "prompt_extend": True,
        "watermark": False,
        "duration": 5,
    }


def test_r2v_inherits_output_envelope_parse():
    adapter = _make_r2v_adapter()
    result = adapter.parse_response(
        {"output": {"taskId": "cgt-9", "videoUrl": "https://v.mp4"}, "usage": {"ratio": "16:9"}}
    )
    assert result.urls == ["https://v.mp4"]
    assert result.task_id == "cgt-9"
    assert result.aspect_ratio == "16:9"


def test_r2v_image_only_supported():
    adapter = _make_r2v_adapter()
    ok, reason = adapter.supports(
        GenerateVideoInput(prompt="x", reference_images=["https://i"], duration_seconds=5)
    )
    assert ok, reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first_frame": "https://f"},                                  # no first/last frame
        {"reference_videos": ["https://v"], "last_frame": "https://l"},
        {"reference_videos": ["https://v"], "reference_audios": ["https://a"]},
        {},                                                            # no reference media at all
        {"reference_images": ["https://i"], "duration_seconds": -1},   # smart duration
    ],
)
def test_r2v_unsupported_scenes_rejected(kwargs):
    adapter = _make_r2v_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert not ok


# ── T2V (text-to-video) ──────────────────────────────────────────────────────


def _make_t2v_adapter() -> WanVideoT2VAdapter:
    config = {
        "adapter_id": "wan-2-7-t2v",
        "display_name": "万相 2.7 (文生视频)",
        "cfgpu_model_id": "wan2.7-t2v",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"text_to_video"},
        "cost_tier": 3,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 400},
    }
    return WanVideoT2VAdapter.from_config(config)


def test_t2v_payload_has_no_media_key():
    adapter = _make_t2v_adapter()
    req = GenerateVideoInput(prompt="雨夜的纽约街头", resolution="720p", duration_seconds=5)
    payload = adapter.build_payload(req)
    assert payload["model"] == "wan2.7-t2v"
    assert payload["input"] == {"prompt": "雨夜的纽约街头"}
    assert "media" not in payload["input"]
    assert payload["parameters"] == {
        "resolution": "720P",
        "ratio": "16:9",
        "prompt_extend": True,
        "watermark": False,
        "duration": 5,
    }


def test_t2v_maps_supported_ratio():
    adapter = _make_t2v_adapter()
    req = GenerateVideoInput(prompt="x", aspect_ratio="4:3", duration_seconds=5)

    assert adapter.build_payload(req)["parameters"]["ratio"] == "4:3"


def test_t2v_text_only_supported():
    adapter = _make_t2v_adapter()
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", duration_seconds=5))
    assert ok, reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first_frame": "https://f"},
        {"reference_images": ["https://i"]},
        {"reference_videos": ["https://v"]},
        {"duration_seconds": -1},
    ],
)
def test_t2v_unsupported_scenes_rejected(kwargs):
    adapter = _make_t2v_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert not ok


# ── 万相 2.7 视频编辑 (videoedit) ─────────────────────────────────────────────


def test_videoedit_media_uses_type_video_then_reference_image():
    adapter = WanVideoEditAdapter.from_config(
        _cfg("wan-2-7-videoedit", "wan2.7-videoedit", {"video_edit"}, timeout=500)
    )
    req = GenerateVideoInput(
        prompt="将视频中女孩的衣服替换为图片中的衣服",
        reference_videos=["https://src.mp4"],
        reference_images=["https://clothes.png"],
        resolution="720p",
        duration_seconds=5,
    )
    payload = adapter.build_payload(req)
    assert payload["model"] == "wan2.7-videoedit"
    assert payload["input"]["media"] == [
        {"type": "video", "url": "https://src.mp4"},
        {"type": "reference_image", "url": "https://clothes.png"},
    ]
    assert payload["parameters"] == {
        "resolution": "720P",
        "ratio": "16:9",
        "prompt_extend": True,
        "watermark": False,
        "duration": 5,
    }


@pytest.mark.parametrize(
    "kwargs, ok_expected",
    [
        ({"reference_videos": ["https://v"]}, True),                               # source video only
        ({"reference_videos": ["https://v"], "reference_images": ["https://i"]}, True),
        ({}, False),                                                              # no source video
        ({"reference_images": ["https://i"]}, False),                             # image but no video
        ({"reference_videos": ["https://v1", "https://v2"]}, False),              # >1 source video
        ({"reference_videos": ["https://v"], "first_frame": "https://f"}, False),
        ({"reference_videos": ["https://v"], "duration_seconds": -1}, False),
    ],
)
def test_videoedit_supports(kwargs, ok_expected):
    adapter = WanVideoEditAdapter.from_config(
        _cfg("wan-2-7-videoedit", "wan2.7-videoedit", {"video_edit"}, timeout=500)
    )
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert ok is ok_expected


# ── 万相 2.6 family (flat input keys, no media array) ─────────────────────────


def test_wan26_t2v_flat_input():
    adapter = Wan26VideoT2VAdapter.from_config(_cfg("wan-2-6-t2v", "wan2.6-t2v", {"text_to_video"}))
    payload = adapter.build_payload(
        GenerateVideoInput(prompt="侦探故事", resolution="720p", duration_seconds=5)
    )
    assert payload["model"] == "wan2.6-t2v"
    assert payload["input"] == {"prompt": "侦探故事"}
    assert payload["parameters"] == {
        "resolution": "720P",
        "ratio": "16:9",
        "prompt_extend": True,
        "watermark": False,
        "duration": 5,
    }


def test_wan26_i2v_img_url_and_optional_audio():
    adapter = Wan26VideoI2VAdapter.from_config(
        _cfg("wan-2-6-i2v", "wan2.6-i2v", {"image_to_video", "audio_generate"})
    )
    # with audio
    payload = adapter.build_payload(
        GenerateVideoInput(
            prompt="rap",
            first_frame="https://rap.png",
            reference_audios=["https://rap.mp3"],
            duration_seconds=5,
        )
    )
    assert payload["input"] == {
        "prompt": "rap",
        "img_url": "https://rap.png",
        "audio_url": "https://rap.mp3",
    }
    assert "ratio" not in payload["parameters"]
    # without audio → no audio_url key
    payload2 = adapter.build_payload(
        GenerateVideoInput(prompt="x", first_frame="https://f.png", duration_seconds=5)
    )
    assert payload2["input"] == {"prompt": "x", "img_url": "https://f.png"}


@pytest.mark.parametrize(
    "kwargs, ok_expected",
    [
        ({"first_frame": "https://f"}, True),
        ({"first_frame": "https://f", "reference_audios": ["https://a"]}, True),
        ({}, False),                                                         # no first_frame
        ({"first_frame": "https://f", "reference_images": ["https://i"]}, False),
        ({"first_frame": "https://f", "reference_videos": ["https://v"]}, False),
        ({"first_frame": "https://f", "reference_audios": ["https://a", "https://b"]}, False),
        ({"first_frame": "https://f", "duration_seconds": -1}, False),
    ],
)
def test_wan26_i2v_supports(kwargs, ok_expected):
    adapter = Wan26VideoI2VAdapter.from_config(
        _cfg("wan-2-6-i2v", "wan2.6-i2v", {"image_to_video", "audio_generate"})
    )
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert ok is ok_expected


def test_wan26_r2v_reference_urls_flat_list():
    adapter = Wan26VideoR2VAdapter.from_config(
        _cfg("wan-2-6-r2v", "wan2.6-r2v", {"multi_modal_reference"}, timeout=500)
    )
    payload = adapter.build_payload(
        GenerateVideoInput(
            prompt="character1看电影",
            reference_videos=["https://vace.mp4"],
            reference_images=["https://i.png"],
            duration_seconds=5,
        )
    )
    assert payload["model"] == "wan2.6-r2v"
    # videos first, then images, flat list with no type tags
    assert payload["input"] == {
        "prompt": "character1看电影",
        "reference_urls": ["https://vace.mp4", "https://i.png"],
    }


@pytest.mark.parametrize(
    "kwargs, ok_expected",
    [
        ({"reference_videos": ["https://v"]}, True),
        ({"reference_images": ["https://i"]}, True),
        ({}, False),
        ({"reference_videos": ["https://v"], "first_frame": "https://f"}, False),
        ({"reference_videos": ["https://v"], "reference_audios": ["https://a"]}, False),
        ({"reference_videos": ["https://v"], "duration_seconds": -1}, False),
    ],
)
def test_wan26_r2v_supports(kwargs, ok_expected):
    adapter = Wan26VideoR2VAdapter.from_config(
        _cfg("wan-2-6-r2v", "wan2.6-r2v", {"multi_modal_reference"}, timeout=500)
    )
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert ok is ok_expected
