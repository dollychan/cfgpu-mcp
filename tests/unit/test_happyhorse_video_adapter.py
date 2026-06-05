import pytest
from cfgpu_mcp.adapters.happyhorse_video import HappyHorseVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _make_adapter() -> HappyHorseVideoAdapter:
    config = {
        "adapter_id": "happyhorse-1-0-t2v",
        "display_name": "happyhorse-1.0-t2v",
        "cfgpu_model_id": "happyhorse-1.0-t2v",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"text_to_video", "image_to_video", "multi_modal_reference"},
        "cost_tier": 2,
        "speed_tier": 3,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 300},
    }
    return HappyHorseVideoAdapter.from_config(config)


# ── build_payload ────────────────────────────────────────────────────────────

def test_text_only_payload():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="a cat running")
    payload = adapter.build_payload(req)
    assert payload["model"] == "happyhorse-1.0-t2v"
    assert payload["input"]["prompt"] == "a cat running"
    assert "media" not in payload["input"]


def test_resolution_uppercased():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", resolution="720p")
    payload = adapter.build_payload(req)
    assert payload["parameters"]["resolution"] == "720P"


def test_first_frame_in_media():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg")
    payload = adapter.build_payload(req)
    assert payload["input"]["media"] == [{"type": "first_frame", "url": "https://example.com/f.jpg"}]


def test_reference_images_in_media():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        reference_images=["https://example.com/r1.jpg", "https://example.com/r2.jpg"],
    )
    payload = adapter.build_payload(req)
    media = payload["input"]["media"]
    assert len(media) == 2
    assert all(m["type"] == "reference_image" for m in media)


def test_adaptive_aspect_ratio_omitted():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", aspect_ratio="adaptive")
    payload = adapter.build_payload(req)
    assert "ratio" not in payload.get("parameters", {})


def test_explicit_aspect_ratio_included():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", aspect_ratio="16:9")
    payload = adapter.build_payload(req)
    assert payload["parameters"]["ratio"] == "16:9"


def test_cfgpu_model_id_only_in_model_field():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x")
    payload = adapter.build_payload(req)
    assert payload["model"] == "happyhorse-1.0-t2v"
    payload_str = str(payload)
    assert payload_str.count("happyhorse-1.0-t2v") == 1


def test_model_specific_merged():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", model_specific={"watermark": False})
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False


# ── extract_task_id ──────────────────────────────────────────────────────────

def test_extract_task_id_from_output():
    adapter = _make_adapter()
    resp = {"output": {"task_id": "task-abc123", "task_status": "PENDING"}}
    assert adapter.extract_task_id(resp) == "task-abc123"


def test_extract_task_id_missing_returns_none():
    adapter = _make_adapter()
    assert adapter.extract_task_id({}) is None


# ── extract_status ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("api_status,expected", [
    ("PENDING", "pending"),
    ("RUNNING", "running"),
    ("SUCCEEDED", "succeeded"),
    ("FAILED", "failed"),
    ("CANCELED", "failed"),
    ("UNKNOWN", "failed"),
])
def test_extract_status_normalizes(api_status, expected):
    adapter = _make_adapter()
    resp = {"output": {"task_status": api_status}}
    assert adapter.extract_status(resp) == expected


# ── parse_response ───────────────────────────────────────────────────────────

def test_parse_response_extracts_video_url():
    adapter = _make_adapter()
    resp = {
        "output": {
            "task_id": "task-abc123",
            "task_status": "SUCCEEDED",
            "video_url": "https://cdn.example.com/video.mp4",
            "seed": 42,
        },
        "usage": {"total_tokens": 230},
        "model": "happyhorse-1.0-t2v",
    }
    result = adapter.parse_response(resp)
    assert result.urls == ["https://cdn.example.com/video.mp4"]
    assert result.task_id == "task-abc123"
    assert result.model_used == "happyhorse-1.0-t2v"
    assert result.seed == 42
    assert result.cost_tokens == 230
    assert result.expires_at is not None


def test_parse_response_missing_url_returns_empty():
    adapter = _make_adapter()
    resp = {"output": {"task_id": "t1", "task_status": "SUCCEEDED"}}
    result = adapter.parse_response(resp)
    assert result.urls == []


# ── supports ─────────────────────────────────────────────────────────────────

def test_supports_rejects_last_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", last_frame="https://example.com/last.jpg")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "last_frame" in reason


def test_supports_rejects_reference_videos():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_videos=["https://example.com/v.mp4"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "reference_videos" in reason


def test_supports_rejects_reference_audios():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_audios=["https://example.com/a.mp3"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "reference_audios" in reason


def test_supports_rejects_480p():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", resolution="480p")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "720p" in reason


def test_supports_rejects_first_frame_with_reference_images():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://example.com/f.jpg",
        reference_images=["https://example.com/r.jpg"],
    )
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "mutually exclusive" in reason


def test_supports_accepts_text_only():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x")
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_accepts_first_frame():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg")
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_accepts_reference_images():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_images=["https://example.com/r.jpg"])
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_rejects_smart_duration():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=-1)
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "explicit duration" in reason
