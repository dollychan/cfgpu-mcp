"""Keep the canonical task lists complete, valid, and the only task vocabulary."""

import re
from pathlib import Path
from typing import get_args

import pytest
import yaml


ROOT = Path(__file__).parent.parent.parent
MODELS_DIR = ROOT / "src" / "cfgpu_mcp" / "models"
TASKS_PATH = ROOT / "src" / "cfgpu_mcp" / "capabilities" / "media_tasks.yaml"


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def test_media_task_dictionary_has_stable_descriptions():
    data = _load_yaml(TASKS_PATH)
    assert data["schema_version"] == 1
    tasks = data["tasks"]
    assert isinstance(tasks, dict) and tasks

    for task_id, task in tasks.items():
        assert task_id.replace("_", "").isalnum(), task_id
        assert task["media_type"] in {"video", "image", "audio", "understand"}
        assert all(isinstance(task[field], str) and task[field] for field in ("name", "description", "prompt_guidance"))


def test_profile_filter_enum_matches_the_canonical_task_dictionary():
    from cfgpu_mcp.tool_registry import CanonicalTaskId

    assert set(get_args(CanonicalTaskId)) == set(_load_yaml(TASKS_PATH)["tasks"])


def _registry():
    from cfgpu_mcp.adapters.registry import AdapterRegistry

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    return registry


def test_every_model_declares_canonical_tasks():
    """Each model's tasks live in its adapter.yaml (inherited through `extends`)."""
    assert not list(MODELS_DIR.glob("*/profile.yaml")), "profile.yaml was folded into adapter.yaml"
    missing = [a.adapter_id for a in _registry().list_all() if not a.tasks]
    assert not missing, f"models without tasks: {missing}"


def test_the_retired_capability_vocabulary_is_refused():
    from cfgpu_mcp.adapters.generic import GenericAdapter

    with pytest.raises(ValueError, match="capabilities: vocabulary was retired"):
        GenericAdapter.from_config({
            "adapter_id": "x", "cfgpu_model_id": "x", "task_type": "image",
            "endpoint": "/i", "capabilities": ["text_to_image"],
        })
    for raw in MODELS_DIR.glob("*/adapter.yaml"):
        assert "capabilities" not in _load_yaml(raw), raw


@pytest.mark.parametrize(
    "tasks, message",
    [
        (["r2v"], "unknown canonical tasks"),
        (["text_to_video"], "do not belong to a image model"),
        (["text_to_image", "text_to_image"], "duplicates"),
    ],
)
def test_tasks_are_validated_at_load(tasks, message):
    from cfgpu_mcp.adapters.generic import GenericAdapter

    with pytest.raises(ValueError, match=message):
        GenericAdapter.from_config({
            "adapter_id": "x", "cfgpu_model_id": "x", "task_type": "image",
            "endpoint": "/i", "tasks": tasks,
        })


@pytest.mark.parametrize("task", [
    "web_grounded_generation", "tool_calling", "visual_agent",
    "image_reasoning", "long_context", "long_video_understanding", "long_document_understanding",
])
def test_tasks_no_call_can_trigger_are_not_in_the_vocabulary(task):
    """Removed in catalog_version 2: no tool parameter reaches them, so an agent that
    filtered by one picked a model for something its call could never ask for."""
    from cfgpu_mcp.task_catalog import load_task_catalog, parse_tasks

    version, catalog = load_task_catalog()
    assert version == 2
    assert task not in catalog
    with pytest.raises(ValueError, match="unknown canonical tasks"):
        parse_tasks("m", "video", [task])


def test_seedance_reference_and_edit_are_distinct_agent_tasks():
    tasks = _registry().get("doubao-seedance-2-5").tasks
    assert {"reference_to_video", "video_edit", "video_extend"}.issubset(tasks)
    assert "multi_modal_reference" not in tasks


