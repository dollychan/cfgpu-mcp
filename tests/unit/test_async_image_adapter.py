import pytest
from cfgpu_mcp.adapters.async_image import ImageGPTAdapter, NanoBananaAdapter
from cfgpu_mcp.tool_registry import GenerateImageInput


def _gpt() -> ImageGPTAdapter:
    return ImageGPTAdapter.from_config({
        "adapter_id": "gpt-image-2",
        "display_name": "GPT Image 2",
        "cfgpu_model_id": "gpt-image-2",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "tasks": ["text_to_image", "image_to_image"],
        "cost_tier": 2,
        "speed_tier": 3,
        "max_images_per_request": 10,
    })


def _nano() -> NanoBananaAdapter:
    return NanoBananaAdapter.from_config({
        "adapter_id": "nano-banana-2",
        "display_name": "Nano Banana 2",
        "cfgpu_model_id": "nano2",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "tasks": ["text_to_image", "image_to_image"],
        "cost_tier": 4,
        "speed_tier": 3,
    })


def _gpt_25() -> ImageGPTAdapter:
    return ImageGPTAdapter.from_config({
        "adapter_id": "gpt-image-2-5-sunburst",
        "display_name": "CF Image 2.5 Sunburst",
        "cfgpu_model_id": "gpt-image-2.5-sunburst",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "tasks": ["text_to_image", "image_to_image"],
        "cost_tier": 3,
        "speed_tier": 3,
        "max_images_per_request": 1,
        "sends_n": False,
        "default_background": "auto",
        "quality_tiers": ["xhigh", "max", "auto"],
    })


def test_gpt_image_2_forwards_image_count_ceiling():
    req = GenerateImageInput(prompt="x", n=3)
    adapter = _gpt()
    ok, reason = adapter.supports(req)
    assert ok is True
    assert reason == ""
    payload = adapter.build_payload(req)
    assert payload["prompt"] == "x"
    assert payload["n"] == 3


@pytest.mark.parametrize("n", [1, 10])
def test_gpt_image_2_accepts_documented_image_count_range(n):
    ok, reason = _gpt().supports(GenerateImageInput(prompt="x", n=n))
    assert ok is True
    assert reason == ""


def test_gpt_image_2_rejects_image_count_above_documented_range():
    ok, reason = _gpt().supports(GenerateImageInput(prompt="x", n=11))
    assert ok is False
    assert "1 to 10" in reason


def test_nano_banana_ignores_group_generation_parameter():
    payload = _nano().build_payload(GenerateImageInput(prompt="x", n=3))
    assert payload["prompt"] == "x"
    assert "n" not in payload
    assert "sequential_image_generation" not in payload


@pytest.mark.parametrize("adapter", [_gpt(), _nano()])
def test_accepts_single_image(adapter):
    req = GenerateImageInput(prompt="x")
    ok, _ = adapter.supports(req)
    assert ok is True


@pytest.mark.parametrize("requested", ["1K", "2K", "4K", "3K"])
def test_gpt_image_2_sends_resolution(requested):
    """The tier must reach the payload verbatim — the API bills per resolution band,
    so a dropped field silently downgrades every call. It names all three tiers
    literally and rejects anything else (an earlier revision translated 1K to "",
    which upstream answered with "resolution 参数必须为 '1K'、'2K' 或 '4K'"); 3K has no
    counterpart and goes up as-is for upstream to reject.
    """
    payload = _gpt().build_payload(GenerateImageInput(prompt="x", resolution=requested))
    assert payload["resolution"] == requested


@pytest.mark.parametrize(
    ("tier", "quality"), [("fast", "low"), ("balanced", "medium"), ("best", "high")]
)
def test_gpt_image_2_maps_quality_tier(tier, quality):
    """quality_tier steers the API's own quality instead of a second, near-synonymous
    tool parameter — same approach as Kling's quality_tier → std/pro mode."""
    payload = _gpt().build_payload(GenerateImageInput(prompt="x", quality_tier=tier))
    assert payload["quality"] == quality


def test_gpt_image_2_quality_defaults_to_medium():
    payload = _gpt().build_payload(GenerateImageInput(prompt="x"))
    assert payload["quality"] == "medium"


