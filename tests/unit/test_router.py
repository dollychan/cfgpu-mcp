import pytest
from pathlib import Path
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _router() -> ModelRouter:
    import cfgpu_mcp.adapters
    registry = AdapterRegistry(model_dir=MODELS_DIR)
    registry.load()
    return ModelRouter(registry)


def test_auto_fast_tier_selects_high_speed_adapter():
    router = _router()
    req = GenerateVideoInput(prompt="test", quality_tier="fast")
    adapter = router.select_model(req)
    # wan-2-0-fast has speed_tier=4, wan-2-0 has speed_tier=2
    assert adapter.adapter_id == "wan-2-0-fast"


def test_auto_balanced_returns_a_video_model():
    router = _router()
    req = GenerateVideoInput(prompt="test", quality_tier="balanced")
    adapter = router.select_model(req)
    assert adapter.task_type == "video"


def test_chinese_prompt_prefers_seedream_for_image():
    router = _router()
    req = GenerateImageInput(prompt="一只可爱的猫咪", quality_tier="balanced")
    adapter = router.select_model(req)
    assert adapter.adapter_id.startswith("doubao-seedream")


def test_reference_videos_score_multi_modal_capable_adapter():
    router = _router()
    req = GenerateVideoInput(
        prompt="test",
        reference_videos=["https://example.com/v.mp4"],
        quality_tier="balanced",
    )
    adapter = router.select_model(req)
    assert "multi_modal_reference" in adapter.capabilities


def test_explicit_model_bypasses_scoring():
    router = _router()
    req = GenerateVideoInput(prompt="test", model="wan-2-0")
    adapter = router.get_adapter("wan-2-0")
    assert adapter.adapter_id == "wan-2-0"


def test_explicit_nonexistent_model_raises_cfgpu_error():
    from cfgpu_mcp.errors import CFGPUError
    router = _router()
    with pytest.raises(CFGPUError) as exc_info:
        router.get_adapter("nonexistent-model")
    assert exc_info.value.error_type == "invalid_params"


def test_no_candidates_raises_cfgpu_error():
    from cfgpu_mcp.errors import CFGPUError
    # Registry with only image model, requesting video
    import cfgpu_mcp.adapters
    registry = AdapterRegistry(
        model_dir=MODELS_DIR,
        enabled_models=["doubao-seedream-5-0-lite"],
    )
    registry.load()
    router = ModelRouter(registry)
    req = GenerateVideoInput(prompt="test")
    with pytest.raises(CFGPUError) as exc_info:
        router.select_model(req)
    assert exc_info.value.error_type == "model_unavailable"
