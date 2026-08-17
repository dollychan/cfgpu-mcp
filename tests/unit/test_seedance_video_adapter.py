import pytest
from cfgpu_mcp.adapters.seedance_video import SeedanceVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput


def _make_adapter(cfgpu_model_id: str = "wan-video") -> SeedanceVideoAdapter:
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
    return SeedanceVideoAdapter.from_config(config)


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


def test_watermark_omitted_when_none():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x")
    payload = adapter.build_payload(req)
    assert "watermark" not in payload


def test_watermark_typed_field_emitted():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", watermark=False)
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False


def test_model_specific_overrides_typed_watermark():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", watermark=True, model_specific={"watermark": False})
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False


def test_parse_response_missing_seed_is_none():
    adapter = _make_adapter()
    resp = {"id": "t1", "model": "wan-video", "content": {"videoUrl": "https://cdn/v.mp4"}, "status": "completed"}
    result = adapter.parse_response(resp)
    assert result.seed is None


def test_parse_response_expires_at_set():
    adapter = _make_adapter()
    resp = {"content": {"videoUrl": "https://cdn/v.mp4"}}
    result = adapter.parse_response(resp)
    assert result.expires_at is not None


def test_parse_response_real_api_result():
    """Validate parse_response against real CFGPU API response (from card.md)."""
    adapter = _make_adapter(cfgpu_model_id="wan-video-fast")
    resp = {
        "id": "cgt-20260513110708-8c5wf",
        "model": "wan-video-fast",
        "status": "succeeded",
        "error": None,
        "createdAt": 1778641628,
        "updatedAt": 1778641776,
        "content": {
            "videoUrl": "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/doubao-seedance-2-0-fast/02177864162835200000000000000000000ffffac18010234a352.mp4?X-Tos-Algorithm=TOS4-HMAC-SHA256",
            "lastFrameUrl": None,
        },
        "seed": 15233,
        "resolution": "720p",
        "ratio": "9:16",
        "duration": 5,
        "frames": None,
        "framesPerSecond": 24,
        "generateAudio": False,
        "draft": False,
        "draftTaskId": None,
        "usage": {"completionTokens": 108900, "totalTokens": 108900},
        "completionTokens": None,
        "totalTokens": None,
    }
    result = adapter.parse_response(resp)
    assert result.urls == ["https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/doubao-seedance-2-0-fast/02177864162835200000000000000000000ffffac18010234a352.mp4?X-Tos-Algorithm=TOS4-HMAC-SHA256"]
    assert result.task_id == "cgt-20260513110708-8c5wf"
    assert result.model_used == "wan-video-fast"
    assert result.seed == 15233
    assert result.usage == {"completionTokens": 108900, "totalTokens": 108900}
    assert result.aspect_ratio == "9:16"  # resolved output ratio read from response


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


