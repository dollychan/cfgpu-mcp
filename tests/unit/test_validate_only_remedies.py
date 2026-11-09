"""The remedies a refused request carries must be ones the caller can follow.

The caller is usually an LLM that treats every sentence of an error as an instruction
and repeats the call until it passes. A remedy that is vague, points at something that
does not exist, or contradicts the caller's own tool description does not just fail
once — it stays in the context and steers every retry. These tests pin the four ways
the preflight used to do that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cfgpu_mcp.adapters.prompt_render import PROMPT_NOTE
from cfgpu_mcp.adapters.regions import render_prompt
from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.task_manager import validate_request
from cfgpu_mcp.tool_registry import (
    GenerateImageInput,
    GenerateVideoInput,
    frame_placeholder_message,
)

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"
_GENERIC_HINT = "重新选择支持该任务的模型"


def _registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401  (registers adapter classes)

    registry = AdapterRegistry(model_dir=MODELS_DIR)
    registry.load()
    return registry


def _video(**kwargs) -> GenerateVideoInput:
    return GenerateVideoInput(prompt="一只猫在雨里散步", **kwargs)


# ── 1. auto routing that finds no model ──────────────────────────────────────


def _no_fit_request() -> GenerateVideoInput:
    return _video(
        first_frame="https://e/a.png",
        last_frame="https://e/b.png",
        reference_videos=["https://e/v.mp4"],
        reference_audios=["https://e/a.mp3"],
    )


def test_auto_with_no_fitting_model_is_a_parameter_error_not_an_outage():
    """``model_unavailable`` is retryable, so a host treating retryable errors as
    environmental lets the call through to approval — where it fails again, billed
    attention for a request that could never run."""
    with pytest.raises(CFGPUError) as exc:
        ModelRouter(_registry()).select_model(_no_fit_request(), for_validation=True)
    assert exc.value.error_type == "invalid_params"
    assert exc.value.retryable is False


def test_auto_with_no_fitting_model_says_what_to_change():
    """"请检查参数" names no parameter; the preferred candidate's own reason does."""
    with pytest.raises(CFGPUError) as exc:
        ModelRouter(_registry()).select_model(_no_fit_request(), for_validation=True)
    message = exc.value.to_tool_result_dict()["message"]
    assert "first_frame" in message
    assert "请检查参数或手动指定 model" not in message
    assert _GENERIC_HINT not in message


def test_auto_with_no_fitting_model_does_not_dump_every_candidate():
    """Twenty candidates' reasons is kilobytes of mostly contradictory advice."""
    with pytest.raises(CFGPUError) as exc:
        ModelRouter(_registry()).select_model(_no_fit_request(), for_validation=True)
    message = exc.value.user_message
    assert len(message) < 1500
    assert message.count("does not support") <= 1


def test_no_candidates_at_all_is_still_model_unavailable():
    """Nothing enabled for the task type really is an environment fact."""
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(model_dir=MODELS_DIR, enabled_models=["doubao-seedream-5-0-lite"])
    registry.load()
    with pytest.raises(CFGPUError) as exc:
        ModelRouter(registry).select_model(_video())
    assert exc.value.error_type == "model_unavailable"


# ── 2. remedies describe the handle instead of telling the caller to write one ─


def test_prompt_note_describes_the_handle_without_telling_the_caller_to_fill_refs():
    """A host that fills ``*_refs`` itself tells its model to leave them out; a note
    saying "put the handle in *_refs" then contradicts it inside one message."""
    assert "句柄放在" not in PROMPT_NOTE
    assert "[[<句柄>]]" in PROMPT_NOTE


def test_frame_placeholder_message_does_not_ask_for_a_new_handle():
    message = frame_placeholder_message("m_a1", "first_frame")
    assert "with a handle in image_refs" not in message
    assert "[[<handle>]]" in message


def test_frames_vs_references_route_never_asks_to_invent_a_name():
    """"在 image_refs 里给它起名 … [[名字]]" teaches the caller to make up a handle."""
    registry = _registry()
    adapter = registry.get("doubao-seedance-2-5")
    ok, reason = adapter.supports(_video(first_frame="https://e/a.png", reference_videos=["https://e/v.mp4"]))
    assert not ok
    assert "起名" not in reason and "[[名字]]" not in reason
    assert "句柄" in reason


def test_unnamed_reference_reason_does_not_tell_the_caller_to_fill_refs():
    adapter = _registry().get("cfdream/minimax-h3-r2v")
    ok, reason = adapter.supports(_video(reference_videos=["https://e/v.mp4"]))
    assert not ok
    assert "句柄放在" not in reason
    assert "[[<句柄>]]" in reason


