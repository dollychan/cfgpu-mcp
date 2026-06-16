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
        "capabilities": {"text_to_video"},
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
        "id": "cgt-abc123",
        "model": "kling-video-o1",
        "status": "succeeded",
        "content": {"videoUrl": "https://cdn.example.com/video.mp4", "lastFrameUrl": None},
        "seed": 42,
        "usage": {"totalTokens": None},
    }
    result = adapter.parse_response(resp)
    assert result.urls == ["https://cdn.example.com/video.mp4"]
    assert result.task_id == "cgt-abc123"
    assert result.model_used == "kling-video-o1"
    assert result.seed == 42
    assert result.expires_at is not None


def test_parse_response_missing_url_returns_empty():
    adapter = _make_adapter()
    resp = {"id": "t1", "status": "succeeded", "content": {}}
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


def test_supports_rejects_first_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "text-to-video only" in reason


def test_supports_rejects_reference_images():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_images=["https://example.com/r.jpg"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "text-to-video only" in reason


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
