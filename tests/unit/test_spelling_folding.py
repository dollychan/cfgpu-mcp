"""Closed vocabularies accept any unambiguous spelling of a member.

A value that differs from a member only in case, surrounding space, or (for ratios) the
separator names that member and nothing else; refusing it costs the caller a round trip
to learn a spelling (production, 2026-09-30: ``resolution="480P"`` copied from the
MiniMax H3 card failed a preflight). Folding happens on every entry point — the schema
(Mode B and the generate_* service calls), the FastMCP signatures (Mode A validates
those before the service runs), the service functions that take raw strings (Mode B and
the CLI bypass the schema), the model registry, and the CLI's own choices. The published
enums stay canonical, and a value matching no member is still rejected.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.tool_registry import (
    GenerateAudioInput,
    GenerateImageInput,
    GenerateVideoInput,
    ListModelProfilesInput,
    ListModelsInput,
    ListVoiceProfilesInput,
    UnderstandVisionInput,
)

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


# ── Schema ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("asked", ["16：9", " 16:9 ", "16 : 9", "16/9", "16x9", "16X9", "16×9", "16*9"])
@pytest.mark.parametrize("model", [GenerateImageInput, GenerateVideoInput])
def test_ratio_separators_and_space_fold(model, asked):
    assert model(prompt="x", aspect_ratio=asked).aspect_ratio == "16:9"


def test_adaptive_folds_case():
    assert GenerateVideoInput(prompt="x", aspect_ratio="Adaptive").aspect_ratio == "adaptive"


@pytest.mark.parametrize(
    "model,kwargs,field,expected",
    [
        (GenerateImageInput, {"prompt": "x", "quality_tier": "BEST"}, "quality_tier", "best"),
        (GenerateVideoInput, {"prompt": "x", "quality_tier": "Fast"}, "quality_tier", "fast"),
        (GenerateAudioInput, {"text": "x", "quality_tier": " Balanced "}, "quality_tier", "balanced"),
        (GenerateAudioInput, {"text": "x", "audio_format": "MP3"}, "audio_format", "mp3"),
        (GenerateAudioInput, {"text": "x", "emotion": "Happy"}, "emotion", "happy"),
        (UnderstandVisionInput, {"prompt": "x", "analysis_depth": "Thorough"}, "analysis_depth", "thorough"),
        (ListModelsInput, {"task_type": "Video"}, "task_type", "video"),
        (ListModelProfilesInput, {"media_type": "IMAGE"}, "media_type", "image"),
        (ListModelProfilesInput, {"match": "Any"}, "match", "any"),
        (ListVoiceProfilesInput, {"gender": "Female"}, "gender", "female"),
        (ListVoiceProfilesInput, {"age": "Middle_Aged"}, "age", "middle_aged"),
    ],
)
def test_case_folds_to_the_canonical_member(model, kwargs, field, expected):
    assert getattr(model(**kwargs), field) == expected


@pytest.mark.parametrize(
    "model,kwargs",
    [
        (GenerateVideoInput, {"prompt": "x", "aspect_ratio": "16:10"}),
        (GenerateVideoInput, {"prompt": "x", "aspect_ratio": "16:9:1"}),
        (GenerateImageInput, {"prompt": "x", "aspect_ratio": "adaptive"}),  # video-only
        (GenerateVideoInput, {"prompt": "x", "quality_tier": "cheap"}),
        (GenerateAudioInput, {"text": "x", "audio_format": "ogg"}),
    ],
)
def test_a_value_matching_no_member_is_still_rejected(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


def test_published_enums_stay_canonical():
    props = GenerateImageInput.model_json_schema()["properties"]
    assert props["resolution"]["enum"] == ["1K", "1.5K", "2K", "3K", "4K"]
    assert props["aspect_ratio"]["enum"][0] == "1:1"


# ── FastMCP signatures (Mode A validates these before the service runs) ─────


def test_mcp_signatures_fold_too():
    from cfgpu_mcp.server import mcp

    voices = mcp._tool_manager.get_tool("list_voice_profiles").fn_metadata.arg_model
    vision = mcp._tool_manager.get_tool("understand_vision").fn_metadata.arg_model
    parsed = voices.model_validate({"gender": "Male", "age": "Senior"})
    assert (parsed.gender, parsed.age) == ("male", "senior")
    assert vision.model_validate({"prompt": "x", "analysis_depth": "Fast"}).analysis_depth == "fast"


# ── Service functions taking raw strings (Mode B / CLI) ─────────────────────


@pytest.mark.asyncio
async def test_list_models_folds_task_type():
    from cfgpu_mcp.service import model as model_service

    rows = await model_service.list_models("Video")
    assert rows and all(row["task_type"] == "video" for row in rows)


@pytest.mark.asyncio
async def test_list_voice_profiles_folds_gender_and_model_ids():
    from cfgpu_mcp.config import get_registry
    from cfgpu_mcp.service import model as model_service

    audio = sorted(a.model_name for a in get_registry().list_all(task_type="audio"))
    exact = await model_service.list_voice_profiles(model_ids=[audio[0]], gender="female")
    folded = await model_service.list_voice_profiles(model_ids=[audio[0].upper()], gender="Female")
    assert folded == exact


# ── Model names ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    reg = AdapterRegistry(model_dir=MODELS_DIR)
    reg.load()
    return reg


def test_no_two_models_collide_once_case_and_space_are_folded(registry):
    """Pins the precondition for folding: every folded name names one model. A collision
    would not misroute (the registry drops ambiguous keys), but it would silently turn
    that spelling back into an error — so a new model that causes one should be noticed."""
    ambiguous = [key for key, adapter in registry._by_folded.items() if adapter is None]
    assert ambiguous == []


@pytest.mark.parametrize("asked", ["minimax-h3", " MiniMax-H3 ", "MINIMAX-H3"])
def test_model_name_folds(registry, asked):
    assert registry.get(asked).model_name == "MiniMax-H3"


def test_a_folded_collision_falls_back_to_exact_matching(registry):
    a, b = registry.list_all(task_type="video")[:2]
    reg = AdapterRegistry(model_dir=MODELS_DIR)
    reg._register(a)
    b_clone = type(b).__new__(type(b))
    b_clone.__dict__.update(b.__dict__)
    b_clone.model_name = a.model_name.upper()
    reg._register(b_clone)

    assert reg.get(a.model_name) is a                   # exact still works
    assert reg.get(a.model_name.upper()) is b_clone     # exact still works
    with pytest.raises(KeyError):
        reg.get(a.model_name.lower() + " ")             # ambiguous → not guessed


def test_candidate_lists_accept_folded_names(registry):
    from cfgpu_mcp.router import ModelRouter

    req = GenerateVideoInput(prompt="x", model=["minimax-h3"], aspect_ratio="16:9")
    assert ModelRouter(registry).resolve(req).model_name == "MiniMax-H3"


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_choices_mirror_the_schema_and_ignore_case():
    import click

    from cfgpu_mcp.cli.cmd_generate import generate
    from cfgpu_mcp.cli.cmd_models import models

    def choice(command, name):
        param = next(p for p in command.params if p.name == name)
        assert isinstance(param.type, click.Choice)
        return param.type

    image_res = choice(generate.commands["image"], "resolution")
    assert list(image_res.choices) == ["1K", "1.5K", "2K", "3K", "4K"]
    assert image_res.convert("1.5k", None, None) == "1.5K"
    assert choice(generate.commands["video"], "resolution").convert("480P", None, None) == "480p"
    image_ratios = choice(generate.commands["image"], "aspect_ratio").choices
    assert {"21:9", "9:21", "3:1", "1:3"} <= set(image_ratios)
    assert set(choice(models.commands["list"], "task_type").choices) == {
        "image", "video", "audio", "understand",
    }
