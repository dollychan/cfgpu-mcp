import pytest
from cfgpu_mcp.adapters.kling_video import KlingVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _make_adapter() -> KlingVideoAdapter:
    config = {
        "adapter_id": "kling-video-o1",
        "display_name": "Kling Video O1 (可灵 O1)",
        "cfgpu_model_id": "kling-video-o1",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {
            "text_to_video",
            "image_to_video",
            "first_last_frame",
            "multi_modal_reference",
            "video_edit",
        },
        "cost_tier": 4,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 600},
    }
    return KlingVideoAdapter.from_config(config)


# ── build_payload ────────────────────────────────────────────────────────────

def test_text_only_payload():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="a cat running", duration_seconds=5)
    payload = adapter.build_payload(req)
    assert payload["model"] == "kling-video-o1"
    assert payload["prompt"] == "a cat running"
    assert payload["seconds"] == "5"


def test_seconds_is_string():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=10)
    payload = adapter.build_payload(req)
    assert payload["seconds"] == "10"
    assert isinstance(payload["seconds"], str)


@pytest.mark.parametrize("resolution,ratio,expected", [
    ("720p", "16:9", "1280x720"),
    ("1080p", "16:9", "1920x1080"),
    ("1080p", "9:16", "1080x1920"),
    ("1080p", "1:1", "1080x1080"),
    ("480p", "16:9", "854x480"),
])
def test_size_mapping(resolution, ratio, expected):
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", resolution=resolution, aspect_ratio=ratio)
    payload = adapter.build_payload(req)
    assert payload["size"] == expected


def test_adaptive_aspect_ratio_uses_16_9():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", resolution="1080p", aspect_ratio="adaptive")
    payload = adapter.build_payload(req)
    assert payload["size"] == "1920x1080"


@pytest.mark.parametrize("quality_tier,expected_mode", [
    ("fast", "std"),
    ("balanced", "std"),
    ("best", "pro"),
])
def test_quality_tier_maps_to_mode(quality_tier, expected_mode):
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", quality_tier=quality_tier)
    payload = adapter.build_payload(req)
    assert payload["mode"] == expected_mode


def test_with_audio_maps_to_sound_flag():
    adapter = _make_adapter()
    assert adapter.build_payload(GenerateVideoInput(prompt="x"))["sound"] == "on"
    assert adapter.build_payload(GenerateVideoInput(prompt="x", with_audio=False))["sound"] == "off"


def test_text_only_payload_has_no_media_arrays():
    adapter = _make_adapter()
    payload = adapter.build_payload(GenerateVideoInput(prompt="x"))
    assert "image_list" not in payload
    assert "video_list" not in payload


def test_first_frame_and_reference_images_share_image_list():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="参考这些图生成视频",
        first_frame="https://ref1.png",
        reference_images=["https://ref2.png"],
        resolution="720p",
        aspect_ratio="9:16",
    )
    payload = adapter.build_payload(req)
    assert payload["size"] == "720x1280"
    # An untyped entry is a plain reference image
    assert payload["image_list"] == [
        {"image": "https://ref1.png", "type": "first_frame"},
        {"image": "https://ref2.png"},
    ]


def test_first_and_last_frame_map_to_first_and_end_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="首帧变尾帧",
        first_frame="https://start.png",
        last_frame="https://end.png",
        resolution="720p",
        aspect_ratio="1:1",
    )
    payload = adapter.build_payload(req)
    assert payload["size"] == "720x720"
    assert payload["image_list"] == [
        {"image": "https://start.png", "type": "first_frame"},
        {"image": "https://end.png", "type": "end_frame"},
    ]


def test_reference_videos_default_to_feature_refer_type():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="跟随参考视频运镜", reference_videos=["https://ref.mp4"])
    payload = adapter.build_payload(req)
    assert payload["video_list"] == [
        {"video_url": "https://ref.mp4", "refer_type": "feature"}
    ]
    assert payload["seconds"] == "5"  # a feature reference keeps the requested duration


def test_base_video_edit_drops_seconds():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="把背景换成沙滩",
        first_frame="https://style.png",
        quality_tier="best",
        resolution="1080p",
        aspect_ratio="16:9",
        model_specific={
            "video_list": [{"video_url": "https://src.mp4", "refer_type": "base"}]
        },
    )
    payload = adapter.build_payload(req)
    # Duration follows the source footage, so no `seconds` goes on the wire
    assert "seconds" not in payload
    assert payload["mode"] == "pro"
    assert payload["size"] == "1920x1080"
    assert payload["video_list"] == [
        {"video_url": "https://src.mp4", "refer_type": "base"}
    ]
    assert payload["image_list"] == [
        {"image": "https://style.png", "type": "first_frame"}
    ]


def test_explicit_seconds_survives_base_video_edit():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        model_specific={
            "video_list": [{"video_url": "https://src.mp4", "refer_type": "base"}],
            "seconds": "8",
        },
    )
    assert adapter.build_payload(req)["seconds"] == "8"


