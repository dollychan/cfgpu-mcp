import pytest
from cfgpu_mcp.adapters.async_image import GptImage2Adapter, NanoBananaAdapter
from cfgpu_mcp.tool_registry import GenerateImageInput


def _gpt() -> GptImage2Adapter:
    return GptImage2Adapter.from_config({
        "adapter_id": "gpt-image-2",
        "display_name": "GPT Image 2",
        "cfgpu_model_id": "gpt-image-2",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "capabilities": {"text_to_image", "image_to_image"},
        "cost_tier": 2,
        "speed_tier": 3,
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
        "capabilities": {"text_to_image", "image_to_image"},
        "cost_tier": 4,
        "speed_tier": 3,
    })


@pytest.mark.parametrize("adapter", [_gpt(), _nano()])
def test_ignores_group_generation_parameter(adapter):
    req = GenerateImageInput(prompt="x", n=3)
    ok, reason = adapter.supports(req)
    assert ok is True
    assert reason == ""
    payload = adapter.build_payload(req)
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


def test_gpt_image_2_passes_aspect_ratio_through():
    """21:9 is in the unified schema but not in GPT Image 2's set — no local guard,
    the request goes up and upstream rejects it."""
    payload = _gpt().build_payload(GenerateImageInput(prompt="x", aspect_ratio="21:9"))
    assert payload["aspect_ratio"] == "21:9"
