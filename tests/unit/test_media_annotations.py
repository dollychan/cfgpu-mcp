"""Material-slot (``x-cfgpu-media``) annotations.

Eight parameters across four tools carry media references rather than scalars. The
annotation is what lets a host find them programmatically — to re-describe them in its
own reference format, and to resolve exactly those values before the call instead of
guessing by string shape. These tests pin the properties a consumer relies on: the
annotation reaches every exposure, its prose is derived from it, and the slot set does
not silently drift from the field types.
"""
from __future__ import annotations

import asyncio

import pytest

from cfgpu_mcp.tool_registry import (
    _REGISTRY,
    _WIRE_FORMS,
    MEDIA_ANNOTATION_KEY,
    GenerateImageInput,
    GenerateVideoInput,
    UnderstandVisionInput,
    get_anthropic_tools,
    get_media_slots,
    media_field,
)

# The expected slot inventory, spelled out rather than derived — this is the list a host
# integrates against, so it should be impossible to add or drop one without editing a test.
_EXPECTED_SLOTS: dict[str, dict[str, str]] = {
    "generate_image": {"reference_images": "image"},
    "generate_video": {
        "first_frame": "image",
        "last_frame": "image",
        "reference_images": "image",
        "reference_videos": "video",
        "reference_audios": "audio",
    },
    "generate_audio": {},
    "understand_vision": {"images": "image", "video": "video"},
    "task_status": {},
    "task_wait": {},
    "list_models": {},
    "get_model_card": {},
}


def _mcp_input_schema(tool_name: str) -> dict:
    from cfgpu_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == tool_name).inputSchema


# ── inventory ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_SLOTS))
def test_media_slot_inventory(tool_name):
    slots = get_media_slots(tool_name)
    assert {name: spec["role"] for name, spec in slots.items()} == _EXPECTED_SLOTS[tool_name]


def test_every_registered_tool_is_covered_by_the_inventory():
    """A newly registered tool must be classified here, media-carrying or not."""
    assert {name for name, _ in _REGISTRY} == set(_EXPECTED_SLOTS)


@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_SLOTS))
def test_arity_matches_the_declared_python_type(tool_name):
    """``arity`` is what tells a host whether to resolve a scalar or iterate — it must
    agree with the field's actual type, or the host resolves the wrong shape."""
    model = next(m for name, m in _REGISTRY if name == tool_name)
    for fname, spec in get_media_slots(tool_name).items():
        annotation = str(model.model_fields[fname].annotation)
        is_list = "list[str]" in annotation
        assert is_list == (spec["arity"] == "many"), (
            f"{tool_name}.{fname}: arity={spec['arity']!r} contradicts type {annotation}"
        )


def test_annotation_carries_permissive_facts_only():
    """The spec says what *may* appear in a slot, never what is forbidden.

    role/arity/accepts hold for the whole tool, and being permissive costs nothing worse
    than a call some model rejects — which is the status quo. Caps and exclusions are
    per-model while this schema is per-tool, so a restrictive key here would be a union
    presented as a universal, and a host enforcing it would reject calls that are legal on
    the model actually in use. Such limits belong in the prose, with get_model_card as the
    authority. Adding a key here means answering that objection first.
    """
    for tool_name in _EXPECTED_SLOTS:
        for fname, spec in get_media_slots(tool_name).items():
            assert set(spec) == {"role", "arity", "accepts", "slot"}, (
                f"{tool_name}.{fname}: unexpected annotation key(s) "
                f"{set(spec) - {'role', 'arity', 'accepts', 'slot'}}"
            )


def test_accepts_uses_only_the_closed_vocabulary():
    for tool_name in _EXPECTED_SLOTS:
        for fname, spec in get_media_slots(tool_name).items():
            assert spec["accepts"], f"{tool_name}.{fname}: accepts must not be empty"
            unknown = set(spec["accepts"]) - set(_WIRE_FORMS)
            assert not unknown, f"{tool_name}.{fname}: unknown wire form(s) {unknown}"


