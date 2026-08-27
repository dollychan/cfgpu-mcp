"""万相 2.7 图像 (``wan2.7-image``) — the first structured-region image model.

Three things here are not covered by any other image adapter's tests, and each is a
place where a wrong value produces a picture and a bill rather than an error:

1. **The two 图像集 keys travel together.** ``enable_sequential: true`` with no ``n``
   defaults to *12* upstream. Sending one without the other is a 12-image invoice for a
   request that asked for one group.
2. **``bbox_list`` is absolute pixels, per image, padded.** A short outer list does not
   fail — it lands the box on the wrong picture. A guessed ``image_size`` does not fail
   either — it edits the wrong rectangle.
3. **Coordinates stay out of the prompt.** This model reads them from its own field, so
   a prompt that also carries them hands it the same box twice in two different rasters.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.regions import build_bbox_list
from cfgpu_mcp.adapters.wan_image import WanImageAdapter
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import GenerateImageInput, RegionSpec

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"

#: The published all-scenario ceiling for wan2.7-image, and its floor.
MAX_TOTAL = 2048 * 2048
MIN_TOTAL = 768 * 768


def _registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    registry = AdapterRegistry(model_dir=MODELS_DIR)
    registry.load()
    return registry


def _adapter() -> WanImageAdapter:
    return _registry().get("wan2.7-image")


def _req(**kw) -> GenerateImageInput:
    kw.setdefault("prompt", "一间有着精致窗户的花店")
    return GenerateImageInput(**kw)


def _params(adapter, req) -> dict:
    return adapter.build_payload(req)["parameters"]


def _content(adapter, req) -> list[dict]:
    return adapter.build_payload(req)["input"]["messages"][0]["content"]


# ── registration ────────────────────────────────────────────────────────────


def test_the_model_is_registered_as_a_synchronous_image_model():
    adapter = _adapter()
    assert isinstance(adapter, WanImageAdapter)
    assert adapter.task_type == "image"
    assert adapter.is_async is False
    assert adapter.cfgpu_model_id == "wan2.7-image"
    assert adapter.endpoint == "/images/generations"


def test_it_declares_the_five_capabilities_it_implements():
    assert _adapter().capabilities == {
        "text_to_image",
        "image_to_image",
        "multi_image_fusion",
        "multi_image_group",
        "region_edit",
    }


def test_only_the_public_model_name_reaches_the_payload_model_field():
    # cfgpu_model_id happens to equal model_name here; the assertion is that
    # build_payload sources it from cfgpu_model_id, which is the documented contract.
    assert _adapter().build_payload(_req())["model"] == "wan2.7-image"


# ── size ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("resolution", ["1K", "2K"])
@pytest.mark.parametrize("ratio", ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"])
def test_every_size_cell_is_the_exact_aspect_ratio_within_the_pixel_budget(resolution, ratio):
    """Exactness is the entire reason pixels are sent instead of the tier name.

    A tier name leaves framing to whatever the model infers from the prompt; a pixel pair
    that is only approximately the requested ratio buys the roundtrip and delivers
    neither.
    """
    size = _params(_adapter(), _req(resolution=resolution, aspect_ratio=ratio))["size"]
    width, height = (int(v) for v in size.split("*"))
    aw, ah = (int(v) for v in ratio.split(":"))
    assert width * ah == height * aw
    assert MIN_TOTAL <= width * height <= MAX_TOTAL


def test_the_separator_is_a_star_not_seedream_s_x():
    # DashScope spells it `width*height` and echoes the same spelling in usage.size;
    # Seedream spells the same idea `width x height`. One adapter copied from the other
    # would send a string the API cannot parse.
    assert "*" in _params(_adapter(), _req())["size"]
    assert "x" not in _params(_adapter(), _req())["size"]


def test_each_tier_spends_most_of_its_documented_pixel_budget():
    for resolution, budget in (("1K", 1024 * 1024), ("2K", 2048 * 2048)):
        for ratio in ("1:1", "16:9", "21:9"):
            size = _params(_adapter(), _req(resolution=resolution, aspect_ratio=ratio))["size"]
            width, height = (int(v) for v in size.split("*"))
            assert width * height >= budget * 0.95


def test_a_tier_the_model_does_not_have_is_rejected_rather_than_downgraded():
    """The billed path must reject what the preflight would correct.

    Otherwise validate_only reports ``corrected_args: {resolution: 2K}`` while a caller
    that ignores it sends a 4K request and gets an opaque upstream 400 — and
    ``model="auto"`` cannot route a 4K request away from this model either.
    """
    for tier in ("1.5K", "3K", "4K"):
        ok, reason = _adapter().supports(_req(resolution=tier))
        assert not ok
        assert "1K, 2K" in reason


@pytest.mark.parametrize("asked,expected", [("1.5K", "1K"), ("3K", "2K"), ("4K", "2K")])
def test_the_correction_never_upgrades_the_caller_into_a_pricier_image(asked, expected):
    assert _adapter().validation_corrections(_req(resolution=asked)) == {"resolution": expected}


def test_a_supported_tier_produces_no_correction():
    assert _adapter().validation_corrections(_req(resolution="2K")) == {}


def test_model_specific_parameters_merge_into_the_object_rather_than_replacing_it():
    """The escape hatch for handing framing back to the model.

    A plain top-level update would replace the whole ``parameters`` object, silently
    dropping the computed size and the watermark flag along with it.
    """
    params = _params(_adapter(), _req(model_specific={"parameters": {"size": "2K", "seed": 42}}))
    assert params["size"] == "2K"
    assert params["seed"] == 42
    assert params["watermark"] is False


# ── content array ───────────────────────────────────────────────────────────


def test_the_prompt_is_the_single_text_part_and_images_follow_in_slot_order():
    """Image order in ``content`` *is* the ordinal the prompt and bbox_list refer to."""
    content = _content(
        _adapter(), _req(reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"])
    )
    assert [part for part in content if "text" in part] == [{"text": "一间有着精致窗户的花店"}]
    assert [part["image"] for part in content if "image" in part] == [
        "https://example.com/a.jpg",
        "https://example.com/b.jpg",
    ]


def test_more_than_nine_reference_images_is_refused_locally():
    ok, reason = _adapter().supports(
        _req(reference_images=[f"https://example.com/{i}.jpg" for i in range(10)])
    )
    assert not ok
    assert "at most 9" in reason


def test_an_empty_prompt_is_refused():
    ok, reason = _adapter().supports(_req(prompt="   "))
    assert not ok
    assert "non-empty prompt" in reason


# ── 图像集 (n / enable_sequential) ───────────────────────────────────────────


def test_a_single_image_request_sends_neither_group_key():
    """``enable_sequential`` is default-false upstream, so n=1 says nothing at all."""
    params = _params(_adapter(), _req(n=1))
    assert "enable_sequential" not in params
    assert "n" not in params


def test_the_two_group_keys_are_always_sent_together():
    """``enable_sequential: true`` with no ``n`` defaults to 12 images — and 12 charges."""
    params = _params(_adapter(), _req(n=4))
    assert params["enable_sequential"] is True
    assert params["n"] == 4


def test_n_above_the_group_ceiling_is_refused():
    # The unified schema allows n up to 15 (Seedream's ceiling); this model stops at 12.
    ok, reason = _adapter().supports(_req(n=13))
    assert not ok
    assert "12" in reason


# ── thinking_mode ───────────────────────────────────────────────────────────


def test_thinking_mode_is_on_by_default_for_plain_text_to_image():
    assert _params(_adapter(), _req())["thinking_mode"] is True


def test_the_fast_quality_tier_turns_thinking_off():
    assert _params(_adapter(), _req(quality_tier="fast"))["thinking_mode"] is False


@pytest.mark.parametrize(
    "kw",
    [
        {"reference_images": ["https://example.com/a.jpg"]},
        {"n": 4},
    ],
)
def test_thinking_mode_is_omitted_where_upstream_ignores_it(kw):
    """Documented as effective only with no image input and no image set.

    Sending it elsewhere would put a flag in the payload that reads as a request and is
    not one, which is the sort of thing a later reader trusts.
    """
    assert "thinking_mode" not in _params(_adapter(), _req(**kw))


# ── regions → bbox_list ─────────────────────────────────────────────────────


def _edit_req(**kw) -> GenerateImageInput:
    kw.setdefault("prompt", "把[[m1]]的闹钟放到[[标记1]]，光影自然融合")
    kw.setdefault("reference_images", ["https://example.com/clock.jpg", "https://example.com/room.jpg"])
    kw.setdefault("image_refs", ["m1", "m2"])
    kw.setdefault(
        "regions",
        [RegionSpec(image_index=1, box=[0.5, 0.5, 1.0, 1.0], image_size=[1696, 960], label="标记1")],
    )
    return GenerateImageInput(**kw)


def test_bbox_list_is_padded_and_aligned_with_the_image_slot():
    """A short outer list does not raise — it edits the wrong picture."""
    params = _params(_adapter(), _edit_req())
    assert params["bbox_list"] == [[], [[848, 480, 1695, 959]]]


def test_bbox_list_is_in_absolute_pixels_of_the_original_image():
    box = _params(_adapter(), _edit_req())["bbox_list"][1][0]
    # 0.5 of 1696 = 848; the far edge names the last covered pixel, not the image width.
    assert box == [848, 480, 1696 - 1, 960 - 1]


def test_the_prompt_carries_a_referent_and_no_coordinates():
    """The box travels in its own field; a copy in the prompt is the same box twice.

    The placeholder still resolves in place, because position carries meaning — "put the
    clock at the marked area" is not the same sentence with the phrase moved.
    """
    content = _content(_adapter(), _edit_req())
    prompt = content[0]["text"]
    assert prompt == "把图1的闹钟放到图2中框选的区域，光影自然融合"
    assert "bbox" not in prompt
    assert "848" not in prompt


def test_a_second_box_on_one_image_is_numbered_in_the_prompt():
    req = _edit_req(
        prompt="把[[标记1]]换成猫，把[[标记2]]换成狗",
        regions=[
            RegionSpec(image_index=1, box=[0.0, 0.0, 0.5, 0.5], image_size=[1000, 1000], label="标记1"),
            RegionSpec(image_index=1, box=[0.5, 0.5, 1.0, 1.0], image_size=[1000, 1000], label="标记2"),
        ],
    )
    payload = _adapter().build_payload(req)
    assert payload["input"]["messages"][0]["content"][0]["text"] == (
        "把图2中框选的第1个区域换成猫，把图2中框选的第2个区域换成狗"
    )
    # Same order in both places: the n-th box is the one the prompt calls 第 n 个.
    assert payload["parameters"]["bbox_list"][1] == [[0, 0, 499, 499], [500, 500, 999, 999]]


def test_an_unreferenced_region_reaches_the_model_without_a_prompt_suffix():
    """It is in bbox_list, so it is not dropped — and a bare mention is not a safety net.

    The prose dialects append a suffix because the prompt is the only channel their
    coordinates have. Here an appended "图2中框选的第2个区域。" would read as one more
    thing to go and edit.
    """
    req = _edit_req(
        prompt="把[[标记1]]换成猫",
        regions=[
            RegionSpec(image_index=1, box=[0.0, 0.0, 0.5, 0.5], image_size=[1000, 1000], label="标记1"),
            RegionSpec(image_index=1, box=[0.5, 0.5, 1.0, 1.0], image_size=[1000, 1000], label="标记2"),
        ],
    )
    payload = _adapter().build_payload(req)
    assert payload["input"]["messages"][0]["content"][0]["text"] == "把图2中框选的第1个区域换成猫"
    assert len(payload["parameters"]["bbox_list"][1]) == 2


def test_a_region_without_an_image_size_is_refused_not_guessed():
    """A guessed size does not fail — it edits a plausible, billed rectangle elsewhere."""
    req = _edit_req(
        regions=[RegionSpec(image_index=1, box=[0.5, 0.5, 1.0, 1.0], label="标记1")]
    )
    ok, reason = _adapter().supports(req)
    assert not ok
    assert "image_size" in reason


def test_build_bbox_list_refuses_a_missing_size_for_a_direct_caller_too():
    with pytest.raises(ValueError, match="image_size"):
        build_bbox_list([RegionSpec(image_index=0, box=[0.1, 0.1, 0.2, 0.2])], 1)


def test_the_documented_two_box_per_image_ceiling_is_enforced():
    req = _edit_req(
        prompt="改这三处",
        regions=[
            RegionSpec(image_index=1, box=[0.0, 0.0, 0.3, 0.3], image_size=[1000, 1000]),
            RegionSpec(image_index=1, box=[0.3, 0.3, 0.6, 0.6], image_size=[1000, 1000]),
            RegionSpec(image_index=1, box=[0.6, 0.6, 0.9, 0.9], image_size=[1000, 1000]),
        ],
    )
    ok, reason = _adapter().supports(req)
    assert not ok
    assert "2" in reason


def test_regions_and_an_image_set_cannot_be_combined():
    ok, reason = _adapter().supports(_edit_req(n=4))
    assert not ok
    assert "图像集" in reason


def test_a_size_less_regions_request_routes_to_a_prompt_coordinate_model_instead():
    """``supports()`` refusing is what lets auto find the dialect that needs no size."""
    req = GenerateImageInput(
        prompt="改这里",
        reference_images=["https://example.com/a.jpg"],
        regions=[RegionSpec(image_index=0, box=[0.1, 0.1, 0.2, 0.2])],
    )
    adapter = ModelRouter(_registry()).resolve(req)
    assert "region_edit" in adapter.capabilities
    assert adapter.adapter_id != "wan-2-7-image"


# ── response ────────────────────────────────────────────────────────────────


def _dashscope_response(*images: str) -> dict:
    return {
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": [{"image": url, "type": "image"} for url in images],
                    },
                }
            ],
            "finished": True,
        },
        "usage": {"image_count": len(images), "size": "2985*1405"},
        "request_id": "a3f4befe",
    }


def test_urls_are_read_out_of_the_dashscope_choices_envelope():
    result = _adapter().parse_response(_dashscope_response("https://example.com/1.png"))
    assert result.urls == ["https://example.com/1.png"]
    assert result.task_id is None          # synchronous: there is no task to poll
    assert result.usage == {"image_count": 1, "size": "2985*1405"}


def test_an_image_set_returns_every_url_in_the_group():
    result = _adapter().parse_response(
        _dashscope_response("https://example.com/1.png", "https://example.com/2.png")
    )
    assert len(result.urls) == 2


def test_the_flat_data_shape_is_tolerated_as_a_fallback():
    """This model shares POST /images/generations with Seedream, whose sync shape is flat."""
    result = _adapter().parse_response({"data": [{"url": "https://example.com/1.png"}]})
    assert result.urls == ["https://example.com/1.png"]


def test_urls_are_reported_as_expiring_within_a_day():
    # Documented as 24 hours; a caller that keeps the URL instead of the bytes loses it.
    result = _adapter().parse_response(_dashscope_response("https://example.com/1.png"))
    assert result.expires_at is not None


def test_a_response_with_no_image_yields_no_urls():
    # The empty list is what trips task_manager's "succeeded but no artifact" invariant,
    # so a shape change fails loudly rather than returning a successful empty result.
    assert _adapter().parse_response({"output": {"choices": []}}).urls == []