def _make_no_reference_adapter() -> SeedanceVideoAdapter:
    """Adapter like Doubao Seedance 1.5 Pro: no multi_modal_reference."""
    config = {
        "adapter_id": "doubao-seedance-1-5-pro",
        "display_name": "Doubao Seedance 1.5 Pro",
        "cfgpu_model_id": "doubao-seedance-1-5-pro-251215",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {"text_to_video", "image_to_video", "first_last_frame"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return SeedanceVideoAdapter.from_config(config)


def test_supports_rejects_reference_videos_without_capability():
    adapter = _make_no_reference_adapter()
    req = GenerateVideoInput(prompt="x", reference_videos=["https://example.com/v.mp4"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "multi_modal_reference" in reason


def test_supports_rejects_reference_images_without_capability():
    adapter = _make_no_reference_adapter()
    req = GenerateVideoInput(prompt="x", reference_images=["https://example.com/r.jpg"])
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "multi_modal_reference" in reason


def test_supports_allows_first_frame_without_reference_capability():
    adapter = _make_no_reference_adapter()
    req = GenerateVideoInput(prompt="x", first_frame="https://example.com/f.jpg")
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_allows_reference_videos_with_capability():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", reference_videos=["https://example.com/v.mp4"])
    ok, _ = adapter.supports(req)
    assert ok is True


def test_resolution_1080p_passthrough():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", resolution="1080p")
    payload = adapter.build_payload(req)
    assert payload["resolution"] == "1080p"


def test_smart_duration_passthrough():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=-1)
    payload = adapter.build_payload(req)
    assert payload["duration"] == -1


def test_wan_accepts_15s_duration():
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=15)
    ok, _ = adapter.supports(req)
    assert ok is True


def test_wan_rejects_duration_over_15():
    # 30s is schema-legal (Seedance 2.5 allows it) but past WAN 2.0's own ceiling,
    # so the adapter — not the Pydantic validator — has to catch it.
    adapter = _make_adapter()
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", duration_seconds=30))
    assert ok is False
    assert "4–15" in reason


def test_seedance_rejects_duration_over_12():
    config = {
        "adapter_id": "doubao-seedance-1-5-pro",
        "display_name": "Doubao Seedance 1.5 Pro",
        "cfgpu_model_id": "doubao-seedance-1-5-pro-251215",
        "task_type": "video",
        "endpoint": "/v1/video/tasks",
        "is_async": True,
        "poll_endpoint": "/v1/video/tasks/{task_id}",
        "capabilities": {"text_to_video"},
        "cost_tier": 2,
        "speed_tier": 3,
        "max_duration_seconds": 12,
    }
    adapter = SeedanceVideoAdapter.from_config(config)
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", duration_seconds=15))
    assert ok is False
    assert "4–12" in reason


def _make_2_5_adapter() -> SeedanceVideoAdapter:
    config = {
        "adapter_id": "doubao-seedance-2-5",
        "display_name": "Doubao Seedance 2.5",
        "cfgpu_model_id": "doubao-seedance-2-5",
        "model_name": "doubao-seedance-2-5",
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {
            "text_to_video", "image_to_video", "first_last_frame",
            "multi_modal_reference", "video_edit", "video_extend", "audio_generate",
        },
        "cost_tier": 4,
        "speed_tier": 2,
        "max_duration_seconds": 30,
        "default_duration_seconds": -1,
        "resolutions": ["480p", "720p", "1080p"],
        "max_reference_images": 30,
        "max_reference_videos": 10,
        "max_reference_audios": 10,
        "allow_audio_only_reference": True,
    }
    return SeedanceVideoAdapter.from_config(config)


def test_seedance_2_5_accepts_1080p():
    adapter = _make_2_5_adapter()
    for req in (
        GenerateVideoInput(prompt="x", resolution="1080p"),
        GenerateVideoInput(prompt="x", resolution="1080p", first_frame="https://e.com/f.jpg"),
    ):
        ok, _ = adapter.supports(req)
        assert ok is True


def test_seedance_2_5_rejects_4k():
    ok, reason = _make_2_5_adapter().supports(
        GenerateVideoInput(prompt="x", resolution="4k")
    )
    assert ok is False
    assert "480p, 720p, 1080p" in reason


def test_seedance_2_5_accepts_720p():
    adapter = _make_2_5_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", resolution="720p"))
    assert ok is True


def _make_seedance_2_0_adapter(
    adapter_id: str = "doubao-seedance-2-0",
    resolutions: list[str] | None = None,
) -> SeedanceVideoAdapter:
    config = {
        "adapter_id": adapter_id,
        "display_name": adapter_id,
        "cfgpu_model_id": adapter_id,
        "model_name": adapter_id,
        "task_type": "video",
        "endpoint": "/video/generations",
        "is_async": True,
        "poll_endpoint": "/video/tasks/{task_id}",
        "capabilities": {
            "text_to_video", "image_to_video", "first_last_frame",
            "multi_modal_reference", "video_edit", "video_extend", "audio_generate",
        },
        "resolutions": resolutions or ["480p", "720p", "1080p", "4k"],
        "max_reference_images": 9,
        "max_reference_videos": 3,
        "max_reference_audios": 3,
        "allow_audio_only_reference": False,
    }
    return SeedanceVideoAdapter.from_config(config)


def test_seedance_2_0_accepts_4k():
    ok, _ = _make_seedance_2_0_adapter().supports(
        GenerateVideoInput(prompt="x", resolution="4k")
    )
    assert ok is True


@pytest.mark.parametrize("variant", ["doubao-seedance-2-0-fast", "doubao-seedance-2-0-mini"])
@pytest.mark.parametrize("resolution", ["1080p", "4k"])
def test_seedance_2_0_fast_and_mini_reject_above_720p(variant, resolution):
    adapter = _make_seedance_2_0_adapter(variant, ["480p", "720p"])
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", resolution=resolution))
    assert ok is False
    assert "480p, 720p" in reason


def test_seedance_2_0_fast_and_mini_default_to_720p():
    req = GenerateVideoInput(prompt="x")
    assert req.resolution == "720p"
    for variant in ("doubao-seedance-2-0-fast", "doubao-seedance-2-0-mini"):
        assert _make_seedance_2_0_adapter(variant, ["480p", "720p"]).supports(req)[0]


def test_seedance_2_0_rejects_audio_only_reference():
    req = GenerateVideoInput(reference_audios=["https://example.com/a.mp3"])
    ok, reason = _make_seedance_2_0_adapter().supports(req)
    assert ok is False
    assert "audio-only" in reason


def test_seedance_2_5_accepts_audio_only_reference_without_prompt():
    req = GenerateVideoInput(reference_audios=["https://example.com/a.mp3"])
    assert req.prompt == ""
    assert _make_2_5_adapter().supports(req)[0]


@pytest.mark.parametrize(
    ("field", "limit"),
    [("reference_images", 9), ("reference_videos", 3), ("reference_audios", 3)],
)
def test_seedance_2_0_reference_limits(field, limit):
    values = [f"https://example.com/{i}" for i in range(limit + 1)]
    kwargs = {field: values[:limit]}
    if field == "reference_audios":
        kwargs["reference_images"] = ["https://example.com/image.jpg"]
    assert _make_seedance_2_0_adapter().supports(
        GenerateVideoInput(prompt="x", **kwargs)
    )[0]
    kwargs[field] = values
    ok, reason = _make_seedance_2_0_adapter().supports(GenerateVideoInput(prompt="x", **kwargs))
    assert ok is False
    assert f"at most {limit}" in reason


@pytest.mark.parametrize(
    ("field", "limit"),
    [("reference_images", 30), ("reference_videos", 10), ("reference_audios", 10)],
)
def test_seedance_2_5_reference_limits(field, limit):
    values = [f"https://example.com/{i}" for i in range(limit + 1)]
    assert _make_2_5_adapter().supports(
        GenerateVideoInput(prompt="x", **{field: values[:limit]})
    )[0]
    ok, reason = _make_2_5_adapter().supports(
        GenerateVideoInput(prompt="x", **{field: values})
    )
    assert ok is False
    assert f"at most {limit}" in reason


def test_text_to_video_still_requires_prompt():
    ok, reason = _make_2_5_adapter().supports(GenerateVideoInput())
    assert ok is False
    assert "non-empty prompt" in reason


def test_undeclared_resolutions_stay_unrestricted():
    # A model that never declared its set (every model before `resolutions` existed)
    # keeps passing anything the schema allows through to the API.
    adapter = _make_adapter()
    assert adapter.resolutions is None
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", resolution="1080p"))
    assert ok is True


def test_seedance_2_5_accepts_30s_duration():
    adapter = _make_2_5_adapter()
    req = GenerateVideoInput(prompt="x", duration_seconds=30)
    ok, _ = adapter.supports(req)
    assert ok is True
    assert adapter.build_payload(req)["duration"] == 30


def test_seedance_2_5_omitted_duration_uses_smart_default():
    req = GenerateVideoInput(prompt="x")
    assert req.duration_seconds is None
    assert _make_2_5_adapter().build_payload(req)["duration"] == -1


def test_seedance_2_5_builds_the_same_content_array_as_2_0():
    # 2.5 differs from 2.0 in scale, not in payload shape — the reference roles and
    # top-level keys must stay byte-identical, only `model` changes.
    req = GenerateVideoInput(
        prompt="x",
        reference_images=["https://example.com/1.jpg"],
        reference_videos=["https://example.com/v.mp4"],
        reference_audios=["https://example.com/a.mp3"],
        duration_seconds=30,
    )
    payload = _make_2_5_adapter().build_payload(req)
    assert payload["model"] == "doubao-seedance-2-5"
    assert [(c["type"], c["role"]) for c in payload["content"] if c["type"] != "text"] == [
        ("image_url", "reference_image"),
        ("video_url", "reference_video"),
        ("audio_url", "reference_audio"),
    ]


def test_seedance_2_5_poll_timeout_covers_a_30s_render():
    adapter = _make_2_5_adapter()
    adapter.poll_config = None  # exercise the no-poll_config branch
    req = GenerateVideoInput(prompt="x", duration_seconds=30)
    # base 300 (text-to-video) + 25s beyond the 5s baseline × 20
    assert adapter.estimate_poll_timeout(req) == 800


def _make_fast_adapter() -> SeedanceVideoAdapter:
    config = {
        "adapter_id": "wan-2-0-fast",
        "display_name": "WAN 2.0 Fast",
        "cfgpu_model_id": "wan-video-fast",
        "task_type": "video",
        "endpoint": "/v1/video/tasks",
        "is_async": True,
        "poll_endpoint": "/v1/video/tasks/{task_id}",
        "capabilities": {"text_to_video", "image_to_video", "first_last_frame", "multi_modal_reference"},
        "cost_tier": 2,
        "speed_tier": 4,
    }
    return SeedanceVideoAdapter.from_config(config)


def test_fast_rejects_1080p_text_to_video():
    adapter = _make_fast_adapter()
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", resolution="1080p"))
    assert ok is False
    assert "1080p" in reason


def test_fast_allows_1080p_image_to_video():
    adapter = _make_fast_adapter()
    req = GenerateVideoInput(prompt="x", resolution="1080p", first_frame="https://example.com/f.jpg")
    ok, _ = adapter.supports(req)
    assert ok is True


def test_fast_allows_720p_text_to_video():
    adapter = _make_fast_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", resolution="720p"))
    assert ok is True


def test_full_wan_allows_1080p_text_to_video():
    adapter = _make_adapter()
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", resolution="1080p"))
    assert ok is True


def test_seedance_accepts_smart_duration():
    config = {
        "adapter_id": "doubao-seedance-1-5-pro",
        "display_name": "Doubao Seedance 1.5 Pro",
        "cfgpu_model_id": "doubao-seedance-1-5-pro-251215",
        "task_type": "video",
        "endpoint": "/v1/video/tasks",
        "is_async": True,
        "poll_endpoint": "/v1/video/tasks/{task_id}",
        "capabilities": {"text_to_video"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    adapter = SeedanceVideoAdapter.from_config(config)
    ok, _ = adapter.supports(GenerateVideoInput(prompt="x", duration_seconds=-1))
    assert ok is True
