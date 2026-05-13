import pytest
from cfgpu_mcp.adapters.wan_video import WanVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _make_adapter(cfgpu_model_id: str = "wan-video") -> WanVideoAdapter:
    config = {
        "adapter_id": "wan-2-0",
        "display_name": "WAN 2.0",
        "cfgpu_model_id": cfgpu_model_id,
        "task_type": "video",
        "endpoint": "/v1/video/tasks",
        "is_async": True,
        "poll_endpoint": "/v1/video/tasks/{task_id}",
        "capabilities": {"text_to_video", "image_to_video", "first_last_frame", "multi_modal_reference"},
        "cost_tier": 3,
        "speed_tier": 2,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 600},
    }
    return WanVideoAdapter.from_config(config)


def test_text_only_has_no_media_items():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="a cat running")
    payload = adapter.build_payload(req)
    types = [c["type"] for c in payload["content"]]
    assert types == ["text"]


def test_first_frame_added_with_correct_role():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg")
    payload = adapter.build_payload(req)
    image_items = [c for c in payload["content"] if c["type"] == "image_url"]
    assert len(image_items) == 1
    assert image_items[0]["role"] == "first_frame"


def test_first_and_last_frame_both_present():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://example.com/first.jpg",
        last_frame="https://example.com/last.jpg",
    )
    payload = adapter.build_payload(req)
    image_items = [c for c in payload["content"] if c["type"] == "image_url"]
    assert len(image_items) == 2
    assert image_items[0]["role"] == "first_frame"
    assert image_items[1]["role"] == "last_frame"


def test_reference_images_use_reference_image_role():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        reference_images=["https://example.com/r1.jpg", "https://example.com/r2.jpg"],
    )
    payload = adapter.build_payload(req)
    image_items = [c for c in payload["content"] if c["type"] == "image_url"]
    assert all(i["role"] == "reference_image" for i in image_items)
    assert len(image_items) == 2


def test_reference_videos_added():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_videos=["https://example.com/v.mp4"])
    payload = adapter.build_payload(req)
    video_items = [c for c in payload["content"] if c["type"] == "video_url"]
    assert len(video_items) == 1
    assert video_items[0]["role"] == "reference_video"


def test_reference_audios_added():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_audios=["https://example.com/a.mp3"])
    payload = adapter.build_payload(req)
    audio_items = [c for c in payload["content"] if c["type"] == "audio_url"]
    assert len(audio_items) == 1
    assert audio_items[0]["role"] == "reference_audio"


def test_cfgpu_model_id_only_in_model_field():
    adapter = _make_adapter(cfgpu_model_id="wan-video-fast")
    req = GenerateVideoInput(prompt="x")
    payload = adapter.build_payload(req)
    assert payload["model"] == "wan-video-fast"
    payload_str = str(payload)
    # cfgpu_model_id value appears exactly once (in model field)
    assert payload_str.count("wan-video-fast") == 1


def test_model_specific_merged():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", model_specific={"watermark": False})
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False


def test_parse_response_missing_seed_is_none():
    adapter = _make_adapter()
    resp = {"id": "t1", "model": "wan-video", "output": {"video_url": "https://cdn/v.mp4"}, "status": "completed"}
    result = adapter.parse_response(resp)
    assert result.seed is None


def test_parse_response_expires_at_set():
    adapter = _make_adapter()
    resp = {"output": {"video_url": "https://cdn/v.mp4"}}
    result = adapter.parse_response(resp)
    assert result.expires_at is not None


def test_supports_rejects_last_frame_without_first_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", last_frame="https://example.com/last.jpg")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "first_frame" in reason


def test_supports_rejects_mixed_first_frame_and_reference_images():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://example.com/f.jpg",
        reference_images=["https://example.com/r.jpg"],
    )
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "mutually exclusive" in reason
