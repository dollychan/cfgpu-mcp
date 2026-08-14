"""MiniMax H3 via comfy-gateway — payload, response parsing, and the two-model split.

The split between ``cfdream/minimax-h3`` and ``cfdream/minimax-h3-r2v`` is not a
stylistic one: they are different weights (fl2va vs ref2va) that cannot be mixed,
and each rejects the other's material slots. Enforcing that in ``supports()``
rather than leaving it to the gateway's 400 is what makes ``model="auto"`` route
*between* them, so the routing tests below are as load-bearing as the payload ones.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfgpu_mcp.adapters.cfdream_h3 import CfdreamH3Adapter, CfdreamH3RefAdapter
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


@pytest.fixture(scope="module")
def registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401  (triggers @register_python_adapter)

    r = AdapterRegistry(model_dir=MODELS_DIR, available_providers={"cfgpu", "comfy"})
    r.load()
    return r


@pytest.fixture
def t2v(registry) -> CfdreamH3Adapter:
    return registry.get("cfdream/minimax-h3")


@pytest.fixture
def r2v(registry) -> CfdreamH3RefAdapter:
    return registry.get("cfdream/minimax-h3-r2v")


def _req(**kw) -> GenerateVideoInput:
    kw.setdefault("prompt", "a red panda in the snow")
    kw.setdefault("resolution", "480p")
    return GenerateVideoInput(**kw)


# ── wiring ───────────────────────────────────────────────────────────────────


def test_yaml_resolves_to_the_python_adapters(t2v, r2v):
    assert isinstance(t2v, CfdreamH3Adapter) and not isinstance(t2v, CfdreamH3RefAdapter)
    assert isinstance(r2v, CfdreamH3RefAdapter)


def test_r2v_inherits_the_shared_fields_through_extends(t2v, r2v):
    """These describe the same weights family on the same GPU — they must not drift."""
    for field in ("provider", "task_type", "endpoint", "poll_endpoint", "resolutions",
                  "max_duration_seconds", "is_async"):
        assert getattr(r2v, field) == getattr(t2v, field), field
    assert r2v.poll_config.default_timeout == t2v.poll_config.default_timeout


def test_public_ids_never_expose_the_directory_name(t2v, r2v):
    assert t2v.model_name == "cfdream/minimax-h3"
    assert r2v.model_name == "cfdream/minimax-h3-r2v"
    assert t2v.adapter_id == "cfdream-minimax-h3"  # internal only


# ── build_payload ────────────────────────────────────────────────────────────


def test_payload_carries_native_types(t2v):
    """Not stringified. A payload_mapping would send "5"/"True" and, worse, a
    list's Python repr — the gateway tolerates the first two and not the third."""
    p = t2v.build_payload(_req(duration_seconds=5, with_audio=False))
    assert p["model"] == "cfdream/minimax-h3"
    assert p["duration_seconds"] == 5 and isinstance(p["duration_seconds"], int)
    assert p["with_audio"] is False
    assert p["resolution"] == "480p"


def test_unset_material_slots_are_omitted_not_emptied(t2v):
    p = t2v.build_payload(_req())
    assert "first_frame" not in p and "last_frame" not in p


def test_frames_are_sent_when_present(t2v):
    p = t2v.build_payload(_req(first_frame="https://x/a.jpg", last_frame="https://x/b.jpg"))
    assert p["first_frame"] == "https://x/a.jpg"
    assert p["last_frame"] == "https://x/b.jpg"


def test_r2v_sends_reference_lists_as_lists(r2v):
    p = r2v.build_payload(_req(
        prompt="<Picture 1> dances",
        reference_images=["https://x/1.jpg", "https://x/2.jpg"],
        reference_audios=["https://x/a.wav"],
    ))
    assert p["reference_images"] == ["https://x/1.jpg", "https://x/2.jpg"]
    assert p["reference_audios"] == ["https://x/a.wav"]
    assert "reference_videos" not in p
    assert "first_frame" not in p


def test_seed_rides_model_specific_to_the_top_level(t2v):
    """The gateway takes seed as a top-level field, and build_payload flattens
    model_specific — so the caller writes model_specific={"seed": N} and nothing
    has to special-case it."""
    p = t2v.build_payload(_req(model_specific={"seed": 4667556858703757508}))
    assert p["seed"] == 4667556858703757508


# ── parse_response ───────────────────────────────────────────────────────────