def test_cfgpu_model_id_only_in_model_field():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x")
    payload = adapter.build_payload(req)
    assert str(payload).count("kling-video-o1") == 1


def test_model_specific_merged_and_overrides():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", model_specific={"mode": "pro", "negative_prompt": "blurry"})
    payload = adapter.build_payload(req)
    assert payload["mode"] == "pro"
    assert payload["negative_prompt"] == "blurry"


# ── parse_response ───────────────────────────────────────────────────────────

def test_parse_response_extracts_video_url():
    adapter = _make_adapter()
    resp = {
        "id": "qvideo-1383109830-1782873292947656139",
        "object": "video",
        "model": "kling-video-o1",
        "mode": "pro",
        "status": "completed",
        "createdAt": 1782873292,
        "updatedAt": 1782873373,
        "completedAt": 1782873373,
        "seconds": "5",
        "size": "1920x1080",
        "taskResult": {
            "videos": [
                {
                    "id": "qvideo-1383109830-1782873292947656139-1",
                    "url": "https://cdn.example.com/video.mp4",
                    "duration": "5",
                }
            ]
        },
        "error": None,
    }
    result = adapter.parse_response(resp)
    assert result.urls == ["https://cdn.example.com/video.mp4"]
    assert result.task_id == "qvideo-1383109830-1782873292947656139"
    assert result.model_used == "kling-video-o1"
    assert result.expires_at is not None


def test_parse_response_multiple_videos():
    adapter = _make_adapter()
    resp = {
        "id": "t1",
        "status": "completed",
        "taskResult": {
            "videos": [
                {"id": "t1-1", "url": "https://cdn.example.com/1.mp4", "duration": "5"},
                {"id": "t1-2", "url": "https://cdn.example.com/2.mp4", "duration": "5"},
            ]
        },
    }
    result = adapter.parse_response(resp)
    assert result.urls == ["https://cdn.example.com/1.mp4", "https://cdn.example.com/2.mp4"]


def test_parse_response_missing_url_returns_empty():
    adapter = _make_adapter()
    resp = {"id": "t1", "status": "completed", "taskResult": {"videos": []}}
    result = adapter.parse_response(resp)
    assert result.urls == []


def test_parse_response_missing_task_result_returns_empty():
    adapter = _make_adapter()
    resp = {"id": "t1", "status": "queued"}
    result = adapter.parse_response(resp)
    assert result.urls == []


# ── extract_task_id / extract_status (inherited base behavior) ───────────────

def test_extract_task_id_from_id():
    adapter = _make_adapter()
    assert adapter.extract_task_id({"id": "cgt-xyz"}) == "cgt-xyz"


def test_extract_status_from_status_field():
    adapter = _make_adapter()
    assert adapter.extract_status({"status": "running"}) == "running"


# ── supports ─────────────────────────────────────────────────────────────────

def test_supports_accepts_text_only():
    adapter = _make_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x"))
    assert ok is True


def test_supports_accepts_first_frame():
    adapter = _make_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg"))
    assert ok is True


def test_supports_accepts_first_and_last_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x", first_frame="https://example.com/f.jpg", last_frame="https://example.com/l.jpg"
    )
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_accepts_reference_images_and_videos():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        reference_images=["https://example.com/r.jpg"],
        reference_videos=["https://example.com/r.mp4"],
    )
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_rejects_last_frame_without_first_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", last_frame="https://example.com/l.jpg")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "last_frame requires first_frame" in reason


def test_supports_rejects_reference_audios():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_audios=["https://example.com/a.mp3"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "reference_audios" in reason


def test_supports_rejects_smart_duration():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=-1)
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "explicit duration" in reason


def test_supports_rejects_image_request():
    from cfgpu_mcp.tool_registry import GenerateImageInput
    adapter = _make_adapter()
    ok, reason = adapter.supports(GenerateImageInput(prompt="x"))
    assert ok is False


# ── kling-v3-omni variant (extends chain reuses KlingVideoAdapter) ────────────

def test_v3_omni_resolves_to_kling_adapter():
    from cfgpu_mcp.config import load_registry
    reg = load_registry()
    adapter = reg.get("kling-v3-omni")
    assert isinstance(adapter, KlingVideoAdapter)
    assert adapter.cfgpu_model_id == "kling-v3-omni"
    assert adapter.extends == "kling-video-o1"


def test_v3_omni_payload_uses_own_model_id():
    from cfgpu_mcp.config import load_registry
    reg = load_registry()
    adapter = reg.get("kling-v3-omni")
    payload = adapter.build_payload(
        GenerateVideoInput(prompt="x", resolution="1080p", aspect_ratio="16:9", quality_tier="best")
    )
    assert payload["model"] == "kling-v3-omni"
    assert payload["size"] == "1920x1080"
    assert payload["mode"] == "pro"
