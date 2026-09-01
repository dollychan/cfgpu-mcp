import pytest
from cfgpu_mcp.adapters.seedream import SeedreamAdapter
from cfgpu_mcp.tool_registry import GenerateImageInput


def _make_adapter() -> SeedreamAdapter:
    config = {
        "adapter_id": "doubao-seedream-5-0-lite",
        "display_name": "Doubao Seedream 5.0 lite",
        "cfgpu_model_id": "doubao-seedream-5-0-260128",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": False,
        "poll_endpoint": None,
        # Mirrors the real adapter.yaml: multi_image_group is what gates 组图, so a
        # fixture missing it describes a different model than the one shipped.
        "capabilities": {"text_to_image", "image_to_image", "multi_image_fusion", "multi_image_group"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return SeedreamAdapter.from_config(config)


def _make_pro_adapter() -> SeedreamAdapter:
    config = {
        "adapter_id": "doubao-seedream-5-0-pro",
        "display_name": "Doubao Seedream 5.0 Pro",
        "cfgpu_model_id": "doubao-seedream-5-0-pro",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": False,
        "poll_endpoint": None,
        "capabilities": {"text_to_image", "image_to_image", "multi_image_fusion"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return SeedreamAdapter.from_config(config)


def test_2k_1x1_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="1:1")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2048x2048"


def test_2k_16x9_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="16:9")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2848x1600"


def test_single_reference_image_is_string():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", reference_images=["https://example.com/img.jpg"])
    payload = adapter.build_payload(req)
    assert isinstance(payload["image"], str)


def test_multiple_reference_images_is_list():
    adapter = _make_adapter()
    req = GenerateImageInput(
        prompt="x",
        reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
    )
    payload = adapter.build_payload(req)
    assert isinstance(payload["image"], list)
    assert len(payload["image"]) == 2


def test_no_reference_images_omits_image_field():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert "image" not in payload


def test_model_specific_merged():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", model_specific={"watermark": False, "output_format": "png"})
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False
    assert payload["output_format"] == "png"


def test_watermark_defaults_false_at_payload_top_level():
    payload = _make_adapter().build_payload(GenerateImageInput(prompt="x"))
    assert payload["watermark"] is False


def test_explicit_true_watermark_is_preserved():
    payload = _make_adapter().build_payload(
        GenerateImageInput(prompt="x", watermark=True)
    )
    assert payload["watermark"] is True


def test_cfgpu_model_id_only_in_model_field():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert payload["model"] == "doubao-seedream-5-0-260128"
    # Verify it only appears once in the whole payload
    assert str(payload).count("doubao-seedream-5-0-260128") == 1


def test_parse_response_task_id_is_none():
    adapter = _make_adapter()
    resp = {"model": "doubao-seedream-5-0-260128", "data": [{"url": "https://cdn/img.jpg"}]}
    result = adapter.parse_response(resp)
    assert result.task_id is None


def test_parse_response_extracts_urls():
    adapter = _make_adapter()
    resp = {"data": [{"url": "https://cdn/a.jpg"}, {"url": "https://cdn/b.jpg"}]}
    result = adapter.parse_response(resp)
    assert len(result.urls) == 2


def test_parse_response_expires_at_set():
    adapter = _make_adapter()
    resp = {"data": [{"url": "https://cdn/img.jpg"}]}
    result = adapter.parse_response(resp)
    assert result.expires_at is not None


def test_n_greater_than_one_enables_group_generation():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", n=4)
    payload = adapter.build_payload(req)
    assert payload["sequential_image_generation"] == "auto"
    assert payload["sequential_image_generation_options"] == {"max_images": 4}


def test_n_equals_one_omits_group_fields():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert "sequential_image_generation" not in payload
    assert "sequential_image_generation_options" not in payload


# --- 2K extended aspect ratios (added with Seedream 5.0 Pro spec) ---

def test_2k_3x2_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="3:2")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2496x1664"


def test_2k_2x3_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="2:3")
    payload = adapter.build_payload(req)
    assert payload["size"] == "1664x2496"


def test_2k_21x9_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="21:9")
    payload = adapter.build_payload(req)
    assert payload["size"] == "3136x1344"


# --- doubao-seedream-5-0-pro (single-image, 1K/2K) ---

def test_pro_n_greater_than_one_is_ignored():
    adapter = _make_pro_adapter()
    req = GenerateImageInput(prompt="x", n=4)
    ok, reason = adapter.supports(req)
    assert ok
    assert reason == ""
    payload = adapter.build_payload(req)
    assert "sequential_image_generation" not in payload
    assert "sequential_image_generation_options" not in payload


def test_pro_1k_maps_to_pixels():
    """Every Pro tier emits pixels, 1K included — the tier name is never sent, so the
    caller's aspect_ratio is honoured at every tier rather than left to the prompt."""
    adapter = _make_pro_adapter()
    req = GenerateImageInput(prompt="x", resolution="1K", aspect_ratio="16:9")
    payload = adapter.build_payload(req)
    assert payload["size"] == "1424x800"


