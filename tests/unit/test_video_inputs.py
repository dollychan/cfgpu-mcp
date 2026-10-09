"""The ``inputs:`` declaration and each adapter's ``supports()`` must agree.

Undeclared slots are refused by adapter code (so the refusal can name a sibling model),
not by the base class — which means the declaration and the code are two copies of one
fact. This file is what keeps them one: for every video model it enumerates slot
combinations, asks ``supports()``, and checks the answers against the declaration in
both directions.
"""

import itertools
from pathlib import Path

import pytest

from cfgpu_mcp.adapters.inputs import (
    AUDIO_ROLES,
    InputSlot,
    VIDEO_INPUT_SLOTS,
    describe_inputs,
    parse_inputs,
)
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.tool_registry import GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"
_VISUAL = ("first_frame", "last_frame", "reference_images", "reference_videos")


def _video_adapters():
    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    return sorted(registry.list_all(task_type="video"), key=lambda a: a.adapter_id)


_ADAPTERS = _video_adapters()


def _candidate_counts(slot: str, spec: InputSlot | None) -> list[int]:
    if slot in ("first_frame", "last_frame"):
        return [0, 1]
    counts = {0, 1}
    if spec is not None and spec.max is not None:
        counts |= {spec.max, spec.max + 1}
    return sorted(counts)


def _request(counts: dict[str, int]) -> GenerateVideoInput:
    kwargs: dict = {"prompt": "a cat walks"}
    for slot, n in counts.items():
        if not n:
            continue
        if slot in ("first_frame", "last_frame"):
            kwargs[slot] = f"https://example.com/{slot}.png"
        else:
            ext = {"reference_images": "png", "reference_videos": "mp4", "reference_audios": "mp3"}[slot]
            kwargs[slot] = [f"https://example.com/{slot}/{i}.{ext}" for i in range(n)]
    return GenerateVideoInput(**kwargs)


def _accepted_combinations(adapter) -> list[dict[str, int]]:
    """Every probed slot-count combination ``supports()`` accepts."""
    inputs = adapter.inputs or {}
    axes = [_candidate_counts(slot, inputs.get(slot)) for slot in VIDEO_INPUT_SLOTS]
    accepted = []
    for combo in itertools.product(*axes):
        counts = dict(zip(VIDEO_INPUT_SLOTS, combo))
        ok, _ = adapter.supports(_request(counts))
        if ok:
            accepted.append(counts)
    return accepted


@pytest.fixture(scope="module")
def accepted():
    return {a.adapter_id: _accepted_combinations(a) for a in _ADAPTERS}


def test_every_video_model_declares_its_inputs():
    missing = [a.adapter_id for a in _ADAPTERS if a.inputs is None]
    assert not missing, f"video models without an inputs: block: {missing}"


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_supports_never_accepts_beyond_the_declaration(adapter, accepted):
    """No accepted request uses an undeclared slot or exceeds a declared count."""
    for counts in accepted[adapter.adapter_id]:
        for slot, n in counts.items():
            if not n:
                continue
            spec = adapter.inputs.get(slot)
            assert spec is not None, f"{adapter.adapter_id} accepted undeclared {slot}: {counts}"
            if spec.max is not None:
                assert n <= spec.max, f"{adapter.adapter_id} accepted {n} {slot} (declared max {spec.max})"
        if counts["reference_audios"] and not any(counts[s] for s in _VISUAL):
            assert adapter.inputs["reference_audios"].standalone, (
                f"{adapter.adapter_id} accepted audio-only input but declares standalone: false"
            )


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_every_declared_slot_and_limit_is_reachable(adapter, accepted):
    """The declaration promises nothing ``supports()`` refuses."""
    combos = accepted[adapter.adapter_id]
    for slot, spec in adapter.inputs.items():
        used = [c[slot] for c in combos if c[slot]]
        assert used, f"{adapter.adapter_id} declares {slot} but no probed request using it is accepted"
        if spec.max is not None:
            assert spec.max in used, (
                f"{adapter.adapter_id} declares {slot} max {spec.max} but never accepts that many"
            )
    audio = adapter.inputs.get("reference_audios")
    if audio is not None and audio.standalone:
        assert any(
            c["reference_audios"] and not any(c[s] for s in _VISUAL) for c in combos
        ), f"{adapter.adapter_id} declares standalone audio but refuses every audio-only request"


# ── the parser ───────────────────────────────────────────────────────────────


def test_audio_slot_requires_a_role():
    with pytest.raises(ValueError, match="role must be one of"):
        parse_inputs("m", {"reference_audios": {"max": 1}})
    for role in AUDIO_ROLES:
        assert parse_inputs("m", {"reference_audios": {"role": role}})["reference_audios"].role == role


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"reference_image": {}}, "unknown slots"),
        ({"first_frame": {"max": 2}}, "unsupported keys"),
        ({"reference_images": {"role": "reference"}}, "unsupported keys"),
        ({"reference_images": {"max": 0}}, "positive integer"),
        ({"reference_images": 3}, "must be a mapping"),
    ],
)
def test_malformed_inputs_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_inputs("m", raw)


def test_null_removes_an_inherited_slot_and_empty_means_no_material():
    assert parse_inputs("m", {"first_frame": None, "last_frame": {}}) == {"last_frame": InputSlot()}
    assert parse_inputs("m", {}) == {}
    assert parse_inputs("m", None) is None


