"""``resolution`` is a fleet vocabulary with per-model subsets and per-model defaults.

Two things make this worth its own file rather than a few lines in the adapter tests.

First, the fleet enum and the per-model sets are edited in different files by different
changes, and every disagreement between them is silent in one direction or the other: a
tier in the enum that no ordering table knows about skips its ``validation_corrections``
fallback without erroring, and a ``default_resolution`` outside a model's own
``resolutions`` removes that model from every call that omits the argument — which is
most calls — while every existing test keeps passing.

Second, the schema default is ``None`` on purpose, and that is the load-bearing part.
``resolution`` is checked by ``supports()``, so a concrete fleet-wide default is a value
every model must offer or be filtered out of ``model="auto"`` by a request the caller
never made. MiniMax H3 offers 480p/768p/2k and no 720p at all, so under the old ``"720p"``
default it would have been unreachable by the plainest possible call and a hard error
when named explicitly — the same failure ``_T2V_DEFAULT_RATIO`` exists to avoid one
field over.
"""
from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from cfgpu_mcp.adapters.base import _RESOLUTION_ORDER
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.task_manager import validate_request
from cfgpu_mcp.tool_registry import GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


@pytest.fixture(scope="module")
def registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    # The whole fleet, not one deployment: disabled_models is a per-deployment
    # blocklist and must not decide which models these invariants cover.
    reg = AdapterRegistry(model_dir=MODELS_DIR)
    reg.load()
    return reg


def _video_adapters(registry: AdapterRegistry):
    return registry.list_all(task_type="video")


def _fleet_enum() -> set[str]:
    # Optional[Literal[...]] — get_args yields (Literal[...], NoneType).
    annotation = GenerateVideoInput.model_fields["resolution"].annotation
    return set(get_args(get_args(annotation)[0]))


# ── the enum and the ordering table ──────────────────────────────────────────


def test_resolution_order_covers_the_whole_schema_enum():
    """A tier missing from the table is skipped by the fallback, not rejected by it.

    ``validation_corrections`` filters both the request and the model's set through
    ``_RESOLUTION_ORDER`` and returns ``{}`` when it finds nothing — so a new enum
    member added without a rank produces no correction at all, and ``validate_only``
    reports a request the billed call will reject.
    """
    assert _fleet_enum() == set(_RESOLUTION_ORDER)


def test_resolution_order_is_ascending_by_output_size():
    assert list(_RESOLUTION_ORDER) == ["480p", "720p", "768p", "1080p", "2k", "4k"]


# ── per-model sets and defaults ──────────────────────────────────────────────


def test_every_video_model_declares_a_subset_of_the_fleet_enum(registry):
    fleet = _fleet_enum()
    for adapter in _video_adapters(registry):
        if adapter.resolutions is None:
            continue
        assert set(adapter.resolutions) <= fleet, adapter.model_name


def test_every_video_models_default_is_one_it_actually_offers(registry):
    """Otherwise the model is unroutable by every call that omits ``resolution``.

    ``supports()`` runs against the *resolved* tier, so a default outside the declared
    set fails the model's own gate — silently under ``model="auto"`` (it just stops
    being selected) and loudly when it is named.
    """
    for adapter in _video_adapters(registry):
        if adapter.resolutions is None:
            continue
        assert adapter.default_resolution in adapter.resolutions, adapter.model_name


def test_omitting_resolution_rules_no_model_out(registry):
    """The reason the schema default is None. Each model resolves to its own tier.

    Asserted on the *reason* rather than on ``ok``: several models are legitimately
    unavailable to a bare text prompt (the i2v/r2v/edit variants want a frame or a
    reference), and those refusals are not what this is watching for. What must never
    appear is a resolution refusal — that would be the model being ruled out by an
    argument the caller never supplied.
    """
    req = GenerateVideoInput(prompt="海浪拍打礁石")
    assert req.resolution is None
    for adapter in _video_adapters(registry):
        _, reason = adapter.supports(req)
        assert "resolution" not in reason, f"{adapter.model_name}: {reason}"


