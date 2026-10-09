"""``aspect_ratios:`` is the one place a model's ratio set lives, and supports() obeys it.

Before it existed the set lived in eight code paths, and three image families checked
it only in ``validation_corrections``: the preflight corrected an unsupported ratio
while the billed call sent it on — Nano Banana passed it upstream, and Seedream /
万相 2.7 图像 silently rendered 9:21 or 3:1 as a square.
"""

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.base import _nearest_ratio, _parse_aspect_ratios, _schema_ratios
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _adapters():
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    return sorted(
        registry.list_all(task_type="image") + registry.list_all(task_type="video"),
        key=lambda a: a.adapter_id,
    )


_ADAPTERS = _adapters()
_VIDEO_MATERIAL = (
    {},
    {"first_frame": "https://example.com/f.png"},
    {"reference_images": ["https://example.com/r.png"]},
    {"reference_videos": ["https://example.com/v.mp4"]},
)


def _request(adapter, ratio):
    if adapter.task_type == "image":
        return GenerateImageInput(prompt="a cat", aspect_ratio=ratio)
    for material in _VIDEO_MATERIAL:   # the first material shape this model takes at all
        if adapter.supports(GenerateVideoInput(prompt="a cat", **material))[0]:
            return GenerateVideoInput(prompt="a cat", aspect_ratio=ratio, **material)
    raise AssertionError(f"{adapter.adapter_id} accepts no probe request")


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_supports_accepts_exactly_the_declared_ratios(adapter):
    """Both ways: undeclared ⇒ every schema ratio accepted; declared ⇒ exactly those."""
    for ratio in _schema_ratios(adapter.task_type):
        ok, reason = adapter.supports(_request(adapter, ratio))
        expected = adapter.aspect_ratios is None or ratio == "adaptive" or ratio in adapter.aspect_ratios
        assert ok == expected, f"{adapter.adapter_id} {ratio}: {reason or 'accepted'}"
        if not expected:
            assert "aspect_ratio" in reason and adapter.model_name in reason


@pytest.mark.parametrize("adapter", [a for a in _ADAPTERS if a.aspect_ratios], ids=lambda a: a.adapter_id)
def test_preflight_correction_is_a_declared_ratio_the_billed_call_accepts(adapter):
    for ratio in _schema_ratios(adapter.task_type):
        req = _request(adapter, ratio)
        corrected = adapter.validation_corrections(req)
        if ratio in adapter.aspect_ratios:
            assert "aspect_ratio" not in corrected
        elif ratio != "adaptive":
            assert corrected["aspect_ratio"] in adapter.aspect_ratios
            assert adapter.supports(req.model_copy(update=corrected))[0]


@pytest.mark.parametrize(
    "ratio, supported, expected",
    [
        ("3:2", ("1:1", "4:3", "16:9"), "4:3"),
        ("9:21", ("1:1", "9:16", "16:9"), "9:16"),
        ("3:1", ("1:1", "16:9", "21:9"), "21:9"),
        ("21:9", ("16:9",), "16:9"),
        ("1:1", ("3:4", "4:3"), "4:3"),   # an exact tie: a square counts as landscape
    ],
)
def test_nearest_ratio(ratio, supported, expected):
    assert _nearest_ratio(ratio, supported) == expected


@pytest.mark.parametrize(
    "raw",
    [["adaptive"], ["16:9", "adaptive"], ["5:4"], [], "16:9"],
)
def test_malformed_declarations_are_rejected(raw):
    with pytest.raises(ValueError, match="aspect_ratios"):
        _parse_aspect_ratios("m", "video", raw)


def test_audio_models_cannot_declare_ratios():
    with pytest.raises(ValueError, match="image and video models only"):
        _parse_aspect_ratios("m", "audio", ["1:1"])


@pytest.mark.parametrize("model", ["doubao-seedream-5-0-lite", "wan2.7-image", "cf-pro"])
def test_the_billed_path_now_refuses_what_used_to_be_squared_or_passed_on(model):
    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    ok, reason = registry.get(model).supports(GenerateImageInput(prompt="x", aspect_ratio="9:21"))
    assert not ok and "9:21" in reason
