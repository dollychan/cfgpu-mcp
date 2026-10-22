"""MiniMax-H3-Max: H3's wire contract with its own tiers, durations and ``extra``."""

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.minimax_h3 import MinimaxH3MaxAdapter
from cfgpu_mcp.adapters.prompt_render import render_for
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import GenerateVideoInput


MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


@pytest.fixture(scope="module")
def registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(MODELS_DIR, available_providers={"cfgpu"})
    registry.load()
    return registry


@pytest.fixture(scope="module")
def adapter(registry) -> MinimaxH3MaxAdapter:
    result = registry.get("minimax-h3-max")
    assert isinstance(result, MinimaxH3MaxAdapter)
    return result


def _req(**kwargs) -> GenerateVideoInput:
    kwargs.setdefault("prompt", "A train crossing a snowy valley")
    kwargs.setdefault("aspect_ratio", "16:9")
    return GenerateVideoInput(**kwargs)


def test_identifiers_and_routes(adapter):
    assert adapter.adapter_id == "cfgpu-minimax-h3-max"
    assert adapter.display_name == "MiniMax H3 Max"
    assert adapter.model_name == "minimax-h3-max"
    assert adapter.cfgpu_model_id == "minimax-turbo-h3"
    assert adapter.provider == "cfgpu"
    assert adapter.endpoint == "/video/generations"
    assert adapter.poll_endpoint == "/video/tasks/{task_id}"
    assert adapter.build_payload(_req())["model"] == "minimax-turbo-h3"


@pytest.mark.parametrize("resolution,wire", [("480p", "480P"), ("768p", "768P"), (None, "768P")])
def test_tiers_are_480p_and_768p(adapter, resolution, wire):
    req = _req(resolution=resolution)
    assert adapter.supports(req) == (True, "")
    assert adapter.build_payload(req)["resolution"] == wire


@pytest.mark.parametrize("resolution", ["2k", "720p", "1080p"])
def test_2k_and_other_tiers_are_refused(adapter, resolution):
    ok, reason = adapter.supports(_req(resolution=resolution))
    assert not ok
    assert "480p, 768p" in reason


@pytest.mark.parametrize("resolution", ["480p", "768p"])
def test_four_seconds_is_refused_on_every_tier(adapter, resolution):
    ok, reason = adapter.supports(_req(resolution=resolution, duration_seconds=4))
    assert not ok
    assert "5–15" in reason


@pytest.mark.parametrize("seconds", [5, 15])
def test_five_to_fifteen_seconds_are_accepted(adapter, seconds):
    assert adapter.supports(_req(duration_seconds=seconds)) == (True, "")


def test_prompt_extend_maps_to_prompt_expansion_mode(adapter):
    assert "extra" not in adapter.build_payload(_req())
    assert adapter.build_payload(_req(prompt_extend=False))["extra"] == {"prompt_expansion_mode": "disabled"}
    assert adapter.build_payload(_req(prompt_extend=True))["extra"] == {"prompt_expansion_mode": "balanced"}


def test_model_specific_extra_wins(adapter):
    req = _req(prompt_extend=False, model_specific={"extra": {"prompt_expansion_mode": "quality"}})
    assert adapter.supports(req) == (True, "")
    assert adapter.build_payload(req)["extra"] == {"prompt_expansion_mode": "quality"}


@pytest.mark.parametrize("extra", [
    {"prompt_expansion_mode": "balance"},
    {"prompt_expansion_mode": ""},
    {"prompt_expansion_mode": True},
    {"other": "x"},
    "balanced",
])
def test_undeclared_extra_is_refused_locally(adapter, extra):
    ok, reason = adapter.supports(_req(model_specific={"extra": extra}))
    assert not ok
    assert "prompt_expansion_mode" in reason


def test_markup_turns_the_unset_expansion_off(adapter):
    out = render_for(adapter, _req(prompt="[[shot 0-3]] 走", duration_seconds=5)).req
    assert adapter.build_payload(out)["extra"] == {"prompt_expansion_mode": "disabled"}


def test_markup_keeps_an_explicit_choice(adapter):
    out = render_for(adapter, _req(prompt="[[shot 0-3]] 走", duration_seconds=5, prompt_extend=True)).req
    assert adapter.build_payload(out)["extra"] == {"prompt_expansion_mode": "balanced"}


def test_h3_itself_never_sends_extra(registry):
    h3 = registry.get("MiniMax-H3")
    assert "extra" not in h3.build_payload(_req(prompt_extend=False))


def test_not_a_routing_default(registry, adapter):
    assert adapter.default_for == frozenset()
    assert "voice_reference_video" not in adapter.tasks
    assert ModelRouter(registry).resolve(GenerateVideoInput(prompt="waves")).model_name == "MiniMax-H3"
