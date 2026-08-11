import pytest
from cfgpu_mcp.adapters.grok_video import GrokVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _make_adapter() -> GrokVideoAdapter:
    config = {
        "adapter_id": "grok-imagine-video-1-5",
        "display_name": "Grok Imagine Video 1.5",
        "cfgpu_model_id": "grok-imagine-video-1.5",
        "model_name": "cf-imagine-video-1.5",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"text_to_video", "image_to_video", "multi_modal_reference"},
        "cost_tier": 3,
        "speed_tier": 3,
        "poll_config": {"base_interval": 5, "max_interval": 20, "backoff_factor": 1.3, "default_timeout": 600},
    }
    return GrokVideoAdapter.from_config(config)


# ── build_payload ────────────────────────────────────────────────────────────

def test_text_to_video_payload():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="镜头不动，石灯上的蚂蚁正在爬行",
        aspect_ratio="16:9",
        resolution="720p",
        duration_seconds=10,
    )
    payload = adapter.build_payload(req)
    assert payload == {
        "model": "grok-imagine-video-1.5",   # cfgpu_model_id, never model_name
        "prompt": "镜头不动，石灯上的蚂蚁正在爬行",
        "aspect_ratio": "16:9",
        "video_length": "10",
        "resolution_name": "720p",
    }


def test_video_length_is_string():
    adapter = _make_adapter()
    payload = adapter.build_payload(GenerateVideoInput(prompt="x", duration_seconds=5))
    assert payload["video_length"] == "5"
    assert isinstance(payload["video_length"], str)


def test_resolution_name_stays_lowercase():
    adapter = _make_adapter()
    payload = adapter.build_payload(GenerateVideoInput(prompt="x", resolution="480p"))
    assert payload["resolution_name"] == "480p"


def test_adaptive_aspect_ratio_falls_back_to_16_9():
    adapter = _make_adapter()
    payload = adapter.build_payload(GenerateVideoInput(prompt="x", aspect_ratio="adaptive"))
    assert payload["aspect_ratio"] == "16:9"


def test_no_refer_images_key_when_text_only():
    adapter = _make_adapter()
    assert "refer_images" not in adapter.build_payload(GenerateVideoInput(prompt="x"))


def test_first_frame_leads_refer_images():
    adapter = _make_adapter()
    req = GenerateVideoInput(
        prompt="x",
        first_frame="https://a.jpg",
        reference_images=["https://b.jpg", "https://c.jpg"],
    )
    payload = adapter.build_payload(req)
    assert payload["refer_images"] == ["https://a.jpg", "https://b.jpg", "https://c.jpg"]


def test_reference_images_alone():
    adapter = _make_adapter()
    payload = adapter.build_payload(
        GenerateVideoInput(prompt="x", reference_images=["https://b.jpg"])
    )
    assert payload["refer_images"] == ["https://b.jpg"]


def test_with_audio_and_watermark_are_not_sent():
    adapter = _make_adapter()
    payload = adapter.build_payload(
        GenerateVideoInput(prompt="x", with_audio=False, watermark=True)
    )
    assert "with_audio" not in payload and "sound" not in payload
    assert "watermark" not in payload


def test_model_specific_merges_last():
    adapter = _make_adapter()
    payload = adapter.build_payload(
        GenerateVideoInput(prompt="x", model_specific={"resolution_name": "1080p", "seed": 7})
    )
    assert payload["resolution_name"] == "1080p"
    assert payload["seed"] == 7


# ── envelope reading ─────────────────────────────────────────────────────────

def test_extract_task_id_from_data():
    adapter = _make_adapter()
    resp = {"code": 200, "message": "success", "data": {"taskId": "d7f12675"}}
    assert adapter.extract_task_id(resp) == "d7f12675"


@pytest.mark.parametrize("resp", [
    {"code": 200, "data": {"task_id": "abc", "status": "pending"}, "message": "success"},
    {"code": 200, "data": {"taskId": "abc"}},
    {"code": 200, "data": {"id": "abc"}},
    {"code": 200, "data": "abc", "message": "success"},
    {"id": "abc"},
    {"task_id": "abc"},
    {"taskId": "abc"},
])
def test_extract_task_id_tolerates_every_create_envelope(resp):
    """A dropped task id means a billed job that can never be polled."""
    assert _make_adapter().extract_task_id(resp) == "abc"


