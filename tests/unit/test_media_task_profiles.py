"""Keep agent-facing task profiles complete, canonical, and free of adapter aliases."""

from pathlib import Path
from typing import get_args

import pytest
import yaml


ROOT = Path(__file__).parent.parent.parent
MODELS_DIR = ROOT / "src" / "cfgpu_mcp" / "models"
TASKS_PATH = ROOT / "src" / "cfgpu_mcp" / "capabilities" / "media_tasks.yaml"
PROFILE_KEYS = {"schema_version", "tasks"}


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
        assert task["media_type"] in {"video", "image", "audio", "understand", "cross_media"}
        assert all(isinstance(task[field], str) and task[field] for field in ("name", "description", "prompt_guidance"))


def test_profile_filter_enum_matches_the_canonical_task_dictionary():
    from cfgpu_mcp.tool_registry import CanonicalTaskId

    assert set(get_args(CanonicalTaskId)) == set(_load_yaml(TASKS_PATH)["tasks"])


def test_every_adapter_has_a_canonical_task_profile():
    adapters = {path.parent for path in MODELS_DIR.glob("*/adapter.yaml")}
    profiles = {path.parent for path in MODELS_DIR.glob("*/profile.yaml")}
    assert profiles == adapters


def test_profiles_reference_only_known_canonical_task_ids():
    tasks = _load_yaml(TASKS_PATH)["tasks"]
    for profile_path in MODELS_DIR.glob("*/profile.yaml"):
        profile = _load_yaml(profile_path)
        assert set(profile) == PROFILE_KEYS, profile_path
        assert profile["schema_version"] == 1, profile_path
        assert isinstance(profile["tasks"], list) and profile["tasks"], profile_path
        assert len(profile["tasks"]) == len(set(profile["tasks"])), profile_path
        assert set(profile["tasks"]).issubset(tasks), profile_path

        media_types = {
            tasks[task_id]["media_type"]
            for task_id in profile["tasks"]
            if tasks[task_id]["media_type"] != "cross_media"
        }
        assert len(media_types) == 1, profile_path


def test_seedance_reference_and_edit_are_distinct_agent_tasks():
    profile = _load_yaml(MODELS_DIR / "doubao-seedance-2-5" / "profile.yaml")
    assert {"reference_to_video", "video_edit", "video_extend"}.issubset(profile["tasks"])
    assert "multi_modal_reference" not in profile["tasks"]


@pytest.mark.parametrize("adapter_id", ["cfgpu-minimax-h3", "cfdream-minimax-h3-r2v"])
def test_minimax_h3_reference_video_supports_video_edit(adapter_id: str):
    profile = _load_yaml(MODELS_DIR / adapter_id / "profile.yaml")
    assert {"reference_to_video", "video_edit"}.issubset(profile["tasks"])


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
    assert catalog["catalog_version"] == 1
    assert set(catalog) == {"catalog_version", "task_catalog", "models"}
    assert set(catalog["task_catalog"]) == {"video_edit"}
    assert all("video_edit" in model["tasks"] for model in catalog["models"])
    assert catalog["models"] == sorted(catalog["models"], key=lambda model: model["model_id"])

    for model in catalog["models"]:
        assert set(model) == {
            "model_id",
            "display_name",
            "task_type",
            "tasks",
            "cost_tier",
            "speed_tier",
        }
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


@pytest.mark.asyncio
async def test_voice_catalog_is_paginated_and_has_only_agent_facing_selection_data(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_voice_profiles(language="中文", limit=2)

    assert set(catalog) == {"catalog_version", "voices", "total", "next_cursor"}
    assert catalog["catalog_version"] == 1
    assert len(catalog["voices"]) == 2
    assert catalog["total"] > len(catalog["voices"])
    assert catalog["next_cursor"] == 2
    assert {
        model["model_id"]
        for voice in catalog["voices"]
        for model in voice["models"]
    } == {
        "MiniMax/speech-2.8-hd",
        "MiniMax/speech-2.8-turbo",
        "seed-tts-2.0",
    }
    for voice in catalog["voices"]:
        assert set(voice) == {"voice_id", "display_name", "language", "tags", "models"}
        assert voice["voice_id"]
        assert "中文" in voice["language"]
        assert voice["models"]
        for model in voice["models"]:
            assert set(model) == {"model_id", "display_name", "cost_tier", "speed_tier"}
            assert "adapter_id" not in model
            assert "is_async" not in model


@pytest.mark.asyncio
async def test_voice_catalog_aggregates_shared_voices_and_filters_by_keyword(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_voice_profiles(query="青涩青年", limit=10)
    voice = next(item for item in catalog["voices"] if item["voice_id"] == "male-qn-qingse")

    assert voice["display_name"] == "青涩青年音色"
    assert {model["model_id"] for model in voice["models"]} == {
        "MiniMax/speech-2.8-hd",
        "MiniMax/speech-2.8-turbo",
    }


@pytest.mark.asyncio
async def test_voice_catalog_preserves_byte_exact_handles(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    catalog = await model_service.list_voice_profiles(query="ProfessionalHost", limit=10)
    assert "Cantonese_ProfessionalHost（F)" in {voice["voice_id"] for voice in catalog["voices"]}


@pytest.mark.asyncio
async def test_voice_catalog_rejects_unknown_model_and_invalid_paging(monkeypatch):
    from cfgpu_mcp.adapters.registry import AdapterRegistry
    from cfgpu_mcp.service import model as model_service

    registry = AdapterRegistry(MODELS_DIR)
    registry.load()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)

    with pytest.raises(ValueError, match="unknown audio model_ids"):
        await model_service.list_voice_profiles(model_ids=["invented-voice-model"])
    with pytest.raises(ValueError, match="limit must be between"):
        await model_service.list_voice_profiles(limit=0)
