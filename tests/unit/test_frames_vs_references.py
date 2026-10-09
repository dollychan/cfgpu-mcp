"""The first/last-frame × reference-media refusal names both fixes, not just the conflict.

A frame is the video's literal opening picture; a reference only carries the subject into
a newly composed shot. Which was meant lives in the prompt, so the server does not pick —
but the bare "mutually exclusive" left the caller guessing, and a wrong guess is another
round trip (production, 2026-09-30: an H3 lip-sync request with first_frame +
reference_audios whose prompt said "场景替换为晨光咖啡馆"). Each route is spelled out as
the exact argument moves, including a model switch where this model cannot take it.
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
    assert "改用" not in reason


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
