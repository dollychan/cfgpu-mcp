"""The first/last-frame × reference-media refusal names both fixes, not just the conflict.

A frame is the video's literal opening picture; a reference only carries the subject into
a newly composed shot. Which was meant lives in the prompt, so the server does not pick —
but the bare "mutually exclusive" left the caller guessing, and a wrong guess is another
round trip (production, 2026-09-30: an H3 lip-sync request with first_frame +
reference_audios whose prompt said "场景替换为晨光咖啡馆"). Each route is spelled out as
the exact argument moves, including a model switch where this model cannot take it.

The conflict is a parameter constraint of one model, not a task nobody can do (2026-10-27:
"use this one of my references as the first frame" was answered with "the model does not
support it"). So a third route keeps the frame *and* the references: a model that takes
both slots, or the reference route with the prompt naming the picture as the opening shot.
"""
from __future__ import annotations

import pytest

from cfgpu_mcp.adapters.base import frames_vs_references_reason
from cfgpu_mcp.tool_registry import GenerateVideoInput


@pytest.fixture(scope="module")
def registry():
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter
    from cfgpu_mcp.config import get_registry

    return get_registry()


def _req(**kwargs) -> GenerateVideoInput:
    kwargs.setdefault("prompt", "x")
    return GenerateVideoInput(**kwargs)


@pytest.mark.parametrize("model", ["MiniMax-H3", "doubao-seedance-2-0"])
def test_the_reported_request_gets_both_routes_on_this_model(registry, model):
    adapter = registry.get(model)
    ok, reason = adapter.supports(_req(
        first_frame="m_1", reference_audios=["m_2"], aspect_ratio="16:9",
        duration_seconds=5, resolution="480p",
    ))

    assert not ok
    assert "mutually exclusive" in reason
    # Route 1: keep the audio, move the picture — on this same model.
    assert "把 first_frame 的图移入 reference_images，删除 first_frame，reference_audios 保留" in reason
    # Route 2: keep the frame, and say what dropping the audio costs.
    assert "保留 first_frame，删除 reference_audios" in reason
    assert "对口型" in reason
    # Routes ① and ② stay on this model; only ③ may name another.
    route_one_two = reason.split("③")[0]
    assert "改用" not in route_one_two


def test_merge_is_mentioned_only_when_reference_images_already_exist():
    without = frames_vs_references_reason("m", _req(first_frame="f", reference_audios=["a"]))
    with_refs = frames_vs_references_reason("m", _req(first_frame="f", reference_images=["r"]))

    assert "合并" not in without
    assert "合并" in with_refs
    assert "对口型" not in with_refs


def test_a_model_without_reference_support_points_route_one_elsewhere(registry):
    adapter = registry.get("doubao-seedance-1-5-pro")
    assert "reference_images" not in adapter.inputs

    ok, reason = adapter.supports(_req(first_frame="f", reference_images=["r"]))

    assert not ok
    assert "改用 tasks 含 reference_to_video 的模型" in reason
    assert "multi_modal_reference" not in reason
    assert "doubao-seedance-1-5-pro）" not in reason  # never suggests itself


@pytest.mark.parametrize(
    "model", ["happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v"]
)
def test_happyhorse_names_the_scenario_model_for_each_route(registry, model):
    """Each HappyHorse scenario is its own model, so both fixes are a model switch —
    and i2v/r2v used to report only half the conflict ("does not support ...")."""
    ok, reason = registry.get(model).supports(
        _req(first_frame="f", reference_images=["r"], resolution="720p", duration_seconds=5)
    )

    assert not ok
    assert "mutually exclusive" in reason
    assert "改用 model=happyhorse-1.0-r2v" in reason
    assert "改用 model=happyhorse-1.0-i2v" in reason


def _route_three(reason: str) -> str:
    assert "③" in reason
    return reason.split("③", 1)[1]


@pytest.mark.parametrize("model", ["doubao-seedance-2-0", "MiniMax-H3", "happyhorse-1.0-i2v"])
def test_the_refusal_says_the_request_is_still_achievable(registry, model):
    ok, reason = registry.get(model).supports(
        _req(first_frame="f", reference_images=["r"], duration_seconds=5)
    )

    assert not ok
    assert "as parameters on this model" in reason
    assert "still achievable" in reason
    third = _route_three(reason)
    assert "其余参考也要保留（framed_reference_video）" in third
    assert "以 [[名字]] 作为视频首帧" in third
    assert "不保证与原图逐像素一致" in third  # approximate, and says so


def test_route_three_names_models_that_take_frames_and_references_together(registry):
    _, reason = registry.get("doubao-seedance-2-0").supports(
        _req(first_frame="f", reference_images=["r"])
    )
    third = _route_three(reason)

    assert "model=kling-v3-omni" in third
    assert "model=wan2.7-r2v" in third
    # Grok accepts the call but its first_frame is just refer_images[0]: no frame is kept.
    assert "cf-imagine-video" not in third
    assert "model=doubao-seedance-2-0" not in third  # never suggests itself


def test_every_named_model_really_accepts_the_combination(registry):
    from cfgpu_mcp.adapters.base import models_accepting_frames_with_references

    req = _req(first_frame="f", last_frame="l", reference_images=["r"])
    named = models_accepting_frames_with_references(req, exclude="doubao-seedance-2-0")

    assert named  # Kling takes first + last frame beside references
    for name in named:
        adapter = registry.get(name)
        assert adapter.pins_frames_with_references
        assert adapter.supports(req)[0], name


def test_both_frames_get_their_own_handle(registry):
    _, reason = registry.get("doubao-seedance-2-0").supports(
        _req(first_frame="f", last_frame="l", reference_images=["r"])
    )

    assert "[[首帧名]] 作为视频首帧、[[尾帧名]] 作为尾帧" in _route_three(reason)


def test_route_three_on_a_model_without_references_points_elsewhere(registry):
    _, reason = registry.get("doubao-seedance-1-5-pro").supports(
        _req(first_frame="f", reference_images=["r"])
    )

    assert "改用 tasks 含 framed_reference_video 的模型" in _route_three(reason)