# ── 768p / 2k are MiniMax H3's alone ─────────────────────────────────────────


def test_minimax_h3_is_the_only_model_offering_768p_and_2k(registry):
    offering = {
        a.model_name
        for a in _video_adapters(registry)
        if a.resolutions and {"768p", "2k"} & set(a.resolutions)
    }
    assert offering == {"MiniMax-H3"}


def test_minimax_h3_sends_its_tiers_verbatim_apart_from_case(registry):
    adapter = registry.get("MiniMax-H3")
    for asked, wire in (("480p", "480P"), ("768p", "768P"), ("2k", "2K"), (None, "768P")):
        req = GenerateVideoInput(prompt="x", aspect_ratio="16:9", resolution=asked)
        assert adapter.build_payload(req)["resolution"] == wire


def test_the_bare_call_reaches_minimax_h3_at_its_own_default(registry):
    """``generate_video(prompt=...)`` — no other arguments — must still land here.

    MiniMax H3 is the declared ``default_for: [balanced]`` video model, and balanced is
    the schema default for ``quality_tier``. A fleet-wide 720p default would have
    filtered it out before the router ever scored it.
    """
    req = GenerateVideoInput(prompt="海浪拍打礁石")
    adapter = ModelRouter(registry).resolve(req)

    assert adapter.model_name == "MiniMax-H3"
    assert adapter.build_payload(req)["resolution"] == "768P"


def test_naming_minimax_h3_with_720p_fails_and_names_the_real_tiers(registry):
    """No silent substitution: 720p used to be translated to 768P behind the caller."""
    adapter = registry.get("MiniMax-H3")
    ok, reason = adapter.supports(GenerateVideoInput(prompt="x", resolution="720p"))

    assert not ok
    assert "480p, 768p, 2k" in reason


# ── validate_only: a model that does not offer the tier says what to send ────


@pytest.mark.parametrize(
    "model,extra,asked,expected",
    [
        # 768p sits between 720p and 1080p, so every other model falls back to 720p —
        # never up to 1080p, which would be a silent upgrade into a pricier tier.
        ("wan-video", {}, "768p", "720p"),
        ("doubao-seedance-2-0", {}, "768p", "720p"),
        ("doubao-seedance-2-0-fast", {}, "768p", "720p"),
        ("wan2.7-t2v", {}, "768p", "720p"),
        ("happyhorse-1.0-t2v", {}, "768p", "720p"),
        ("kling-video-o1", {}, "768p", "720p"),
        # 2k sits above 1080p and below 4k.
        ("wan-video", {}, "2k", "1080p"),
        ("doubao-seedance-2-0", {}, "2k", "1080p"),
        ("doubao-seedance-2-0-fast", {}, "2k", "720p"),
        ("happyhorse-1.0-t2v", {}, "2k", "1080p"),
        # MiniMax H3 supports 480p directly; 720p falls back to that lower tier.
        ("MiniMax-H3", {"aspect_ratio": "16:9"}, "720p", "480p"),
        ("MiniMax-H3", {"aspect_ratio": "16:9"}, "1080p", "768p"),
    ],
)
def test_validate_only_corrects_a_tier_the_model_does_not_offer(
    registry, model, extra, asked, expected
):
    req = GenerateVideoInput(prompt="x", model=model, resolution=asked, **extra)
    adapter = ModelRouter(registry).resolve(req, for_validation=True)

    result = validate_request(adapter, req)

    assert result["corrected_args"]["resolution"] == expected
    # The payload and corrected_args must describe the same request, or a caller that
    # merges the correction sends something the preflight never validated.
    assert result["validated"] is True


def test_validate_only_reports_no_correction_for_an_omitted_resolution(registry):
    """None is already every model's own tier; pinning it would be a choice nobody made."""
    for model in ("MiniMax-H3", "doubao-seedance-2-0-fast", "wan-video"):
        req = GenerateVideoInput(prompt="x", model=model, aspect_ratio="16:9")
        adapter = ModelRouter(registry).resolve(req, for_validation=True)

        assert "resolution" not in validate_request(adapter, req)["corrected_args"]