def test_legacy_limit_keys_are_rejected_rather_than_ignored():
    from cfgpu_mcp.adapters.seedance_video import SeedanceVideoAdapter

    config = {
        "adapter_id": "x", "cfgpu_model_id": "x", "task_type": "video",
        "endpoint": "/v", "max_reference_images": 9,
    }
    with pytest.raises(ValueError, match="moved into the inputs"):
        SeedanceVideoAdapter.from_config(config)


def test_variant_inherits_and_overrides_inputs_slot_by_slot():
    by_id = {a.adapter_id: a for a in _ADAPTERS}
    assert by_id["doubao-seedance-2-0-fast"].inputs == by_id["wan-2-0"].inputs
    pro = by_id["doubao-seedance-1-5-pro"].inputs
    assert set(pro) == {"first_frame", "last_frame"}
    r2v = by_id.get("cfdream-minimax-h3-r2v")
    if r2v is not None:  # registered only where the comfy provider is configured
        assert "first_frame" not in r2v.inputs


def test_describe_lists_role_and_standalone_only_for_audio():
    described = describe_inputs(parse_inputs("m", {
        "first_frame": {},
        "reference_images": {"max": 9},
        "reference_audios": {"max": 1, "role": "driving"},
    }))
    assert described == {
        "first_frame": {},
        "reference_images": {"max": 9},
        "reference_audios": {"max": 1, "role": "driving", "standalone": False},
    }


# ── canonical tasks vs. inputs ───────────────────────────────────────────────
#
# A task list is agent-facing selection metadata and is written by hand, so it is the
# fact most likely to drift. These tests make the structural half of every video task
# claim checkable: a model cannot claim a task whose required material it has no slot
# for, nor one that ``supports()`` refuses for every request carrying that material.
# The semantic half (does it *edit* well) still rests on the card.


def _task_catalog():
    from cfgpu_mcp.task_catalog import load_task_catalog

    return load_task_catalog()[1]


def _satisfies(counts: dict[str, int], requires: dict) -> bool:
    return all(counts[s] for s in requires.get("all", [])) and (
        not requires.get("any") or any(counts[s] for s in requires["any"])
    )


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_declared_tasks_are_carried_by_the_declared_inputs(adapter):
    catalog = _task_catalog()
    for task in adapter.tasks:
        requires = catalog[task].get("requires")
        if not requires:
            continue
        missing = set(requires.get("all", [])) - set(adapter.inputs)
        assert not missing, f"{adapter.adapter_id} claims {task} but accepts no {sorted(missing)}"
        if requires.get("any"):
            assert set(requires["any"]) & set(adapter.inputs), (
                f"{adapter.adapter_id} claims {task} but accepts none of {requires['any']}"
            )
        if requires.get("audio_role"):
            role = adapter.inputs["reference_audios"].role
            assert role == requires["audio_role"], (
                f"{adapter.adapter_id} claims {task}, which needs audio role "
                f"{requires['audio_role']!r}, but its audio role is {role!r}"
            )


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_declared_tasks_are_reachable_through_supports(adapter, accepted):
    catalog = _task_catalog()
    for task in adapter.tasks:
        requires = catalog[task].get("requires")
        if not requires:
            continue
        assert any(_satisfies(c, requires) for c in accepted[adapter.adapter_id]), (
            f"{adapter.adapter_id} claims {task} but supports() refuses every request "
            f"carrying its required inputs {requires}"
        )


@pytest.mark.parametrize("adapter", _ADAPTERS, ids=lambda a: a.adapter_id)
def test_a_driving_audio_track_and_audio_driven_video_imply_each_other(adapter):
    """The one task fully decided by an input fact, so it is checked both ways.

    A model that takes a driving track but does not say so is invisible to the agent
    looking for one; a model that says so without a driving track would be sent speech
    it then treats as background material — a plausible, billed, unsynced video.
    """
    audio = adapter.inputs.get("reference_audios")
    drives = audio is not None and audio.role == "driving"
    claims = "audio_driven_video" in adapter.tasks
    assert drives == claims, (
        f"{adapter.adapter_id}: audio role {audio.role if audio else None!r} "
        f"but audio_driven_video {'claimed' if claims else 'not claimed'}"
    )


def test_reference_to_video_no_longer_promises_audio():
    task = _task_catalog()["reference_to_video"]
    assert "reference_audios" not in task["requires"].get("any", [])
    assert "reference_audios" not in task["requires"].get("all", [])


@pytest.mark.parametrize(
    "adapter", [a for a in _ADAPTERS if a.adapter_id != a.model_name], ids=lambda a: a.adapter_id
)
def test_refusals_name_the_public_model_never_the_adapter_id(adapter):
    """adapter_id is internal; a refusal that names it hands the caller an id it cannot use."""
    inputs = adapter.inputs or {}
    axes = [_candidate_counts(slot, inputs.get(slot)) for slot in VIDEO_INPUT_SLOTS]
    for combo in itertools.product(*axes):
        ok, reason = adapter.supports(_request(dict(zip(VIDEO_INPUT_SLOTS, combo))))
        if not ok:
            assert adapter.adapter_id not in reason, reason
            assert "capabilit" not in reason, reason