def test_pro_2k_3x2_maps_to_correct_size():
    adapter = _make_pro_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="3:2")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2496x1664"


def test_pro_no_group_fields():
    adapter = _make_pro_adapter()
    req = GenerateImageInput(prompt="x")  # n defaults to 1
    payload = adapter.build_payload(req)
    assert "sequential_image_generation" not in payload
    assert "sequential_image_generation_options" not in payload


def test_pro_cfgpu_model_id_in_model_field():
    adapter = _make_pro_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert payload["model"] == "doubao-seedream-5-0-pro"
    assert str(payload).count("doubao-seedream-5-0-pro") == 1


# --- per-family size tables -------------------------------------------------
#
# Pro and the Lite/4.x line publish *different* pixel values for the same tier and
# ratio. One shared table silently hands one family the other's geometry — no error, no
# warning, just a picture at the wrong dimensions.


def _make_4_0_adapter() -> SeedreamAdapter:
    config = {
        "adapter_id": "doubao-seedream-4-0",
        "display_name": "Doubao Seedream 4.0",
        "cfgpu_model_id": "doubao-seedream-4-0-250828",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": False,
        "poll_endpoint": None,
        "capabilities": {"text_to_image", "multi_image_group"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return SeedreamAdapter.from_config(config)


@pytest.mark.parametrize(
    "aspect_ratio, pro_size, lite_size",
    [
        ("1:1", "2048x2048", "2048x2048"),
        ("4:3", "2368x1776", "2304x1728"),
        ("3:4", "1776x2368", "1728x2304"),
        ("16:9", "2816x1584", "2848x1600"),
        ("9:16", "1584x2816", "1600x2848"),
    ],
)
def test_pro_and_lite_2k_geometry_differ(aspect_ratio, pro_size, lite_size):
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio=aspect_ratio)
    assert _make_pro_adapter().build_payload(req)["size"] == pro_size
    assert _make_adapter().build_payload(req)["size"] == lite_size


def test_pro_2k_stays_inside_its_own_pixel_ceiling():
    """Pro's total-pixel ceiling is 4,624,220 — under a quarter of Lite's — so every
    value emitted for it has to be checked against that, not against Lite's range."""
    adapter = _make_pro_adapter()
    for aspect_ratio in ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"):
        size = adapter.build_payload(
            GenerateImageInput(prompt="x", resolution="2K", aspect_ratio=aspect_ratio)
        )["size"]
        w, h = (int(v) for v in size.split("x"))
        assert 921600 <= w * h <= 4624220, f"{aspect_ratio} -> {size}"


@pytest.mark.parametrize(
    "resolution, aspect_ratio, expected",
    [
        ("3K", "4:3", "3456x2592"),
        ("3K", "3:2", "3744x2496"),
        ("3K", "21:9", "4704x2016"),
        ("4K", "4:3", "4704x3520"),
        ("4K", "2:3", "3328x4992"),
        ("4K", "21:9", "6240x2656"),
    ],
)
def test_3k_and_4k_carry_every_published_ratio(resolution, aspect_ratio, expected):
    """These rows were missing, so a 4K 21:9 request came back as 4K 1:1 — reported as
    an aspect_ratio 'correction' for a ratio the model documents and supports."""
    req = GenerateImageInput(prompt="x", resolution=resolution, aspect_ratio=aspect_ratio)
    assert _make_adapter().build_payload(req)["size"] == expected


def test_pro_1_5k_maps_to_pixels():
    """1.5K has its own published pixel table on Pro and is sent as pixels like the
    other two tiers."""
    req = GenerateImageInput(prompt="x", resolution="1.5K", aspect_ratio="16:9")
    assert _make_pro_adapter().build_payload(req)["size"] == "2048x1152"


@pytest.mark.parametrize("tier", ["1K", "1.5K", "2K"])
@pytest.mark.parametrize("ratio", ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"])
def test_pro_every_tier_and_ratio_is_pixels_inside_the_documented_range(tier, ratio):
    """Pro's constraint is a total-pixel range, [921600, 4624220], plus a ratio range of
    [1/16, 16]. A table entry outside it is an opaque upstream 400 that no local check
    would catch, so pin the whole grid rather than a sample of it."""
    req = GenerateImageInput(prompt="x", resolution=tier, aspect_ratio=ratio)
    size = _make_pro_adapter().build_payload(req)["size"]
    w, h = (int(v) for v in size.split("x"))
    assert 921600 <= w * h <= 4624220, size
    assert 1 / 16 <= w / h <= 16, size


def test_4_0_supports_1k():
    """4.0's pixel floor is 921600 — exactly its own 1K 16:9 — so 1K is in range and
    must not be 'corrected' up into a bigger, pricier image nobody asked for."""
    adapter = _make_4_0_adapter()
    req = GenerateImageInput(prompt="x", resolution="1K", aspect_ratio="16:9")
    assert adapter.supports(req)[0]
    assert adapter.build_payload(req)["size"] == "1280x720"


def test_4_0_and_pro_1k_are_not_the_same_table():
    """Both offer 1K and each publishes its own values for it."""
    size = _make_4_0_adapter().build_payload(
        GenerateImageInput(prompt="x", resolution="1K", aspect_ratio="21:9")
    )["size"]
    assert size == "1512x648"


@pytest.mark.parametrize(
    "factory, resolution, supported",
    [
        (_make_pro_adapter, "4K", False),     # over Pro's pixel ceiling
        (_make_pro_adapter, "3K", False),
        (_make_pro_adapter, "1.5K", True),
        (_make_adapter, "1K", False),         # under Lite's pixel floor
        (_make_adapter, "1.5K", False),
        (_make_adapter, "4K", True),
    ],
)
def test_the_billed_path_rejects_a_tier_the_model_does_not_have(factory, resolution, supported):
    """Previously only ``validate_only`` knew. ``supports()`` never looked at image
    resolution, so a caller that ignored ``corrected_args`` sent 5504x3040 to a model
    capped at 4,624,220 pixels and got an opaque upstream 400 — while the preflight had
    reported the request as fine-with-a-correction. Preflight and billed call have to
    agree, or the preflight is not worth running."""
    req = GenerateImageInput(prompt="x", resolution=resolution, aspect_ratio="1:1")
    ok, reason = factory().supports(req)
    assert ok is supported
    if not supported:
        assert "resolution" in reason


# --- n / 组图 (sequential image generation) ----------------------------------


def test_group_generation_is_gated_on_the_capability_not_on_the_model_id():
    """A single-image variant must not be recognised by being 'not Pro'.

    Pro happens to be the only family member without 组图 today, so an ``adapter_id ==
    pro`` test passes right now — and would keep passing while silently switching 组图 on
    for the next single-image variant. Variants without the capability must ignore n.
    """
    adapter = _make_adapter()
    adapter.capabilities = adapter.capabilities - {"multi_image_group"}
    ok, reason = adapter.supports(GenerateImageInput(prompt="x", n=4))
    assert ok
    assert reason == ""
    payload = adapter.build_payload(GenerateImageInput(prompt="x", n=4))
    assert "sequential_image_generation" not in payload
    assert "sequential_image_generation_options" not in payload


def test_single_image_seedream_ignores_n():
    ok, reason = _make_pro_adapter().supports(GenerateImageInput(prompt="x", n=4))
    assert ok
    assert reason == ""


@pytest.mark.parametrize(
    "refs, n, ok",
    [
        (13, 2, True),    # 15 total — the documented ceiling, inclusive
        (14, 2, False),   # 16 total
        (14, 1, True),    # not a group at all; only the 14-reference limit applies
    ],
)
def test_reference_images_plus_generated_images_cap(refs, n, ok):
    req = GenerateImageInput(prompt="x", reference_images=["https://e/a.jpg"] * refs, n=n)
    assert _make_adapter().supports(req)[0] is ok


def test_n_equals_one_omits_the_group_key_entirely():
    """`disabled` is already the upstream default, and Pro rejects the key even when it
    is set to `disabled`, so sending nothing is both correct and the only portable form."""
    payload = _make_adapter().build_payload(GenerateImageInput(prompt="x"))
    assert "sequential_image_generation" not in payload


def test_a_partially_failed_group_reports_which_slots_failed_and_why():
    """Four requested, two blocked, HTTP 200. Returning two urls and nothing else hands
    the caller a short list with no reason — and the two causes need opposite responses:
    a moderation rejection means rewrite the prompt, an upstream 500 means generation
    stopped there and the remaining slots were never attempted."""
    resp = {
        "model": "doubao-seedream-5-0-260128",
        "data": [
            {"url": "https://cdn/a.jpg"},
            {"error": {"code": "content_blocked", "message": "审核不通过"}},
            {"url": "https://cdn/b.jpg"},
            {"error": {"code": "InternalServiceError", "message": "internal error"}},
        ],
        "usage": {"generated_images": 2},
    }
    result = _make_adapter().parse_response(resp)
    assert result.urls == ["https://cdn/a.jpg", "https://cdn/b.jpg"]
    assert [e["index"] for e in result.partial_errors] == [1, 3]
    assert [e["code"] for e in result.partial_errors] == ["content_blocked", "InternalServiceError"]


def test_a_fully_successful_group_carries_no_partial_errors():
    """Absent, not an empty list: every consumer already branches on truthiness, and an
    empty list would put an always-present key into the model's context for nothing."""
    resp = {"data": [{"url": "https://cdn/a.jpg"}, {"url": "https://cdn/b.jpg"}]}
    assert _make_adapter().parse_response(resp).partial_errors is None