_SUCCESS = {
    "id": "9f1c3a7e",
    "status": "succeeded",
    "data": [{"url": "https://oss.test/h3/9f1c/out.mp4?sig=x",
              "storage": {"provider": "oss", "bucket": "b", "key": "h3/9f1c/out.mp4"}}],
    "expires_at": "2026-08-14T11:20:00Z",
    "seed": 4667556858703757508,
    "usage": {"gpu_seconds": 96.3, "width": 864, "height": 480,
              "length": 124, "fps": 24, "actual_duration": 5.167},
}


def test_parse_response_extracts_url_and_usage(t2v):
    r = t2v.parse_response(_SUCCESS)
    assert r.urls == ["https://oss.test/h3/9f1c/out.mp4?sig=x"]
    assert r.task_id == "9f1c3a7e"
    assert r.usage["width"] == 864 and r.usage["actual_duration"] == 5.167


def test_seed_survives_the_response(t2v):
    """★ The whole reproducibility handle. GenericAdapter hardcodes seed=None,
    which would silently drop the one value a caller needs to re-generate a
    result they liked — and drop it on the success path, where nobody looks."""
    assert t2v.parse_response(_SUCCESS).seed == 4667556858703757508


def test_expires_at_is_the_gateway_value_not_now_plus_24h(t2v):
    """★ The link's clock started when the artifact was published, which may be
    long before this response is read (single serial GPU, tasks queue). Reporting
    "24h from now" hands a re-hosting consumer an expiry that outlives the link."""
    r = t2v.parse_response(_SUCCESS)
    assert r.expires_at == datetime(2026, 8, 14, 11, 20, tzinfo=UTC)


def test_missing_expires_at_falls_back_rather_than_crashing(t2v):
    r = t2v.parse_response({**_SUCCESS, "expires_at": None})
    assert r.expires_at is not None


def test_unparseable_expires_at_falls_back(t2v):
    r = t2v.parse_response({**_SUCCESS, "expires_at": "not-a-date"})
    assert r.expires_at is not None


def test_in_flight_response_yields_no_urls(t2v):
    r = t2v.parse_response({"id": "9f1c", "status": "running"})
    assert r.urls == []


# ── supports(): the two-model split ──────────────────────────────────────────


def test_t2v_rejects_reference_materials_and_names_the_other_model(t2v):
    ok, reason = t2v.supports(_req(reference_images=["https://x/1.jpg"]))
    assert not ok and "cfdream/minimax-h3-r2v" in reason


def test_r2v_rejects_frames_and_names_the_other_model(r2v):
    ok, reason = r2v.supports(_req(first_frame="https://x/a.jpg",
                                   reference_images=["https://x/1.jpg"]))
    assert not ok and "cfdream/minimax-h3" in reason


def test_r2v_requires_at_least_one_reference(r2v):
    ok, reason = r2v.supports(_req())
    assert not ok and "at least one" in reason


@pytest.mark.parametrize("field,cap", [
    ("reference_images", 9), ("reference_videos", 3), ("reference_audios", 3),
])
def test_r2v_enforces_the_autogrow_caps(r2v, field, cap):
    at_cap = _req(**{field: [f"https://x/{i}.jpg" for i in range(cap)]})
    assert r2v.supports(at_cap)[0]
    over = _req(**{field: [f"https://x/{i}.jpg" for i in range(cap + 1)]})
    ok, reason = r2v.supports(over)
    assert not ok and str(cap) in reason


def test_smart_duration_is_rejected_by_both(t2v, r2v):
    """-1 means "let the model choose"; H3 has no such mode, and the frame count
    is a required node input, so the request has nothing to become."""
    assert not t2v.supports(_req(duration_seconds=-1))[0]
    assert not r2v.supports(_req(duration_seconds=-1,
                                 reference_images=["https://x/1.jpg"]))[0]


@pytest.mark.parametrize("res", ["480p", "720p", "1080p"])
def test_all_three_tiers_are_open(t2v, r2v, res):
    """Widened from 480p-only on 2026-08-14, matching the gateway's
    OPEN_RESOLUTIONS. Both models, since r2v inherits the list through extends."""
    assert t2v.supports(_req(resolution=res))[0]
    assert r2v.supports(_req(resolution=res, reference_images=["https://x/1.jpg"]))[0]


