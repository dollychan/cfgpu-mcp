"""MiniMax H3 Turbo: H3's wire contract; today the only difference is no 2K."""

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.minimax_h3 import MinimaxH3Adapter
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
def turbo(registry) -> MinimaxH3Adapter:
    return registry.get("minimax-turbo-h3")


def _req(**kwargs) -> GenerateVideoInput:
    kwargs.setdefault("prompt", "A train crossing a snowy valley")
    kwargs.setdefault("aspect_ratio", "16:9")
    return GenerateVideoInput(**kwargs)


def test_identifiers(turbo):
    assert type(turbo) is MinimaxH3Adapter
    assert turbo.adapter_id == "cfgpu-minimax-turbo-h3"
    assert turbo.model_name == "minimax-turbo-h3"
    assert turbo.display_name == "MiniMax H3 Turbo"
    assert turbo.build_payload(_req())["model"] == "minimax-turbo-h3"


def test_only_resolution_differs_from_h3(registry, turbo):
    h3 = registry.get("MiniMax-H3")
    assert turbo.resolutions == ["480p", "768p"]
    assert turbo.default_resolution == h3.default_resolution
    for field in ("provider", "endpoint", "poll_endpoint", "tasks", "inputs", "outputs",
                  "aspect_ratios", "min_duration_seconds", "max_duration_seconds",
                  "prompt_dialect", "prompt_limits"):
        assert getattr(turbo, field) == getattr(h3, field), field


@pytest.mark.parametrize("resolution,wire", [("480p", "480P"), ("768p", "768P"), (None, "768P")])
def test_tiers(turbo, resolution, wire):
    req = _req(resolution=resolution)
    assert turbo.supports(req) == (True, "")
    assert turbo.build_payload(req)["resolution"] == wire


def test_2k_is_refused(turbo):
    ok, reason = turbo.supports(_req(resolution="2k"))
    assert not ok
    assert "480p, 768p" in reason


def test_not_a_routing_default(registry, turbo):
    assert turbo.default_for == frozenset()
    assert ModelRouter(registry).resolve(GenerateVideoInput(prompt="waves")).model_name == "MiniMax-H3"