def test_region_hint_with_nothing_passed_does_not_ask_for_image_refs():
    with pytest.raises(CFGPUError) as exc:
        render_prompt("[[m_a1]] 里的 [[标记1]]", None, None, include_names=False)
    message = exc.value.user_message
    assert "给出句柄" not in message and "没有 image_refs" not in message
    assert "图片参数" in message and "只写在 prompt 里不算传入" in message


# ── 3. a concrete supports() reason carries no generic "switch models" tail ─


def test_explicit_model_rejection_has_no_generic_switch_model_tail():
    router = ModelRouter(_registry())
    req = _video(model="happyhorse-1.0-i2v", first_frame="https://e/a.png", last_frame="https://e/b.png")
    with pytest.raises(CFGPUError) as exc:
        router.resolve(req)
    result = exc.value.to_tool_result_dict()
    assert "last_frame" in result["message"]
    assert _GENERIC_HINT not in result["message"]
    assert result["model_id"] == "happyhorse-1.0-i2v"


def test_validate_only_rejection_has_no_generic_switch_model_tail():
    registry = _registry()
    adapter = registry.get("happyhorse-1.0-i2v")
    req = _video(model="happyhorse-1.0-i2v", first_frame="https://e/a.png", last_frame="https://e/b.png")
    with pytest.raises(CFGPUError) as exc:
        validate_request(adapter, req)
    assert _GENERIC_HINT not in exc.value.to_tool_result_dict()["message"]


# ── 4. an unknown explicit model lists only models of the same task type ─────


def test_unknown_model_lists_only_models_of_the_requested_task_type():
    router = ModelRouter(_registry())
    with pytest.raises(CFGPUError) as exc:
        router.resolve(GenerateImageInput(prompt="猫", model="nope-model"))
    message = exc.value.user_message
    assert exc.value.error_type == "invalid_params" and exc.value.retryable is False
    assert "nope-model" in message
    assert "doubao-seedream-5-0-lite" in message
    assert "wan-video" not in message, "a video model is no remedy for an image request"
    assert "Model '" not in message and "\"" not in message, "no KeyError repr quoting"


# ── 5/6. a suggested switch names an enabled model by its public name ────────


def _registry_without(*disabled: str) -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(model_dir=MODELS_DIR, disabled_models=list(disabled))
    registry.load()
    return registry


def test_wan_fast_1080p_refusal_names_the_public_model_name(monkeypatch):
    """``wan-2-0`` is an adapter_id; passed as ``model=`` it is not what callers see
    in ``list_models``, so the advice reads as a model that does not exist."""
    registry = _registry()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)
    ok, reason = registry.get("wan-video-fast").supports(_video(resolution="1080p"))
    assert not ok
    assert "model=wan-video" in reason
    assert "wan-2-0" not in reason


def test_wan_fast_1080p_refusal_names_no_disabled_model(monkeypatch):
    registry = _registry_without("wan-video")
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)
    ok, reason = registry.get("wan-video-fast").supports(_video(resolution="1080p"))
    assert not ok
    assert "wan-video " not in reason + " " and "model=wan-video" not in reason
    assert "480p" in reason


def test_happyhorse_route_names_no_disabled_sibling(monkeypatch):
    registry = _registry_without("happyhorse-1.0-r2v")
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)
    req = _video(first_frame="https://e/a.png", reference_images=["https://e/r.png"])
    ok, reason = registry.get("happyhorse-1.0-t2v").supports(req)
    assert not ok
    assert "happyhorse-1.0-r2v" not in reason
    assert "reference_to_video" in reason, "falls back to naming the task"


def test_happyhorse_route_still_names_an_enabled_sibling(monkeypatch):
    registry = _registry()
    monkeypatch.setattr("cfgpu_mcp.config.get_registry", lambda: registry)
    req = _video(first_frame="https://e/a.png", reference_images=["https://e/r.png"])
    ok, reason = registry.get("happyhorse-1.0-t2v").supports(req)
    assert not ok
    assert "model=happyhorse-1.0-r2v" in reason and "model=happyhorse-1.0-i2v" in reason


# ── 7. a box refusal neither echoes the numbers nor asks for arithmetic ──────


@pytest.mark.parametrize("box", [[10, 20, 300, 400], [0.3, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3]])
def test_box_refusal_does_not_echo_coordinates_or_ask_to_compute(box):
    """Coordinates are a host's to produce; a refusal that repeats them and says
    "divide by the width" invites the caller to start doing pixel arithmetic."""
    from pydantic import ValidationError

    from cfgpu_mcp.tool_registry import RegionSpec

    with pytest.raises(ValidationError) as exc:
        RegionSpec(image_index=0, box=box)
    message = str(exc.value).split("[type=")[0]
    assert "Divide" not in message
    assert str(box) not in message and repr(box[0]) + "," not in message
