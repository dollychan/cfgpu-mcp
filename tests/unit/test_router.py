import pytest
from pathlib import Path
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.router import ModelRouter, selection_key
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
    # wan-2-0-fast, grok-imagine-video and doubao-seedance-2-0-fast all score 6
    # (speed_tier=4, cost_tier=2); doubao-seedance-2-0-fast's auto_priority breaks
    # the tie, where it used to be decided by alphabetical adapter_id.
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


def test_best_tier_prefers_declared_quality_rank_over_price():
    """"best" follows quality_rank, not the cost_tier proxy it replaced.

    nano-banana-2 / -pro-official / -pro-premium all carry cost_tier 4 and used to
    win this tier on price alone; the flagship is gpt-image-2 at cost_tier 2.
    """
    router = _router()
    best = router.select_model(GenerateImageInput(prompt="a cat", quality_tier="best"))
    balanced = router.select_model(GenerateImageInput(prompt="a cat", quality_tier="balanced"))
    assert best.adapter_id == "gpt-image-2"
    assert balanced.adapter_id != "gpt-image-2"


def test_best_tier_runner_up_is_banana_pro():
    """With the flagship filtered out by supports(), "best" falls to CF Banana Pro.

    Pinned because the two pricier members of the same chain (official / premium)
    would outscore it on the surviving cost_tier proxy if their inherited
    quality_rank were not zeroed out.
    """
    router = _router()
    req = GenerateImageInput(prompt="a cat", quality_tier="best")
    candidates = [
        a for a in router._registry.list_all(task_type="image")
        if a.adapter_id != "gpt-image-2" and a.supports(req)[0]
    ]
    ranked = sorted(candidates, key=lambda a: selection_key(router._score(a, req), a))
    assert ranked[0].adapter_id == "nano-banana-pro"


def test_auto_image_default_is_seedream_5_0_pro():
    """The default pick is declared, not alphabetical.

    Every Seedream shares speed_tier 3 / cost_tier 2, so before auto_priority the
    family tied and the adapter_id tie-break handed every auto image request to the
    oldest member (4.0).
    """
    router = _router()
    for tier in ("balanced", "fast"):
        for prompt in ("a red panda", "一只红熊猫"):
            adapter = router.select_model(
                GenerateImageInput(prompt=prompt, quality_tier=tier)
            )
            assert adapter.adapter_id == "doubao-seedream-5-0-pro", (tier, prompt)


def test_auto_image_falls_back_to_5_0_lite_when_pro_unsupported():
    # 4K exceeds Pro's pixel ceiling, so supports() drops it. The fallback must be
    # the newest lite, not whichever name sorts first.
    router = _router()
    adapter = router.select_model(GenerateImageInput(prompt="a cat", resolution="4K"))
    assert adapter.adapter_id == "doubao-seedream-5-0-lite"


def test_group_request_avoids_the_model_that_ignores_n():
    # 5.0 Pro is the default pick but has no multi_image_group; n>1 there silently
    # returns a single image, so the group bonus must route around it.
    router = _router()
    adapter = router.select_model(GenerateImageInput(prompt="a cat", n=4))
    assert "multi_image_group" in adapter.capabilities


def test_auto_video_default_is_seedance_2_0_fast():
    # balanced and fast both land on the declared default; without auto_priority
    # the tie with 2.0 mini / grok / wan-2-0-fast fell to alphabetical adapter_id.
    router = _router()
    for tier in ("balanced", "fast"):
        adapter = router.select_model(GenerateVideoInput(prompt="waves", quality_tier=tier))
        assert adapter.adapter_id == "doubao-seedance-2-0-fast", tier


def test_video_best_tier_prefers_declared_flagship_over_price():
    # The cost_tier proxy ranked kling-v3-omni first on price alone (cost 5).
    # Seedance 2.5 declares the rank and wins at cost 4.
    router = _router()
    adapter = router.select_model(GenerateVideoInput(prompt="waves", quality_tier="best"))
    assert adapter.adapter_id == "doubao-seedance-2-5"


def test_cost_proxy_still_applies_where_no_rank_is_declared():
    """The proxy is retained, not replaced: it orders every unranked model.

    With the two ranked models removed, "best" must still fall to the priciest
    remaining candidate rather than to whatever sorts first.
    """
    router = _router()
    req = GenerateVideoInput(prompt="waves", quality_tier="best")
    candidates = [
        a for a in router._registry.list_all(task_type="video")
        if a.quality_rank == 0 and a.supports(req)[0]
    ]
    ranked = sorted(candidates, key=lambda a: selection_key(router._score(a, req), a))
    assert ranked[0].adapter_id == "kling-v3-omni"