@pytest.mark.parametrize("resp", [
    {},
    {"data": None},
    {"data": {}},
    {"data": {"task_id": ""}},
    {"data": {"status": "pending"}},
])
def test_extract_task_id_returns_none_when_genuinely_absent(resp):
    assert _make_adapter().extract_task_id(resp) is None


def test_bare_id_create_response_is_treated_as_still_running():
    adapter = _make_adapter()
    resp = {"code": 200, "data": "abc"}
    assert adapter.extract_task_id(resp) == "abc"
    assert adapter.extract_status(resp) == "running"


def test_extract_status_defaults_to_running():
    adapter = _make_adapter()
    assert adapter.extract_status({"data": {}}) == "running"
    assert adapter.extract_status({}) == "running"


@pytest.mark.parametrize("raw,expected", [
    ("completed", "completed"),
    ("PENDING", "pending"),
    ("canceled", "failed"),
    ("unknown", "failed"),
])
def test_extract_status_normalization(raw, expected):
    adapter = _make_adapter()
    assert adapter.extract_status({"data": {"status": raw}}) == expected


# ── parse_response ───────────────────────────────────────────────────────────

_COMPLETED = {
    "code": 200,
    "message": "success",
    "data": {
        "taskId": "d7f12675-02f4-49f4-9ece-3b42fb74e87d",
        "status": "completed",
        "jobId": "6fe2dc0e-b655-9003-b3fe-d5ecf89ebda3",
        "videoId": None,
        "videoUrl": "https://smartml-oss-production.cfgpu.com/VIDEO_GENERATIONS/x.mp4?Expires=1786506003",
        "proxyUrl": None,
        "prompt": None,
        "aspectRatio": None,
        "videoLength": 1,
        "resolutionName": None,
        "quota": None,
        "finishTime": None,
        "pointsCost": 25,
        "pointsRefunded": False,
    },
}


def test_parse_response_reads_data_envelope():
    adapter = _make_adapter()
    result = adapter.parse_response(_COMPLETED)
    assert result.urls == [_COMPLETED["data"]["videoUrl"]]
    assert result.task_id == "d7f12675-02f4-49f4-9ece-3b42fb74e87d"
    assert result.expires_at is not None


def test_parse_response_falls_back_to_proxy_url():
    adapter = _make_adapter()
    resp = {"data": {"videoUrl": None, "proxyUrl": "https://proxy/x.mp4"}}
    assert adapter.parse_response(resp).urls == ["https://proxy/x.mp4"]


def test_parse_response_no_url_yields_empty_urls():
    adapter = _make_adapter()
    assert adapter.parse_response({"data": {"status": "processing"}}).urls == []


# ── usage (per-second billing, assembled by the adapter) ─────────────────────

def test_usage_built_from_data_fields():
    adapter = _make_adapter()
    resp = {"data": {"videoUrl": "https://x.mp4", "videoLength": 10,
                     "resolutionName": "720p", "aspectRatio": "16:9"}}
    assert adapter.parse_response(resp).usage == {"duration": 10, "sr": 720, "ratio": "16:9"}


def test_usage_duration_string_becomes_number():
    adapter = _make_adapter()
    resp = {"data": {"videoLength": "10", "resolutionName": "1080p"}}
    usage = adapter.parse_response(resp).usage
    assert usage["duration"] == 10 and isinstance(usage["duration"], int)
    assert usage["sr"] == 1080


def test_usage_tolerates_missing_resolution_and_ratio():
    adapter = _make_adapter()
    usage = adapter.parse_response(_COMPLETED).usage
    assert usage == {"duration": 1, "sr": None, "ratio": None}


def test_usage_is_none_when_nothing_extractable():
    adapter = _make_adapter()
    assert adapter.parse_response({"data": {"status": "pending"}}).usage is None