@pytest.mark.parametrize("adapter_id", ["cfgpu-minimax-h3", "cfdream-minimax-h3-r2v"])
def test_minimax_h3_reference_video_supports_video_edit(adapter_id: str):
    assert {"reference_to_video", "video_edit"}.issubset(_registry().get(adapter_id).tasks)


@pytest.mark.asyncio
async def test_profile_catalog_lists_all_qwen_vision_models(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_model_profiles(media_type="understand")
    model_ids = {model["model_id"] for model in catalog["models"]}
    assert {"qwen3.6-plus", "qwen3.7-flash", "qwen3.7-plus", "qwen3.8-max"} <= model_ids


@pytest.mark.asyncio
async def test_profile_catalog_exposes_only_agent_facing_metadata(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_model_profiles(required_tasks=["video_edit"])
    assert catalog["catalog_version"] == 2
    assert set(catalog) == {"catalog_version", "task_catalog", "models"}
    assert set(catalog["task_catalog"]) == {"video_edit"}
    assert all("video_edit" in model["tasks"] for model in catalog["models"])
    assert catalog["models"] == sorted(catalog["models"], key=lambda model: model["model_id"])

    for model in catalog["models"]:
        assert set(model) - {"outputs", "aspect_ratios"} == {
            "model_id",
            "display_name",
            "task_type",
            "tasks",
            "cost_tier",
            "speed_tier",
            "inputs",  # video models only; video_edit is a video task
        }
        # outputs: video models that document their sound only (edit models do not)
        assert model.get("outputs", {"audio": "always"})["audio"] in ("always", "switchable", "never")
        assert "adapter_id" not in model
        assert "capabilities" not in model
        assert "is_async" not in model
        assert "multi_modal_reference" not in model["tasks"]


@pytest.mark.asyncio
async def test_profile_catalog_rejects_unknown_canonical_task():
    from cfgpu_mcp.service import model as model_service

    with pytest.raises(ValueError, match="unknown canonical task IDs"):
        await model_service.list_model_profiles(required_tasks=["r2v"])


@pytest.mark.asyncio
async def test_layer_decomposition_search_returns_an_executable_call_template(monkeypatch):
    """Discovery must provide the API switch and arity, not just a task label."""
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_model_profiles(
        required_tasks=["layer_decomposition"]
    )
    flash = next(
        model
        for model in catalog["models"]
        if model["model_id"] == "doubao-seedream-5-0-flash"
    )
    contract = flash["task_parameters"]["layer_decomposition"]
    assert contract["call_template"] == {
        "prompt": "",
        "n": 1,
        "model_specific": {"layer_decomposition": True, "size": "auto"},
    }
    assert contract["requirements"]["reference_images"] == {
        "required": True,
        "min_items": 1,
        "max_items": 1,
    }
    assert contract["size"] == {
        "default": "auto",
        "accepted": ["auto", "1K", "1.5K", "2K"],
        "explicit_dimensions_supported": False,
    }
    assert contract["result"]["urls"] == {
        "description": "Ordered output URLs: base image first, followed by independently editable transparent layers.",
        "layer_limit": 16,
    }


@pytest.mark.asyncio
async def test_profile_catalog_supports_all_and_any_capability_queries(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    required = ["reference_to_video", "video_edit"]
    all_match = await model_service.list_model_profiles(required_tasks=required)
    any_match = await model_service.list_model_profiles(required_tasks=required, match="any")

    assert all(set(required).issubset(model["tasks"]) for model in all_match["models"])
    assert len(any_match["models"]) > len(all_match["models"])
    assert all(set(required) & set(model["tasks"]) for model in any_match["models"])


@pytest.fixture
def _full_registry(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)


_VOICE_ROW_KEYS = {"voice", "label", "languages", "gender", "age", "tags", "model_ids"}
_VOICE_ROW_OPTIONAL = {"default", "accent", "description"}


@pytest.mark.asyncio
async def test_voice_catalog_is_paginated_and_has_only_agent_facing_selection_data(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(language="中文", limit=2)

    assert set(catalog) == {"catalog_version", "voices", "total", "next_cursor"}
    assert catalog["catalog_version"] == 5
    assert len(catalog["voices"]) == 2
    assert catalog["total"] > len(catalog["voices"])
    assert catalog["next_cursor"] == 2
    # Round-robin: the first page draws from both provider families.
    assert {m for voice in catalog["voices"] for m in voice["model_ids"]} == {
        "MiniMax/speech-2.8-hd",
        "MiniMax/speech-2.8-turbo",
        "seed-tts-2.0",
    }
    for voice in catalog["voices"]:
        assert _VOICE_ROW_KEYS <= set(voice) <= _VOICE_ROW_KEYS | _VOICE_ROW_OPTIONAL
        assert voice["voice"] != voice["label"]
        assert any(code == "zh" or code.startswith("zh-") for code in voice["languages"])


@pytest.mark.asyncio
async def test_hd_and_turbo_share_one_catalog(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(query="沉稳高管")
    voice = next(v for v in catalog["voices"] if v["voice"] == "Chinese (Mandarin)_Reliable_Executive")
    assert voice["model_ids"] == ["MiniMax/speech-2.8-hd", "MiniMax/speech-2.8-turbo"]
    assert voice["label"] == "沉稳高管"
    assert voice["gender"] == "male"
    assert "磁性" in voice["tags"]


@pytest.mark.asyncio
async def test_query_matches_the_description_not_only_the_name(_full_registry):
    """'磁性' is in this voice's description and traits; nothing in its name says so."""
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(query="磁性 男声", limit=100)
    assert "Chinese (Mandarin)_Reliable_Executive" in {v["voice"] for v in catalog["voices"]}
    assert all(v["gender"] == "male" for v in catalog["voices"])


@pytest.mark.asyncio
async def test_voice_catalog_makes_the_generate_audio_value_unambiguous(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(
        model_ids=["MiniMax/speech-2.8-hd"], language="中文", query="播报 男声", limit=10
    )
    announcer = next(voice for voice in catalog["voices"] if voice["label"] == "播报男声")
    assert announcer["voice"] == "Chinese (Mandarin)_Male_Announcer"


@pytest.mark.asyncio
async def test_voice_catalog_preserves_byte_exact_handles(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(query="ProfessionalHost", limit=10)
    assert "Cantonese_ProfessionalHost（F)" in {voice["voice"] for voice in catalog["voices"]}


@pytest.mark.asyncio
async def test_voice_catalog_rejects_unknown_model_and_invalid_paging(_full_registry):
    from cfgpu_mcp.service import model as model_service

    with pytest.raises(ValueError, match="unknown audio model_ids"):
        await model_service.list_voice_profiles(model_ids=["invented-voice-model"])
    with pytest.raises(ValueError, match="limit must be between"):
        await model_service.list_voice_profiles(limit=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["English", "英文", "英语", "en"])
async def test_every_spelling_of_english_finds_both_providers(_full_registry, spelling):
    """The export says 英语 (MiniMax) and 英文 (volcengine); callers say English."""
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(language=spelling, limit=100)
    model_ids = {m for voice in catalog["voices"] for m in voice["model_ids"]}
    assert {"seed-tts-2.0", "MiniMax/speech-2.8-hd"} <= model_ids
    assert all(
        any(code == "en" or code.startswith("en-") for code in voice["languages"])
        for voice in catalog["voices"]
    )


@pytest.mark.asyncio
async def test_regional_variants_narrow_but_the_base_language_covers_them(_full_registry):
    from cfgpu_mcp.service import model as model_service

    british = await model_service.list_voice_profiles(language="英式英语", limit=100)
    assert british["voices"]
    assert all("en-GB" in voice["languages"] for voice in british["voices"])

    brazilian = await model_service.list_voice_profiles(language="巴西葡萄牙语", limit=100)
    portuguese = await model_service.list_voice_profiles(language="葡萄牙语", limit=100)
    assert 0 < brazilian["total"] < portuguese["total"]


@pytest.mark.asyncio
async def test_gender_filter_finds_english_named_voices(_full_registry):
    """Japanese female voices are named Lady/Queen/Woman — no `female` in the id."""
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(language="日文", gender="female", limit=100)
    voices = {voice["voice"] for voice in catalog["voices"]}
    assert {"Japanese_KindLady", "Japanese_ColdQueen", "Japanese_DependableWoman"} <= voices
    assert all(voice["gender"] == "female" for voice in catalog["voices"])


@pytest.mark.asyncio
async def test_age_filter(_full_registry):
    from cfgpu_mcp.service import model as model_service

    children = await model_service.list_voice_profiles(age="child", limit=100)
    assert children["voices"]
    assert all(voice["age"] == "child" for voice in children["voices"])
    with pytest.raises(ValueError, match="age must be one of"):
        await model_service.list_voice_profiles(age="toddler")


@pytest.mark.asyncio
async def test_the_word_male_does_not_match_female_voices(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(query="male", limit=100)
    assert catalog["voices"]
    assert all(voice["gender"] == "male" for voice in catalog["voices"])


@pytest.mark.asyncio
async def test_unknown_gender_is_listed_but_never_gender_filtered(_full_registry, monkeypatch):
    """No exported voice is `unknown` today; the schema still allows it, so pin the rule."""
    from cfgpu_mcp.service import model as model_service
    from cfgpu_mcp.voice_catalog import VoiceCatalog

    catalog = VoiceCatalog.model_validate({
        "schema_version": 1,
        "voices": [{"voice": "mystery", "label": "Mystery", "languages": ["en"], "gender": "unknown"}],
    })
    monkeypatch.setattr(model_service, "catalog_for_adapter", lambda adapter_id: catalog)

    listed = await model_service.list_voice_profiles(query="mystery")
    assert [voice["gender"] for voice in listed["voices"]] == ["unknown"]
    for gender in ("male", "female", "neutral"):
        assert (await model_service.list_voice_profiles(query="mystery", gender=gender))["voices"] == []


@pytest.mark.asyncio
async def test_cantonese_is_not_returned_for_mandarin(_full_registry):
    from cfgpu_mcp.service import model as model_service

    everything, cursor = [], 0
    while cursor is not None:
        page = await model_service.list_voice_profiles(language="中文", limit=100, cursor=cursor)
        everything += page["voices"]
        cursor = page["next_cursor"]
    assert len(everything) == page["total"]
    assert all("yue" not in voice["languages"] for voice in everything)

    cantonese = await model_service.list_voice_profiles(language="粤语", limit=100)
    assert "Cantonese_GentleLady" in {voice["voice"] for voice in cantonese["voices"]}


@pytest.mark.asyncio
async def test_seed_tts_default_is_marked(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_voice_profiles(query="zh_female_xiaohe_uranus_bigtts")
    assert catalog["voices"][0]["default"] is True


@pytest.mark.asyncio
async def test_bad_language_and_contradictory_gender_are_rejected(_full_registry):
    from cfgpu_mcp.service import model as model_service

    with pytest.raises(ValueError, match="unknown language"):
        await model_service.list_voice_profiles(language="克林贡语")
    with pytest.raises(ValueError, match="contradicts"):
        await model_service.list_voice_profiles(query="女声", gender="male")
    with pytest.raises(ValueError, match="gender must be one of"):
        await model_service.list_voice_profiles(gender="unknown")


def test_tts_validation_accepts_the_listed_voices_plus_the_adapter_default():
    """list_voice_profiles and the adapter's local voice check read one catalog; the
    default is added because the adapter sends it whenever voice is omitted."""
    from cfgpu_mcp.adapters.audio_tts import (
        _MINIMAX_DEFAULT_VOICE,
        _MINIMAX_SYSTEM_VOICES,
        _SEED_DEFAULT_VOICE,
        _SEED_SYSTEM_VOICES,
    )
    from cfgpu_mcp.voice_catalog import catalog_for_adapter

    for adapter_id, accepted, default in (
        ("seed-tts-2-0", _SEED_SYSTEM_VOICES, _SEED_DEFAULT_VOICE),
        ("minimax-speech-2-8-hd", _MINIMAX_SYSTEM_VOICES, _MINIMAX_DEFAULT_VOICE),
        ("minimax-speech-2-8-turbo", _MINIMAX_SYSTEM_VOICES, _MINIMAX_DEFAULT_VOICE),
    ):
        assert {v.voice for v in catalog_for_adapter(adapter_id).voices} | {default} == accepted


@pytest.mark.parametrize("adapter_id", ["seed-tts-2-0", "minimax-speech-2-8-hd"])
def test_cards_point_to_the_catalog_instead_of_listing_voices(adapter_id):
    """A voice table in the card would be a second, drifting copy of voices.yaml."""
    card = (MODELS_DIR / adapter_id / "card.md").read_text(encoding="utf-8")
    section = card.split("## 系统音色列表", 1)[1].split("\n## ", 1)[0]
    assert "list_voice_profiles" in section
    assert "|" not in section


@pytest.mark.asyncio
async def test_profile_catalog_says_how_each_model_uses_audio(_full_registry):
    """Speech-to-video selection needs the audio role, which the task list alone hides."""
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_model_profiles(media_type="video")
    by_id = {model["model_id"]: model for model in catalog["models"]}
    assert by_id["wan2.6-i2v"]["inputs"]["reference_audios"] == {
        "max": 1, "role": "driving", "standalone": False,
    }
    assert by_id["wan2.7-r2v"]["inputs"]["reference_audios"]["role"] == "voice"
    assert by_id["doubao-seedance-2-0"]["inputs"]["reference_audios"]["role"] == "reference"
    assert "reference_audios" not in by_id["kling-video-o1"]["inputs"]
    assert catalog["task_catalog"]["audio_driven_video"]["requires"] == {
        "all": ["reference_audios"], "audio_role": "driving",
    }

    images = await model_service.list_model_profiles(media_type="image")
    image_inputs = {model["model_id"]: model["inputs"] for model in images["models"]}
    assert image_inputs["wan2.7-image"] == {"reference_images": {"max": 9}, "regions": {"max_per_image": 2}}
    assert image_inputs["doubao-seedream-5-0-pro"]["reference_images"] == {"max": 10}
    assert "reference_audios" not in image_inputs["cf-image-2"]


@pytest.mark.asyncio
async def test_audio_driven_search_returns_only_driving_models(_full_registry):
    from cfgpu_mcp.service import model as model_service

    catalog = await model_service.list_model_profiles(required_tasks=["audio_driven_video"])
    assert catalog["models"]
    for model in catalog["models"]:
        assert model["inputs"]["reference_audios"]["role"] == "driving"


_RETIRED = re.compile(
    r"multi_modal_reference|audio_generate|\*\*web_search\*\*|region_understand\b|能力标签"
    r"|web_grounded_generation|tool_calling|visual_agent"
    r"|image_reasoning|long_context|long_video_understanding|long_document_understanding"
)


@pytest.mark.parametrize("card", sorted(MODELS_DIR.glob("*/card.md")), ids=lambda p: p.parent.name)
def test_cards_speak_the_canonical_vocabulary(card):
    """A card is read by people and agents; a second vocabulary there is the drift that
    the retired `capabilities` list was. `tools.type: web_search` is an upstream wire
    value and stays."""
    text = card.read_text(encoding="utf-8")
    assert not _RETIRED.search(text), _RETIRED.search(text).group(0)
    row = re.search(r"^\| 任务（tasks） \| ([^|]*) \|$", text, re.M)
    if row:
        declared = _registry().get(card.parent.name).tasks
        assert [t.strip() for t in row.group(1).split(",")] == list(declared)