def test_declared_ranks_are_not_inherited_down_the_extends_chain():
    """Both new fields ride ``extends`` like every other YAML field.

    gpt-image-2 (rank 3) is the parent of the whole nano-banana chain and
    doubao-seedream-5-0-lite (priority 1) the parent of 4.0 / 4.5, so every variant
    that must not inherit has to zero the field out in its own YAML. Pinned because
    a silent inheritance would promote a model nobody nominated.
    """
    router = _router()
    get = router._registry.get
    assert get("gpt-image-2").quality_rank == 3
    assert get("nano-banana-pro").quality_rank == 2
    for adapter_id in ("nano-banana-2", "nano-banana-pro-official", "nano-banana-pro-premium"):
        assert get(adapter_id).quality_rank == 0, adapter_id
    assert get("doubao-seedance-2-5").quality_rank == 3
    assert get("doubao-seedance-2-0-fast").auto_priority == 2
    # Both are leaves of the wan-2-0 chain, so nothing inherits from them; their
    # siblings must stay undeclared.
    for adapter_id in ("doubao-seedance-2-0", "doubao-seedance-2-0-mini", "wan-2-0-fast"):
        assert get(adapter_id).quality_rank == 0, adapter_id
        assert get(adapter_id).auto_priority == 0, adapter_id
    assert get("doubao-seedream-5-0-pro").auto_priority == 2
    assert get("doubao-seedream-5-0-lite").auto_priority == 1
    for adapter_id in ("doubao-seedream-4-0", "doubao-seedream-4-5"):
        assert get(adapter_id).auto_priority == 0, adapter_id


def test_auto_priority_never_outweighs_a_scoring_difference():
    """It is a tie-break, not a bonus.

    doubao-seedance-2-0-fast carries auto_priority 2; in the "best" tier its proxy
    score (cost 2 + speed 4 = 6) sits one below kling-v3-omni's (cost 5 + speed 2 =
    7). Folded into the score it would win, handing a "best" request to the model
    chosen for being fast. Checked with the flagship removed, since 2.5 outranks
    both.
    """
    router = _router()
    req = GenerateVideoInput(prompt="waves", quality_tier="best")
    fast = router._registry.get("doubao-seedance-2-0-fast")
    kling = router._registry.get("kling-v3-omni")
    assert fast.auto_priority > kling.auto_priority
    assert router._score(fast, req) < router._score(kling, req)
    assert selection_key(router._score(kling, req), kling) < selection_key(
        router._score(fast, req), fast
    )


def test_undeclared_model_scores_exactly_as_before():
    # Both fields default to 0, so a model that declares neither is unaffected.
    router = _router()
    adapter = router._registry.get("wan-2-7-image")
    assert adapter.auto_priority == 0
    assert adapter.quality_rank == 0


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


# ── ③ Unknown-model fallback (understand only) ───────────────────────────────


def test_understand_unknown_model_falls_back_to_auto():
    """A hallucinated / unknown model_id on understand_vision must not hard-fail:
    vision-understanding is a small, synchronous, cheap-to-rerun set, so resolve()
    falls back to auto-selection instead of raising."""
    router = _router()
    req = UnderstandVisionInput(prompt="describe this", model="qwen-3-vl-plus")
    adapter = router.resolve(req)
    assert adapter.task_type == "understand"


def test_generate_unknown_model_still_raises():
    """generate_* keeps the hard error for an unknown model_id — a wrong media model
    would waste an async, billed generation job and must surface loudly."""
    from cfgpu_mcp.errors import CFGPUError

    router = _router()
    req = GenerateImageInput(prompt="a cat", model="doubao-fake-999")
    with pytest.raises(CFGPUError) as exc_info:
        router.resolve(req)
    assert exc_info.value.error_type == "invalid_params"


def test_understand_known_but_unsupported_model_still_raises():
    """The fallback covers only *unknown* ids. A model that exists but does not
    support the understand task (capability mismatch) stays a hard error."""
    from cfgpu_mcp.errors import CFGPUError

    router = _router()
    # A real image model is a valid id but cannot serve an understand request.
    req = UnderstandVisionInput(prompt="x", model="doubao-seedream-5-0-lite")
    with pytest.raises(CFGPUError) as exc_info:
        router.resolve(req)
    assert exc_info.value.error_type == "invalid_params"