def test_aspect_ratio_echoed_when_present_none_otherwise():
    adapter = _make_adapter()
    assert adapter.parse_response({"data": {"aspectRatio": "9:16"}}).aspect_ratio == "9:16"
    # null → task_manager falls back to the requested ratio
    assert adapter.parse_response(_COMPLETED).aspect_ratio is None


# ── supports ─────────────────────────────────────────────────────────────────

def test_supports_text_and_image_to_video():
    adapter = _make_adapter()
    assert adapter.supports(GenerateVideoInput(prompt="x"))[0]
    assert adapter.supports(GenerateVideoInput(prompt="x", first_frame="https://a.jpg"))[0]
    assert adapter.supports(GenerateVideoInput(prompt="x", reference_images=["https://a.jpg"]))[0]


@pytest.mark.parametrize("kwargs,fragment", [
    ({"first_frame": "https://a.jpg", "last_frame": "https://b.jpg"}, "last_frame"),
    ({"reference_videos": ["https://v.mp4"]}, "reference_videos"),
    ({"reference_audios": ["https://a.mp3"]}, "reference_audios"),
    ({"duration_seconds": -1}, "explicit duration"),
])
def test_supports_rejects_unsupported_slots(kwargs, fragment):
    adapter = _make_adapter()
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", **kwargs))
    assert not ok and fragment in reason


def test_supports_rejects_image_request():
    from cfgpu_mcp.tool_registry import GenerateImageInput

    adapter = _make_adapter()
    ok, reason = adapter.supports(GenerateImageInput(prompt="x"))
    assert not ok and "video model" in reason


# ── registry wiring ──────────────────────────────────────────────────────────

def test_registry_resolves_public_and_internal_ids():
    from pathlib import Path

    import cfgpu_mcp.adapters  # noqa: F401 — triggers registration
    from cfgpu_mcp.adapters.registry import AdapterRegistry

    models_dir = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"
    registry = AdapterRegistry(model_dir=models_dir)
    registry.load()
    adapter = registry.get("cf-imagine-video-1.5")
    assert isinstance(adapter, GrokVideoAdapter)
    assert adapter.cfgpu_model_id == "grok-imagine-video-1.5"
    # adapter_id / cfgpu_model_id keep resolving for existing callers
    assert registry.get("grok-imagine-video-1-5") is adapter
    assert registry.get("grok-imagine-video-1.5") is adapter


def test_base_model_reuses_the_same_class_via_extends():
    """`grok-imagine-video` inherits everything but its ids and price tiers."""
    from pathlib import Path

    import cfgpu_mcp.adapters  # noqa: F401 — triggers registration
    from cfgpu_mcp.adapters.registry import AdapterRegistry

    models_dir = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"
    registry = AdapterRegistry(model_dir=models_dir)
    registry.load()

    base = registry.get("cf-imagine-video")
    v15 = registry.get("cf-imagine-video-1.5")
    assert isinstance(base, GrokVideoAdapter)
    assert base is not v15
    assert base.adapter_id == "grok-imagine-video"
    assert base.cfgpu_model_id == "grok-imagine-video"
    # Inherited from the parent YAML
    assert base.capabilities == v15.capabilities
    assert base.task_type == "video" and base.is_async
    assert base.endpoint == v15.endpoint and base.poll_endpoint == v15.poll_endpoint
    assert base.poll_config.default_timeout == v15.poll_config.default_timeout
    # Overridden: cheaper and faster than 1.5
    assert base.cost_tier < v15.cost_tier
    assert base.speed_tier > v15.speed_tier


def test_base_model_payload_carries_its_own_cfgpu_model_id():
    from pathlib import Path

    import cfgpu_mcp.adapters  # noqa: F401 — triggers registration
    from cfgpu_mcp.adapters.registry import AdapterRegistry

    models_dir = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"
    registry = AdapterRegistry(model_dir=models_dir)
    registry.load()

    payload = registry.get("cf-imagine-video").build_payload(
        GenerateVideoInput(prompt="蚂蚁爬行", duration_seconds=10, resolution="720p", aspect_ratio="16:9")
    )
    assert payload == {
        "model": "grok-imagine-video",
        "prompt": "蚂蚁爬行",
        "aspect_ratio": "16:9",
        "video_length": "10",
        "resolution_name": "720p",
    }