def test_gpt_image_2_model_specific_overrides_quality():
    """model_specific merges last, so it stays the escape hatch for a value the
    tier mapping can't express."""
    payload = _gpt().build_payload(
        GenerateImageInput(prompt="x", quality_tier="fast", model_specific={"quality": "high"})
    )
    assert payload["quality"] == "high"


def test_nano_banana_has_no_quality():
    """The field is GPT Image 2's; nano-banana's API has no counterpart."""
    payload = _nano().build_payload(GenerateImageInput(prompt="x", quality_tier="best"))
    assert "quality" not in payload


@pytest.mark.parametrize("aspect_ratio", ["21:9", "9:21", "3:1", "1:3", "2:1", "1:2"])
def test_gpt_image_2_passes_new_aspect_ratios_through(aspect_ratio):
    payload = _gpt().build_payload(GenerateImageInput(prompt="x", aspect_ratio=aspect_ratio))
    assert payload["aspect_ratio"] == aspect_ratio


def test_gpt_image_25_sizes_by_ratio_and_resolution_without_n():
    payload = _gpt_25().build_payload(
        GenerateImageInput(prompt="x", aspect_ratio="2:1", resolution="4K", quality_tier="best")
    )
    # No image_size: the upstream maps aspect_ratio + resolution itself, the
    # same way GPT Image 2 is sized.  No n: the API has no output-count field.
    assert payload == {
        "model": "gpt-image-2.5-sunburst",
        "prompt": "x",
        "aspect_ratio": "2:1",
        "resolution": "4K",
        "quality": "high",
        "background": "auto",
    }


def test_gpt_image_25_model_specific_overrides_background_and_quality():
    payload = _gpt_25().build_payload(
        GenerateImageInput(prompt="x", model_specific={"background": "transparent", "quality": "xhigh"})
    )
    assert payload["background"] == "transparent"
    assert payload["quality"] == "xhigh"


def test_gpt_image_25_refuses_more_than_one_image():
    ok, reason = _gpt_25().supports(GenerateImageInput(prompt="x", n=2))
    assert ok is False
    assert "1 to 1" in reason


def test_gpt_image_2_payload_has_no_background():
    assert "background" not in _gpt().build_payload(GenerateImageInput(prompt="x"))


@pytest.mark.parametrize("tier", ["xhigh", "max", "auto"])
def test_gpt_image_25_sends_extra_quality_tiers_verbatim(tier):
    adapter = _gpt_25()
    req = GenerateImageInput(prompt="x", quality_tier=tier)
    assert adapter.supports(req) == (True, "")
    assert adapter.build_payload(req)["quality"] == tier


def test_quality_tiers_key_rejects_unknown_tier():
    with pytest.raises(ValueError, match="quality_tiers has unknown tiers"):
        ImageGPTAdapter.from_config({
            "adapter_id": "x", "cfgpu_model_id": "x", "task_type": "image",
            "endpoint": "/i", "quality_tiers": ["ultra"],
        })


def _completed_task(model: str) -> dict:
    # Shape of GET /images/tasks/{id} for a finished task (verified 2026-10-15 on
    # gpt-image-2 and gpt-image-2.5-flare): no top-level `usage`, the token count
    # sits under data.result.tokenUsage.
    return {
        "code": 200,
        "data": {
            "result": {
                "images": ["https://cdn/a.png"],
                "tokenUsage": {
                    "imageInputTokens": 0,
                    "textInputTokens": 987,
                    "totalTokens": 3689,
                    "imageOutputTokens": 2702,
                },
                "tokenBillingSettlement": {"actualPoints": 40, "channel": "official"},
            },
            "task_id": "t-1",
            "task_type": "gpt_image_generation",
            "status": "completed",
        },
        "model": model,
        "message": "success",
    }


@pytest.mark.parametrize("adapter", [_gpt(), _gpt_25()])
def test_usage_is_read_from_result_token_usage(adapter):
    result = adapter.parse_response(_completed_task(adapter.cfgpu_model_id))
    assert result.urls == ["https://cdn/a.png"]
    assert result.usage == {
        "imageInputTokens": 0,
        "textInputTokens": 987,
        "totalTokens": 3689,
        "imageOutputTokens": 2702,
    }


def test_usage_is_none_when_the_task_reports_none():
    resp = _completed_task("gpt-image-2")
    del resp["data"]["result"]["tokenUsage"]
    assert _gpt().parse_response(resp).usage is None
