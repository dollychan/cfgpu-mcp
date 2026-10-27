"""A real call (``validate_only=false``) repairs placeholders instead of refusing.

The preflight (``validate_only=true``) is where a caller learns its handles or markup
are wrong, and it stays exactly as strict as before — the strict cases are pinned next
to each check (``test_prompt_dialect.py`` / ``test_prompt_structure.py`` /
``test_regions.py``). A real call is either a plan that already passed the preflight or
a person's edit of one on an approval card, so refusing it only throws the request
away. Its single rule, at every layer: a ``[[...]]`` that cannot be used as written is
written as its own text — brackets dropped, the inside verbatim — and nothing is ever
*guessed* (a guess that picks the wrong material is billed and looks right).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cfgpu_mcp.adapters import prompt_markup as markup
from cfgpu_mcp.adapters.prompt_render import check_prompt_refs, render_for
from cfgpu_mcp.adapters.regions import render_prompt
from cfgpu_mcp.config import load_registry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput, RegionSpec, UnderstandVisionInput

IMG = "https://x/i{}.png"
VID = "https://x/v{}.mp4"
AUD = "https://x/a{}.mp3"


@pytest.fixture(scope="module")
def registry():
    return load_registry(disabled_models=[])


def _video(**kw) -> GenerateVideoInput:
    kw.setdefault("resolution", None)
    return GenerateVideoInput(**kw)


# ── the shared primitive ─────────────────────────────────────────────────────


def test_degrade_writes_refused_placeholders_as_text_and_keeps_the_rest():
    out = markup.degrade("[[a]] 与 [[ghost]] [[shot 3-1]] [[say 你好]] [[text: 开业]]", lambda n: not isinstance(n, markup.Ref) or n.token == "a")
    assert out == "[[a]] 与 ghost shot 3-1 say 你好 [[text: 开业]]"


def test_degrade_drops_an_overlapping_shot_but_keeps_the_later_ones():
    out = markup.degrade("[[shot 0-3]] a [[shot 1-4]] b [[shot 3-6]] c", lambda n: True)
    assert out == "[[shot 0-3]] a shot 1-4 b [[shot 3-6]] c"
    assert [s.index for s in markup.shots(markup.parse(out))] == [1, 2]


# ── schema layer (model-independent): handle lists ───────────────────────────


def test_an_unresolved_placeholder_becomes_text_and_a_resolved_one_stays():
    req = _video(prompt="[[a]] 和 [[主角]] 在雨里", reference_images=[IMG.format(1)], image_refs=["a"])
    assert req.prompt == "[[a]] 和 主角 在雨里"
    assert req.image_refs == ["a"]


@pytest.mark.parametrize(
    "items, refs",
    [
        # the front image was removed: cutting would make `a` name what used to be `b`
        ([IMG.format(2)], ["a", "b"]),
        # an image was added in front: padding would make `a` name the new one
        ([IMG.format(0), IMG.format(1)], ["a"]),
    ],
)
def test_a_list_of_the_wrong_length_is_dropped_whole_never_realigned(items, refs):
    req = _video(prompt="[[a]] [[b]] 走路", reference_images=items, image_refs=refs)
    assert req.image_refs is None
    assert req.prompt == "a b 走路"


def test_a_handle_naming_two_items_is_never_resolved_to_either():
    req = _video(
        prompt="[[a]] 跳舞", reference_images=[IMG.format(1)], reference_videos=[VID.format(1)],
        image_refs=["a"], video_refs=["a"],
    )
    assert req.image_refs == [""] and req.video_refs == [""]
    assert req.prompt == "a 跳舞"


@pytest.mark.parametrize("handle", ["a#1", " a", "text", "shot"])
def test_an_unusable_handle_is_blanked(handle):
    req = _video(prompt="x", reference_images=[IMG.format(1)], image_refs=[handle])
    assert req.image_refs == [""]


def test_a_region_placeholder_on_video_becomes_text():
    req = _video(prompt="[[a#标记1]] 变红", reference_images=[IMG.format(1)], image_refs=["a"])
    assert req.prompt == "a#标记1 变红"


# ── schema layer: structure markers ──────────────────────────────────────────


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("[[shot 3-1]] x", "shot 3-1 x"),
        ("[[say 你好]]", "say 你好"),
        ("[[text]]", "text"),
        ("[[shot 0-3]] a [[shot 1-4]] b", "[[shot 0-3]] a shot 1-4 b"),
    ],
)
def test_malformed_markup_becomes_text(prompt, expected):
    assert _video(prompt=prompt).prompt == expected


def test_a_shot_past_an_explicit_duration_becomes_text():
    assert _video(prompt="[[shot 0-3]] 走 [[shot 3-8]] 跑", duration_seconds=5).prompt == "[[shot 0-3]] 走 shot 3-8 跑"


def test_the_preflight_still_refuses_what_the_real_call_repairs():
    with pytest.raises(ValueError, match="available here"):
        _video(prompt="[[主角]]", reference_images=[IMG.format(1)], image_refs=["a"], validate_only=True)
    with pytest.raises(ValueError, match="same length"):
        _video(prompt="x", reference_images=[IMG.format(1)], image_refs=["a", "b"], validate_only=True)


# ── per-model layer (pinned model) ───────────────────────────────────────────


def test_a_kind_the_model_cannot_name_becomes_text(registry):
    adapter = registry.get("wan2.7-r2v")
    req = _video(
        model="wan2.7-r2v", prompt="[[a]] 用 [[v]] 的声音说话", duration_seconds=5,
        reference_videos=[VID.format(1)], video_refs=["a"],
        reference_audios=[AUD.format(1)], audio_refs=["v"],
    )
    assert adapter.supports(req) == (True, "")
    rendered = render_for(adapter, req)
    assert "[[" not in rendered.req.prompt and " v 的声音" in rendered.req.prompt
    assert rendered.source_prompt == "[[a]] 用 [[v]] 的声音说话"


def test_an_unnamed_reference_is_accepted_on_a_pinned_model(registry):
    adapter = registry.get("cfdream/minimax-h3-r2v")
    req = _video(model="cfdream/minimax-h3-r2v", prompt="a hero walks", reference_images=[IMG.format(1)], resolution="480p")
    assert adapter.supports(req) == (True, "")
    strict = _video(model="cfdream/minimax-h3-r2v", prompt="a hero walks", reference_images=[IMG.format(1)], resolution="480p", validate_only=True)
    assert not adapter.supports(strict)[0]


def test_auto_routing_still_steers_around_a_model_that_would_ignore_a_reference(registry):
    """Routing around costs nothing, so the per-model checks keep doing it under auto."""
    req = _video(model=["cfdream/minimax-h3-r2v", "doubao-seedance-2-0"], prompt="a hero walks", reference_images=[IMG.format(1)])
    assert ModelRouter(registry).resolve(req).model_name == "doubao-seedance-2-0"


def test_a_shot_past_the_models_own_duration_becomes_text(registry):
    adapter = registry.get("wan2.7-t2v")
    req = _video(model="wan2.7-t2v", prompt="[[shot 0-9]] 甲")
    assert adapter.supports(req) == (True, "")
    assert render_for(adapter, req).req.prompt.startswith("shot 0-9")


def test_a_marker_the_dialect_cannot_write_becomes_text():
    adapter = SimpleNamespace(model_name="m", prompt_dialect="image-ordinal", task_type="video", prompt_limits=None,
                              resolve_duration_seconds=lambda req: 5)
    assert check_prompt_refs(adapter, _video(model="m", prompt="[[shot 0-3]] 走")) == (True, "")


def test_the_length_limit_still_applies(registry):
    """Not a placeholder question — the upstream would refuse it anyway."""
    adapter = registry.get("wan-video")
    ok, reason = adapter.supports(_video(model="wan-video", prompt="[[shot 0-3]] " + "雨" * 500, duration_seconds=5))
    assert not ok and "500 个汉字" in reason


# ── image tools ──────────────────────────────────────────────────────────────


def test_an_unresolved_image_handle_becomes_text(registry):
    req = GenerateImageInput(prompt="以[[own]]为准，参考[[pose]]", model="cf-image-2", reference_images=[IMG.format(1)], image_refs=["own"])
    out = render_for(registry.get("cf-image-2"), req).req.prompt
    assert out == "以图1为准，参考pose"


def test_an_image_list_of_the_wrong_length_is_dropped_whole(registry):
    req = GenerateImageInput(prompt="以[[own]]为准", model="cf-image-2", reference_images=[IMG.format(1)], image_refs=["own", "pose"])
    assert req.image_refs is None
    assert render_for(registry.get("cf-image-2"), req).req.prompt == "以own为准"


def test_video_only_markers_on_an_image_become_text(registry):
    req = GenerateImageInput(prompt="[[shot 0-3]] 海报 [[text: 开业]]", model="gpt-image-2")
    out = render_for(registry.get("gpt-image-2"), req).req.prompt
    assert out.startswith("shot 0-3 海报") and "[[" not in out and "开业" in out


def test_an_ambiguous_region_label_is_never_resolved_to_either_image():
    regions = [
        RegionSpec(image_index=0, box=[0.1, 0.1, 0.2, 0.2], label="标记1"),
        RegionSpec(image_index=1, box=[0.3, 0.3, 0.4, 0.4], label="标记1"),
    ]
    out = render_prompt("把[[标记1]]换成红色", regions, ["a", "b"], include_names=False, lenient=True)
    assert out.startswith("把标记1换成红色")
    # Neither region is dropped: both fall through to the suffix, each naming its own image.
    assert out.count("<bbox>") == 2 and "图1" in out and "图2" in out


def test_understand_vision_repairs_its_handles():
    req = UnderstandVisionInput(prompt="[[a]] 里有什么", images=[IMG.format(1)], image_refs=["a", "b"])
    assert req.image_refs is None
