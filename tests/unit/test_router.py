import pytest
from pathlib import Path
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.tool_registry import (
    GenerateImageInput,
    GenerateVideoInput,
    UnderstandVisionInput,
)

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
    # wan-2-0-fast and doubao-seedance-2-0-fast both score 6 (speed_tier=4,
    # cost_tier=2); the deterministic adapter_id tie-break picks the latter.
    assert adapter.adapter_id == "doubao-seedance-2-0-fast"


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


def test_best_tier_prefers_higher_cost_tier():
    router = _router()
    # "best" uses cost_tier as a quality proxy: among image models nano-banana-2
    # has the highest cost_tier (4), so it should win — whereas "balanced"
    # (speed - cost) would penalize it. Use an English prompt to avoid the
    # Chinese→Seedream bias.
    best = router.select_model(GenerateImageInput(prompt="a cat", quality_tier="best"))
    balanced = router.select_model(GenerateImageInput(prompt="a cat", quality_tier="balanced"))
    assert best.adapter_id == "nano-banana-2"
    assert balanced.adapter_id != "nano-banana-2"


def test_image_reference_bonus_uses_real_capability():
    router = _router()
    # Seedream declares multi_image_fusion/multi_image_group; with reference_images
    # it should win the +3 reference bonus over models lacking those capabilities.
    req = GenerateImageInput(
        prompt="cat",
        reference_images=["https://example.com/a.png", "https://example.com/b.png"],
        quality_tier="balanced",
    )
    adapter = router.select_model(req)
    caps = adapter.capabilities
    assert "multi_image_fusion" in caps or "multi_image_group" in caps


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


def test_model_list_restricts_candidates_and_picks_best():
    router = _router()
    # Restrict to the two WAN variants; fast tier should pick the fast one.
    req = GenerateVideoInput(
        prompt="test", model=["wan-2-0", "wan-2-0-fast"], quality_tier="fast"
    )
    adapter = router.resolve(req)
    assert adapter.adapter_id == "wan-2-0-fast"


def test_model_list_excludes_unlisted_models():
    router = _router()
    # Only the slow variant is allowed, so even fast tier must return it.
    req = GenerateVideoInput(
        prompt="test", model=["wan-2-0"], quality_tier="fast"
    )
    adapter = router.resolve(req)
    assert adapter.adapter_id == "wan-2-0"


def test_model_list_with_unknown_id_raises_cfgpu_error():
    from cfgpu_mcp.errors import CFGPUError
    router = _router()
    req = GenerateVideoInput(prompt="test", model=["wan-2-0", "nope-model"])
    with pytest.raises(CFGPUError) as exc_info:
        router.resolve(req)
    assert exc_info.value.error_type == "invalid_params"


def test_resolve_single_model_bypasses_scoring():
    router = _router()
    req = GenerateVideoInput(prompt="test", model="wan-2-0")
    adapter = router.resolve(req)
    assert adapter.adapter_id == "wan-2-0"


def test_resolve_explicit_wrong_task_type_raises_cfgpu_error():
    from cfgpu_mcp.errors import CFGPUError
    # Naming a video model on an image request must surface the friendly
    # supports() reason (with a model_id model-card hint), not a raw
    # AssertionError leaking from build_payload().
    router = _router()
    req = GenerateImageInput(prompt="test", model="wan-2-0")
    with pytest.raises(CFGPUError) as exc_info:
        router.resolve(req)
    assert exc_info.value.error_type == "invalid_params"
    assert exc_info.value.model_id == "wan-video"


def test_resolve_auto_selects_from_all():
    router = _router()
    req = GenerateVideoInput(prompt="test", model="auto", quality_tier="fast")
    adapter = router.resolve(req)
    assert adapter.adapter_id == "doubao-seedance-2-0-fast"


def test_understand_request_selects_understand_model():
    router = _router()
    req = UnderstandVisionInput(prompt="描述这张图片", images=["https://x/a.jpg"])
    adapter = router.select_model(req)
    assert adapter.task_type == "understand"
    assert adapter.adapter_id == "qwen-3-6-plus"


def test_understand_request_never_selects_media_model():
    router = _router()
    # A video model must never be returned for an understand request.
    req = UnderstandVisionInput(prompt="x")
    adapter = router.resolve(req)
    assert adapter.task_type == "understand"


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
