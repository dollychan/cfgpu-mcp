"""Regions: user-marked boxes travelling from a canvas to a model, untouched by an LLM.

Three properties are load-bearing here and each has its own section below.

1. **Coordinates are never invented and never dropped.** A box that goes missing does
   not raise — it produces a whole-image edit that looks plausible and gets billed. So
   every path that could drop one is pinned: a model without the capability refuses, an
   unreferenced region falls into the structured suffix, and an ambiguous placeholder
   fails closed rather than guessing an image.

2. **The [0, 999] grid conversion is inclusive at the far edge.** Off by one cell is
   invisible in any single call and accumulates across edit round-trips.

3. **Reading models are told the names, writing models are not.** A generation model
   given "标记1" in its prompt may paint those characters into the picture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.regions import GRID, format_bbox, render_prompt, to_grid, to_pixels
from cfgpu_mcp.adapters.seedream import SeedreamAdapter
from cfgpu_mcp.adapters.vision_chat import QwenVisionAdapter
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.task_manager import validate_request
from cfgpu_mcp.tool_registry import GenerateImageInput, RegionSpec, UnderstandVisionInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    registry = AdapterRegistry(model_dir=MODELS_DIR)
    registry.load()
    return registry


def _pro() -> SeedreamAdapter:
    return _registry().get("doubao-seedream-5-0-pro")


def _lite() -> SeedreamAdapter:
    return _registry().get("doubao-seedream-5-0-lite")


def _qwen() -> QwenVisionAdapter:
    return _registry().get("qwen3.6-plus")


def _region(index=0, box=None, **kw) -> RegionSpec:
    return RegionSpec(image_index=index, box=box or [0.1, 0.2, 0.3, 0.4], **kw)


# ── the [0, 999] grid ───────────────────────────────────────────────────────


def test_full_image_box_covers_the_whole_grid():
    """``[0,0,1,1]`` must reach the last cell on both axes.

    Rounding the far edge the same way as the near edge yields 999→998 here: the box
    stops one cell short of the image, silently, and the gap widens with every
    round-trip through an edit.
    """
    assert to_grid([0.0, 0.0, 1.0, 1.0]) == (0, 0, GRID - 1, GRID - 1)


def test_far_edge_names_the_last_covered_cell():
    # 0.5 of the axis = cells 0..499; the far edge is the index of the last one.
    assert to_grid([0.0, 0.0, 0.5, 0.5]) == (0, 0, 499, 499)


def test_near_edge_floors_into_its_own_cell():
    assert to_grid([0.5, 0.5, 1.0, 1.0]) == (500, 500, 999, 999)


def test_a_sliver_still_has_a_non_inverted_box():
    """A box thinner than one cell must not come out with x2 < x1."""
    x1, y1, x2, y2 = to_grid([0.5001, 0.5001, 0.5002, 0.5002])
    assert x1 <= x2 and y1 <= y2


def test_the_grid_is_just_the_pixel_conversion_over_a_1000x1000_image():
    """One conversion serves both rasters, so the inclusive-last-cell rule has one home.

    万相 2.7 wants absolute pixels of the real image; the prompt dialects want cells of a
    1000-wide grid. Two implementations of a rule this easy to get subtly wrong would be
    fixed one at a time.
    """
    box = [0.1, 0.2, 0.35, 0.9]
    assert to_grid(box) == to_pixels(box, (GRID, GRID))


def test_pixels_land_on_the_last_covered_pixel_of_the_real_image():
    # 1696x960, the size in the model's own interactive-editing example.
    assert to_pixels([0.0, 0.0, 1.0, 1.0], [1696, 960]) == (0, 0, 1695, 959)
    assert to_pixels([0.5, 0.5, 1.0, 1.0], [1696, 960]) == (848, 480, 1695, 959)


def test_bbox_is_the_documented_spelling():
    assert format_bbox([0.0, 0.0, 1.0, 1.0]) == "<bbox>0 0 999 999</bbox>"


# ── placeholder resolution ──────────────────────────────────────────────────


def test_label_placeholder_is_replaced_in_place():
    out = render_prompt(
        "把[[标记1]]换成一只猫",
        [_region(label="标记1", box=[0.0, 0.0, 1.0, 1.0])],
        ["m_a1"],
        include_names=False,
    )
    assert out == "把图1 <bbox>0 0 999 999</bbox>换成一只猫"


def test_every_box_names_the_image_it_is_on():
    """A composite edit puts boxes from two pictures in one sentence.

    Observed in production: "站立着[[标记2]]" with 标记2 on the second image rendered as a
    bare <bbox>, so the soldier's box on 图2 read as a patch of 图1's landscape. The model
    cannot recover the image from the numbers, and guessing wrong still returns a picture
    and still bills for it.
    """
    regions = [
        _region(0, label="山顶", box=[0.0, 0.0, 0.5, 0.5]),
        _region(1, label="士兵", box=[0.5, 0.5, 1.0, 1.0]),
    ]
    out = render_prompt(
        "在[[山顶]]的位置，站立着[[士兵]]", regions, ["m_a1", "m_7c"], include_names=False
    )
    assert out == "在图1 <bbox>0 0 499 499</bbox>的位置，站立着图2 <bbox>500 500 999 999</bbox>"


def test_the_ordinal_is_emitted_even_with_one_image():
    """Redundant on a single image, but the alternative is a branch whose dangerous side
    (several images) is the one exercised least."""
    out = render_prompt("改[[标记1]]", [_region(label="标记1")], ["m_a1"], include_names=False)
    assert "图1 <bbox>" in out


def test_inline_and_suffix_render_a_box_identically():
    """Same box, two placements — the only difference may be where it sits in the text.

    The ordinal used to live in the suffix builder alone, so the two paths disagreed
    about whether a box says which image it is on — and the load-bearing one (in-place
    substitution, the only coordinate channel Seedream has) was the one missing it.
    """
    box = [0.0, 0.0, 0.5, 0.5]
    inline = render_prompt("改[[标记1]]", [_region(0, label="标记1", box=box)], ["m_a1"], include_names=False)
    suffix = render_prompt("调暖一点", [_region(0, label="标记1", box=box)], ["m_a1"], include_names=False)
    piece = "图1 <bbox>0 0 499 499</bbox>"
    assert piece in inline
    assert suffix.endswith(f"参考区域：{piece}。")
    assert "图1 图1" not in suffix          # the builder must not prepend a second one


def test_image_handle_placeholder_becomes_that_dialects_ordinal():
    out = render_prompt(
        "把[[m_7c]]的风格套到[[m_a1]]上", [], ["m_a1", "m_7c"], include_names=False
    )
    assert out == "把图2的风格套到图1上"


def test_a_repeated_label_across_images_fails_closed():
    """Every image restarts its own numbering, so '标记1' on two images is ambiguous.

    Picking the first would edit *the other picture* — an outcome that produces an
    artifact, costs money, and reads as reasonable. That is the worst available failure,
    so this must be an error and the message must name the qualified forms.
    """
    regions = [_region(0, label="标记1"), _region(1, label="标记1")]
    with pytest.raises(CFGPUError) as exc:
        render_prompt("改[[标记1]]", regions, ["m_a1", "m_7c"], include_names=False)
    assert "[[m_a1#标记1]]" in exc.value.user_message
    assert "[[m_7c#标记1]]" in exc.value.user_message
    # The card cannot say which one was meant; the message already lists both.
    assert exc.value.card_hint is False


def test_the_qualified_form_disambiguates_it():
    regions = [
        _region(0, label="标记1", box=[0.0, 0.0, 0.5, 0.5]),
        _region(1, label="标记1", box=[0.5, 0.5, 1.0, 1.0]),
    ]
    out = render_prompt("改[[m_7c#标记1]]", regions, ["m_a1", "m_7c"], include_names=False)
    assert "<bbox>500 500 999 999</bbox>" in out


def test_a_token_that_is_both_a_handle_and_a_label_fails_closed():
    regions = [_region(0, label="m_a1")]
    with pytest.raises(CFGPUError, match="同时是一个图片句柄和一个标记名"):
        render_prompt("改[[m_a1]]", regions, ["m_a1"], include_names=False)


def test_an_unmatched_placeholder_is_left_alone():
    """Zero hits is not an error: the caller may simply not be using placeholders, and
    the region still reaches the model through the suffix."""
    out = render_prompt("改[[标记9]]", [_region(label="标记1")], ["m_a1"], include_names=False)
    assert "[[标记9]]" in out


def test_an_unreferenced_region_is_appended_never_dropped():
    out = render_prompt("把画面调暖一点", [_region(label="标记1")], ["m_a1"], include_names=False)
    assert "参考区域" in out and "<bbox>" in out


def test_only_the_unreferenced_regions_are_appended():
    regions = [
        _region(0, label="标记1", box=[0.0, 0.0, 0.5, 0.5]),
        _region(0, label="标记2", box=[0.5, 0.5, 1.0, 1.0]),
    ]
    out = render_prompt("改[[标记1]]", regions, ["m_a1"], include_names=False)
    assert out.count("<bbox>") == 2          # one inline, one in the suffix
    assert out.index("<bbox>0 0 499 499") < out.index("参考区域")


def test_regions_render_without_image_refs():
    """image_refs is optional — only ``[[handle]]`` needs it."""
    out = render_prompt("改[[标记1]]", [_region(label="标记1")], None, include_names=False)
    assert "<bbox>" in out


# ── names reach reading models only ─────────────────────────────────────────


def test_the_editing_prompt_carries_coordinates_but_no_names():
    """A generation model paints what it reads; '标记1' in the prompt is a string it may
    render into the picture, which is exactly what a positional mark must never become."""
    req = GenerateImageInput(
        prompt="把[[标记1]]换成一只猫",
        model="doubao-seedream-5-0-pro",
        reference_images=["https://example.com/a.jpg"],
        regions=[_region(label="标记1", note="用户说这里是杯子")],
        image_refs=["m_a1"],
    )
    payload = _pro().build_payload(req)
    assert "<bbox>" in payload["prompt"]
    assert "标记1" not in payload["prompt"]
    assert "杯子" not in payload["prompt"]


def test_the_vision_prompt_carries_names_notes_and_an_output_contract():
    req = UnderstandVisionInput(
        prompt="[[标记3]]是什么材质？",
        images=["https://example.com/a.jpg"],
        regions=[_region(label="标记3", note="沙发左下角")],
        image_refs=["m_a1"],
    )
    text = _qwen().build_payload(req)["messages"][1]["content"][0]["text"]
    assert "图1 标记3<bbox>" in text
    assert "沙发左下角" in text
    assert "不要在回答中输出 bbox 坐标" in text


def test_a_reading_model_is_told_which_image_each_mark_is_on():
    """Same fix, the other dialect: names alone do not identify a box across images.

    Every image restarts at 标记1, so two images in one question legitimately carry the
    same name. The prompt half is now unambiguous; the answer half is not (see the
    output contract, which still quotes a bare label).
    """
    req = UnderstandVisionInput(
        prompt="[[m_a1#标记1]]和[[m_7c#标记1]]是同一种材质吗？",
        images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
        image_refs=["m_a1", "m_7c"],
        regions=[
            _region(0, label="标记1", box=[0.0, 0.0, 0.5, 0.5]),
            _region(1, label="标记1", box=[0.5, 0.5, 1.0, 1.0]),
        ],
    )
    text = _qwen().build_payload(req)["messages"][1]["content"][0]["text"]
    assert "图1 标记1<bbox>0 0 499 499</bbox>" in text
    assert "图2 标记1<bbox>500 500 999 999</bbox>" in text


def test_an_unlabelled_region_reads_the_way_the_output_contract_asks_for_it():
    """The fallback name is per-image, so 图N + 第K个区域 matches 「图N的第K个区域」."""
    out = render_prompt(
        "看看这些",
        [_region(1, box=[0.0, 0.0, 0.2, 0.2]), _region(1, box=[0.5, 0.5, 1.0, 1.0])],
        ["m_a1", "m_7c"],
        include_names=True,
    )
    assert "图2 第1个区域<bbox>" in out and "图2 第2个区域<bbox>" in out


def test_the_output_contract_quotes_a_real_label():
    """The instruction has to name something the model can actually use; a generic
    example invites it to answer with coordinates anyway."""
    req = UnderstandVisionInput(
        prompt="这是什么",
        images=["https://example.com/a.jpg"],
        regions=[_region(label="标记7")],
    )
    text = _qwen().build_payload(req)["messages"][1]["content"][0]["text"]
    assert "「标记7」" in text


def test_the_output_contract_survives_unlabelled_regions():
    req = UnderstandVisionInput(
        prompt="这是什么", images=["https://example.com/a.jpg"], regions=[_region()]
    )
    text = _qwen().build_payload(req)["messages"][1]["content"][0]["text"]
    assert "不要在回答中输出 bbox 坐标" in text


def test_no_regions_leaves_the_prompt_byte_identical():
    req = UnderstandVisionInput(prompt="描述这张图", images=["https://example.com/a.jpg"])
    text = _qwen().build_payload(req)["messages"][1]["content"][0]["text"]
    assert text == "描述这张图"


# ── the capability gate ─────────────────────────────────────────────────────


def test_a_model_without_region_edit_refuses():
    req = GenerateImageInput(
        prompt="x", reference_images=["https://example.com/a.jpg"], regions=[_region()]
    )
    ok, reason = _lite().supports(req)
    assert not ok
    assert "region_edit" in reason


def test_the_refusal_names_both_escape_routes():
    """This error is the end of the line — nothing renders a fallback behind it — so it
    has to be executable, not merely correct."""
    req = GenerateImageInput(
        prompt="x", reference_images=["https://example.com/a.jpg"], regions=[_region()]
    )
    _, reason = _lite().supports(req)
    assert "doubao-seedream-5-0-pro" in reason      # (1) a model that can
    assert "understand_vision" in reason            # (2) read, then describe in words


def test_the_region_capable_model_accepts():
    req = GenerateImageInput(
        prompt="x", reference_images=["https://example.com/a.jpg"], regions=[_region()]
    )
    assert _pro().supports(req)[0]


def test_build_payload_refuses_regions_even_when_called_directly():
    """supports() is the gate, but build_payload is reachable on its own — and a region
    silently dropped here is a billed whole-image edit."""
    req = GenerateImageInput(
        prompt="x", reference_images=["https://example.com/a.jpg"], regions=[_region()]
    )
    with pytest.raises(ValueError, match="does not support region editing"):
        _lite().build_payload(req)


def test_seedream_pro_has_no_region_count_ceiling():
    """Explicitly unlimited, not merely unspecified: an invented cap would reject calls
    the model accepts."""
    adapter = _pro()
    assert adapter.max_regions_per_image is None
    req = GenerateImageInput(
        prompt="x",
        reference_images=["https://example.com/a.jpg"],
        regions=[_region(box=[i / 20, 0.0, (i + 1) / 20, 1.0]) for i in range(12)],
    )
    assert adapter.supports(req)[0]


def test_a_declared_per_image_ceiling_is_enforced_per_image():
    adapter = _pro()
    adapter.max_regions_per_image = 2
    try:
        req = GenerateImageInput(
            prompt="x",
            reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
            regions=[
                _region(0, box=[0.0, 0.0, 0.2, 0.2]),
                _region(0, box=[0.2, 0.2, 0.4, 0.4]),
                _region(0, box=[0.4, 0.4, 0.6, 0.6]),
                _region(1, box=[0.0, 0.0, 0.2, 0.2]),
            ],
        )
        ok, reason = adapter.supports(req)
        assert not ok
        assert "image_index=0" in reason and "3" in reason
    finally:
        adapter.max_regions_per_image = None


def test_vision_uses_its_own_capability_name():
    """Reading a marked region does not imply being able to edit by one, so a single
    shared capability would let a reader be routed an editing job."""
    assert "region_understand" in _qwen().capabilities
    assert "region_edit" not in _qwen().capabilities
    assert "region_edit" in _pro().capabilities
    assert "region_understand" not in _pro().capabilities


# ── structural validation (schema layer) ────────────────────────────────────


def test_a_region_pointing_past_the_image_slot_is_rejected():
    with pytest.raises(ValidationError, match="out of range"):
        GenerateImageInput(
            prompt="x",
            reference_images=["https://example.com/a.jpg"],
            regions=[_region(index=2)],
        )


def test_regions_without_any_image_are_rejected():
    with pytest.raises(ValidationError, match="reference_images is empty"):
        GenerateImageInput(prompt="x", regions=[_region()])


def test_crossed_image_sizes_on_one_image_are_rejected():
    with pytest.raises(ValidationError, match="different image_size"):
        GenerateImageInput(
            prompt="x",
            reference_images=["https://example.com/a.jpg"],
            regions=[
                _region(0, image_size=[1024, 768]),
                _region(0, box=[0.5, 0.5, 0.6, 0.6], image_size=[800, 600]),
            ],
        )


def test_image_refs_must_be_one_per_image():
    with pytest.raises(ValidationError, match="image_refs has"):
        GenerateImageInput(
            prompt="x",
            reference_images=["https://example.com/a.jpg"],
            image_refs=["m_a1", "m_7c"],
        )


@pytest.mark.parametrize(
    "box, match",
    [
        ([0.1, 0.2, 0.3], "exactly 4"),
        ([0.1, 0.2, 300.0, 400.0], "normalised"),   # pixels mistaken for normalised
        ([0.5, 0.2, 0.3, 0.4], "inverted"),
        ([0.3, 0.2, 0.3, 0.4], "empty or inverted"),
    ],
)
def test_malformed_boxes_are_rejected(box, match):
    with pytest.raises(ValidationError, match=match):
        RegionSpec(image_index=0, box=box)


def test_coordinates_are_stored_at_a_fixed_precision():
    r = RegionSpec(image_index=0, box=[0.1234567891, 0.2, 0.3, 0.4])
    assert r.box[0] == 0.123457


def test_the_same_structural_rules_apply_to_understand_vision():
    with pytest.raises(ValidationError, match="images is empty"):
        UnderstandVisionInput(prompt="x", regions=[_region()])


# ── routing and preflight ───────────────────────────────────────────────────


def test_auto_routes_a_regions_request_to_a_region_model():
    req = GenerateImageInput(
        prompt="改这里", reference_images=["https://example.com/a.jpg"], regions=[_region()]
    )
    adapter = ModelRouter(_registry()).resolve(req)
    assert "region_edit" in adapter.capabilities


def test_naming_a_model_that_cannot_take_regions_is_a_hard_error():
    req = GenerateImageInput(
        prompt="改这里",
        model="doubao-seedream-5-0-lite",
        reference_images=["https://example.com/a.jpg"],
        regions=[_region()],
    )
    with pytest.raises(CFGPUError) as exc:
        ModelRouter(_registry()).resolve(req)
    assert exc.value.error_type == "invalid_params"
    assert "understand_vision" in exc.value.user_message


def test_validate_only_reports_the_rendered_payload_without_sending():
    req = GenerateImageInput(
        prompt="把[[标记1]]换成一只猫",
        model="doubao-seedream-5-0-pro",
        reference_images=["https://example.com/a.jpg"],
        regions=[_region(label="标记1", box=[0.0, 0.0, 1.0, 1.0])],
        image_refs=["m_a1"],
        validate_only=True,
    )
    adapter = ModelRouter(_registry()).resolve(req, for_validation=True)
    result = validate_request(adapter, req)
    assert result["validated"] is True
    # The whole point of the preflight is that the *exact* request is inspectable before
    # anything is billed — including where the coordinates landed in the sentence.
    assert "<bbox>0 0 999 999</bbox>" in result["payload"]["prompt"]


def test_validate_only_catches_an_out_of_range_region_before_billing():
    req = GenerateImageInput(
        prompt="x",
        model="doubao-seedream-5-0-lite",
        reference_images=["https://example.com/a.jpg"],
        regions=[_region()],
        validate_only=True,
    )
    adapter = ModelRouter(_registry()).resolve(req, for_validation=True)
    with pytest.raises(CFGPUError, match="region_edit"):
        validate_request(adapter, req)


def test_regions_and_group_images_are_mutually_exclusive():
    """A group is several independent pictures; a region marks one place on the single
    picture being edited.

    Unreachable through any shipped model — the only region editor is Pro, and Pro has
    no 组图 capability, so it ignores n. Exercised here on a hypothetical group-capable
    region model so that combination cannot start passing silently the day one exists.
    """
    adapter = _lite()
    adapter.capabilities = adapter.capabilities | {"region_edit"}
    try:
        req = GenerateImageInput(
            prompt="x",
            reference_images=["https://example.com/a.jpg"],
            regions=[_region()],
            n=3,
        )
        ok, reason = adapter.supports(req)
        assert not ok
        assert "组图" in reason
    finally:
        adapter.capabilities = adapter.capabilities - {"region_edit"}


def test_thousandth_coordinates_do_not_drift_a_cell():
    """Binary floats can land a hair under an integer, and ``floor`` would then move the
    edge one cell left — silently, and in exactly the direction the inclusive-far-edge
    rule was written to avoid. Coordinates are stored at 6 decimals, so every value that
    should land on a grid line is a thousandth; all 999 of them are checked here rather
    than trusting a spot check.
    """
    for k in range(1, GRID):
        v = k / GRID
        assert to_grid([v, v, 1.0, 1.0])[:2] == (k, k), v
