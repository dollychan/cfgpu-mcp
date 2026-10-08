"""``duration_seconds=-1`` is Seedance-only, and every refusal says what to write instead.

-1 means "the model picks the length". Only the Seedance request schema has such a
value, so every other model refuses it — and the refusal must carry the remedy (this
model's real range), because the server will not pick a billed length on the caller's
behalf. The over-maximum refusal used to suggest "-1" to every model, sending callers
from one refusal straight into the next.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.seedance_video import SeedanceVideoAdapter
from cfgpu_mcp.tool_registry import GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"

#: Requests that each scenario-specific model accepts, tried in order.
_SCENARIOS = (
    {},
    {"first_frame": "https://x.test/f.png"},
    {"reference_images": ["https://x.test/r.png"]},
    {"reference_videos": ["https://x.test/v.mp4"]},
)


@pytest.fixture(scope="module")
def video_adapters():
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    reg = AdapterRegistry(model_dir=MODELS_DIR)
    reg.load()
    return reg.list_all(task_type="video")


def _scenario(adapter) -> dict:
    for kwargs in _SCENARIOS:
        ok, _ = adapter.supports(
            GenerateVideoInput(prompt="x", duration_seconds=5, aspect_ratio="16:9", **kwargs)
        )
        if ok:
            return kwargs
    pytest.fail(f"{adapter.model_name}: no baseline scenario is accepted")


def test_only_the_seedance_family_and_a_durationless_edit_accept_minus_one(video_adapters):
    accepting = {a.model_name for a in video_adapters if a.accepts_smart_duration}
    seedance = {a.model_name for a in video_adapters if isinstance(a, SeedanceVideoAdapter)}

    # happyhorse-1.0-video-edit follows the source video's length and sends no duration.
    assert accepting == seedance | {"happyhorse-1.0-video-edit"}


def test_minus_one_is_refused_with_the_models_own_range(video_adapters):
    for adapter in video_adapters:
        if adapter.accepts_smart_duration:
            continue
        ok, reason = adapter.supports(GenerateVideoInput(
            prompt="x", duration_seconds=-1, aspect_ratio="16:9", **_scenario(adapter)
        ))
        assert not ok, adapter.model_name
        assert f"{adapter.min_duration_seconds}–{adapter.max_duration_seconds} seconds" in reason
        assert reason.startswith(adapter.model_name)  # the public id, never adapter_id


def test_over_maximum_suggests_minus_one_only_where_it_is_accepted(video_adapters):
    for adapter in video_adapters:
        if adapter.max_duration_seconds >= 30:
            continue
        ok, reason = adapter.supports(GenerateVideoInput(
            prompt="x", duration_seconds=30, aspect_ratio="16:9", **_scenario(adapter)
        ))
        assert not ok, adapter.model_name
        assert ("or -1" in reason) == adapter.accepts_smart_duration, reason


def test_two_second_duration_is_available_to_documented_wan_models(video_adapters):
    """The shared schema permits 2 seconds; adapters retain their own lower bound."""
    for adapter in video_adapters:
        ok, _ = adapter.supports(GenerateVideoInput(
            prompt="x", duration_seconds=2, aspect_ratio="16:9", **_scenario(adapter)
        ))
        assert ok == (adapter.model_name in {
            "wan2.7-i2v", "wan2.6-i2v", "wan2.6-t2v", "wan2.6-r2v",
            "wan2.7-t2v", "wan2.7-r2v", "wan2.7-videoedit",
        }), adapter.model_name