def test_media_field_rejects_an_unknown_wire_form():
    with pytest.raises(ValueError, match="unknown media wire form"):
        media_field(slot="x", role="image", arity="one", accepts=["ftp_url"])


# ── description composition ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model, fname",
    [
        (GenerateImageInput, "reference_images"),
        (GenerateVideoInput, "first_frame"),
        (GenerateVideoInput, "reference_images"),
        (UnderstandVisionInput, "images"),
        (UnderstandVisionInput, "video"),
    ],
)
def test_description_is_slot_plus_generated_wire_clause(model, fname):
    """The prose is derived from the annotation, so the two cannot drift — and a host
    that substitutes its own wire clause can rebuild the text from ``slot``."""
    field = model.model_fields[fname]
    spec = field.json_schema_extra[MEDIA_ANNOTATION_KEY]
    assert field.description.startswith(spec["slot"])
    for accepted in spec["accepts"]:
        assert _WIRE_FORMS[accepted] in field.description


def test_descriptions_no_longer_hardcode_url_wording_in_the_semantic_half():
    """The semantic half must stay format-neutral: a host with its own reference format
    replaces only the generated clause, so any 'URL' left in ``slot`` would survive the
    rewrite and go on contradicting that host's ledger."""
    for tool_name in _EXPECTED_SLOTS:
        for fname, spec in get_media_slots(tool_name).items():
            assert "url" not in spec["slot"].lower(), (
                f"{tool_name}.{fname}: slot text names a wire format; move it to `accepts`"
            )


def test_asset_handles_are_claimed_on_exactly_the_generate_video_slots():
    """The Seedance / WAN request schema takes a 素材 ID on its image, video and audio
    inputs alike, and nowhere else. Both directions matter: claiming it on another tool
    invites a host to route a handle into a slot the API rejects, while dropping it from
    one of these five silently downgrades a caller to fetching bytes it need not fetch."""
    claimed = {
        (tool_name, fname)
        for tool_name in _EXPECTED_SLOTS
        for fname, spec in get_media_slots(tool_name).items()
        if "asset_url" in spec["accepts"]
    }
    assert claimed == {("generate_video", fname) for fname in _EXPECTED_SLOTS["generate_video"]}


# ── reaches every exposure ──────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_SLOTS))
def test_mcp_schema_carries_the_annotation(tool_name):
    """Mode A builds its schema from the wrapper's bare signature, so the annotation only
    arrives via server._inject_media_annotations — the path most likely to be forgotten."""
    props = _mcp_input_schema(tool_name)["properties"]
    for fname, spec in get_media_slots(tool_name).items():
        assert props[fname].get(MEDIA_ANNOTATION_KEY) == spec


@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_SLOTS))
def test_anthropic_schema_carries_the_annotation(tool_name):
    """Mode B gets it free from model_json_schema(); pinned so a future schema-building
    change (e.g. stripping unknown keys) cannot quietly drop it."""
    tool = next(t for t in get_anthropic_tools() if t["name"] == tool_name)
    props = tool["input_schema"]["properties"]
    for fname, spec in get_media_slots(tool_name).items():
        assert props[fname].get(MEDIA_ANNOTATION_KEY) == spec


def test_get_media_slots_returns_a_copy():
    first = get_media_slots("generate_video")
    first["reference_images"]["accepts"].append("mutated")
    assert "mutated" not in get_media_slots("generate_video")["reference_images"]["accepts"]


def test_non_media_fields_carry_no_annotation():
    for tool_name in _EXPECTED_SLOTS:
        model = next(m for name, m in _REGISTRY if name == tool_name)
        media = set(get_media_slots(tool_name))
        for fname, field in model.model_fields.items():
            if fname in media:
                continue
            extra = field.json_schema_extra
            assert not (isinstance(extra, dict) and MEDIA_ANNOTATION_KEY in extra), (
                f"{tool_name}.{fname} is annotated as a media slot but is not in the inventory"
            )
