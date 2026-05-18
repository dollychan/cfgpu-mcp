import pytest
from pathlib import Path
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.wan_video import WanVideoAdapter
from cfgpu_mcp.adapters.seedream import SeedreamAdapter

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _load(enabled_models=None) -> AdapterRegistry:
    import cfgpu_mcp.adapters  # trigger registration
    registry = AdapterRegistry(model_dir=MODELS_DIR, enabled_models=enabled_models)
    registry.load()
    return registry


def test_all_models_registered():
    registry = _load()
    assert len(registry) == 10


def test_lookup_by_adapter_id():
    registry = _load()
    adapter = registry.get("wan-2-0")
    assert adapter.adapter_id == "wan-2-0"


def test_lookup_by_cfgpu_model_id():
    registry = _load()
    adapter = registry.get("wan-video")
    assert adapter.adapter_id == "wan-2-0"


def test_lookup_by_display_name():
    registry = _load()
    adapter = registry.get("WAN 2.0 (Seedance 2.0)")
    assert adapter.adapter_id == "wan-2-0"


def test_enabled_models_allowlist_by_adapter_id():
    registry = _load(enabled_models=["wan-2-0", "wan-2-0-fast"])
    assert len(registry) == 2
    with pytest.raises(KeyError):
        registry.get("doubao-seedream-5-0-lite")


def test_enabled_models_allowlist_by_cfgpu_model_id():
    registry = _load(enabled_models=["doubao-seedream-5-0-260128"])
    assert len(registry) == 1
    adapter = registry.get("doubao-seedream-5-0-lite")
    assert adapter.adapter_id == "doubao-seedream-5-0-lite"


def test_enabled_models_none_registers_all():
    registry = _load(enabled_models=None)
    assert len(registry) == 10


def test_wan_fast_uses_wan_video_adapter_class():
    registry = _load()
    adapter = registry.get("wan-2-0-fast")
    assert isinstance(adapter, WanVideoAdapter)


def test_wan_fast_cfgpu_model_id_is_fast():
    registry = _load()
    adapter = registry.get("wan-2-0-fast")
    assert adapter.cfgpu_model_id == "wan-video-fast"


def test_wan_fast_cost_tier_overrides_parent():
    registry = _load()
    wan = registry.get("wan-2-0")
    fast = registry.get("wan-2-0-fast")
    assert fast.cost_tier < wan.cost_tier


def test_wan_fast_poll_config_merged():
    registry = _load()
    wan = registry.get("wan-2-0")
    fast = registry.get("wan-2-0-fast")
    # default_timeout overridden in child
    assert fast.poll_config.default_timeout < wan.poll_config.default_timeout
    # base_interval inherited from parent
    assert fast.poll_config.base_interval == wan.poll_config.base_interval


def test_python_adapter_preferred_over_generic():
    registry = _load()
    seedream = registry.get("doubao-seedream-5-0-lite")
    assert isinstance(seedream, SeedreamAdapter)


def test_unknown_model_raises_key_error():
    registry = _load()
    with pytest.raises(KeyError, match="not found"):
        registry.get("nonexistent-model")


def test_list_all_filters_by_task_type():
    registry = _load()
    video_models = registry.list_all(task_type="video")
    image_models = registry.list_all(task_type="image")
    assert all(a.task_type == "video" for a in video_models)
    assert all(a.task_type == "image" for a in image_models)
    assert len(video_models) + len(image_models) == len(registry)