def test_the_declared_list_now_matches_the_fleet_enum_exactly(t2v):
    """★ Widening made this model's ``resolutions`` a no-op — say so out loud.

    ``GenerateVideoInput.resolution`` is a Literal of exactly these three, so no
    request can now carry a value H3 would reject; the declaration constrains
    nothing today. It is kept rather than deleted because it is the record of
    what the *gateway* accepts (its ``OPEN_RESOLUTIONS``, three megapixel values),
    and the two lists are maintained in different repos. The day the fleet enum
    gains a tier — a 2K model arrives, say — this declaration goes back to being
    load-bearing with no code change, exactly as ``max_duration_seconds`` did when
    Seedance 2.5 pushed the schema-wide ceiling from 15 to 30.
    """
    from typing import get_args

    fleet = set(get_args(GenerateVideoInput.model_fields["resolution"].annotation))
    assert set(t2v.resolutions) == fleet == {"480p", "720p", "1080p"}

    # And the guard itself still works, for when the two do diverge again.
    ok, reason = t2v.supports(GenerateVideoInput.model_construct(prompt="x", resolution="2k"))
    assert not ok and "480p" in reason


def test_duration_within_range_is_accepted(t2v):
    assert t2v.supports(_req(duration_seconds=15))[0]
    assert not t2v.supports(_req(duration_seconds=16))[0]


# ── routing ──────────────────────────────────────────────────────────────────


def test_auto_picks_r2v_when_references_are_present(registry):
    """★ The payoff of putting the exclusions in supports(): auto routes between
    the two models instead of picking one and failing at POST."""
    chosen = ModelRouter(registry).resolve(
        _req(prompt="<Picture 1> dances",
             reference_images=["https://x/1.jpg"],
             model=["cfdream/minimax-h3", "cfdream/minimax-h3-r2v"]),
    )
    assert chosen.model_name == "cfdream/minimax-h3-r2v"


def test_auto_picks_t2v_for_a_plain_prompt(registry):
    chosen = ModelRouter(registry).resolve(
        _req(model=["cfdream/minimax-h3", "cfdream/minimax-h3-r2v"]),
    )
    assert chosen.model_name == "cfdream/minimax-h3"


def test_fleet_auto_does_not_land_on_h3_for_a_default_request(registry):
    """★ One serial GPU must stay out of the path of ordinary traffic.

    This used to hold for the wrong reason: a default generate_video asks for
    720p, and H3 had only opened 480p, so it was filtered out before scoring ever
    ran. Since 2026-08-14 all three tiers are open and H3 *is* a candidate — the
    property is now carried by ``speed_tier: 1`` alone, which is where it belongs
    ("~96s of GPU for 5s of video" is a speed fact, not a resolution fact).

    So this test now exercises the scoring path rather than the filter, and it is
    the thing that fails if someone "fixes" the tiers to look more flattering.
    """
    router = ModelRouter(registry)
    req = GenerateVideoInput(prompt="a cat")
    assert registry.get("cfdream/minimax-h3").supports(req)[0], "候选资格是前提，不是结论"
    assert not router.resolve(req).model_name.startswith("cfdream/")


def test_named_model_still_gets_the_friendly_reason(registry):
    """Explicitly naming the wrong one of the pair must explain the split, not
    raise an AssertionError out of build_payload."""
    from cfgpu_mcp.errors import CFGPUError

    with pytest.raises(CFGPUError) as e:
        ModelRouter(registry).resolve(
            _req(model="cfdream/minimax-h3", reference_images=["https://x/1.jpg"])
        )
    assert "cfdream/minimax-h3-r2v" in e.value.user_message


# ── model card ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("adapter_id", ["cfdream-minimax-h3", "cfdream-minimax-h3-r2v"])
def test_card_documents_the_silent_degradations(adapter_id):
    """These three are the ones that produce a *wrong result with no error*, so
    a card missing them is worse than no card — the caller has no other source."""
    card = (MODELS_DIR / adapter_id / "card.md").read_text()
    assert "seed" in card
    assert "5.17" in card              # quantized duration ≠ requested duration
    assert "480p" in card


def test_r2v_card_spells_out_the_prompt_tag_rule():
    """Materials that aren't referenced by <Picture N> are silently ignored by
    the model — no error, just a video that used none of them."""
    card = (MODELS_DIR / "cfdream-minimax-h3-r2v" / "card.md").read_text()
    assert "<Picture 1>" in card and "<Video 1>" in card and "<Audio 1>" in card
    assert "24fps" in card             # reference videos are not resampled for you
