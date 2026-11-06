"""A frame the prompt names as ``[[...]]`` beside other references is moved into reference_images.

Production, 2026-10-27: MiniMax-H3 with ``first_frame`` + two portraits + two voice
samples, the prompt saying "首帧参考 [[m_14ad8213]]". A frame has no handle list, so the
placeholder could never resolve, and H3 refuses frames beside references anyway — the
request failed on every model. The caller was already on the reference route; only the
slot was wrong. The fold is that one repair, reported in ``corrected_args``.
"""
from __future__ import annotations

import pytest

from cfgpu_mcp.tool_registry import GenerateVideoInput

FRAME, KAEL, BIGWEI = "m_14ad8213", "m_20dd4122", "m_a7d1bb3d"
VOICES = ["m_ce763dbe", "m_8a8e624b"]
PROMPT = (
    f"首帧参考 [[{FRAME}]]：开场画面。KAEL（[[{KAEL}]]）指着 Bigwei（[[{BIGWEI}]]），"
    f"音色 [[{VOICES[0]}]] [[{VOICES[1]}]]。"
)


@pytest.fixture(scope="module")
def registry():
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter
    from cfgpu_mcp.config import get_registry

    return get_registry()


def _req(**kwargs) -> GenerateVideoInput:
    base = dict(
        prompt=PROMPT, first_frame=FRAME,
        reference_images=[KAEL, BIGWEI], reference_audios=VOICES, audio_refs=VOICES,
        duration_seconds=5, resolution="480p", aspect_ratio="16:9", validate_only=True,
    )
    return GenerateVideoInput(**{**base, **kwargs})


@pytest.mark.parametrize("image_refs", [
    [FRAME, KAEL, BIGWEI],  # the frame listed among the image handles (as the agent wrote it)
    [KAEL, BIGWEI],         # image_refs aligned with the old reference_images (as a host sends it)
])
def test_the_named_frame_becomes_the_first_reference_image(image_refs):
    req = _req(image_refs=image_refs)

    assert req.first_frame is None
    assert req.reference_images == [FRAME, KAEL, BIGWEI]
    assert req.image_refs[0] == FRAME
    assert req._schema_corrections["first_frame"] is None
    assert req._schema_corrections["reference_images"] == [FRAME, KAEL, BIGWEI]


@pytest.mark.parametrize("validate_only", [True, False])
def test_the_reported_request_now_passes_on_minimax_h3(registry, validate_only):
    from cfgpu_mcp.adapters.prompt_render import render_for
    from cfgpu_mcp.task_manager import validate_request

    adapter = registry.get("MiniMax-H3")
    req = _req(model="MiniMax-H3", image_refs=[FRAME, KAEL, BIGWEI], validate_only=validate_only)

    assert adapter.supports(req) == (True, "")
    text = adapter.build_payload(render_for(adapter, req).req)["content"][0]["text"]
    assert "[[" not in text and "<Picture 1>" in text
    if validate_only:
        corrected = validate_request(adapter, req)["corrected_args"]
        assert corrected["first_frame"] is None
        assert corrected["reference_images"] == [FRAME, KAEL, BIGWEI]
        assert corrected["image_refs"] == [FRAME, KAEL, BIGWEI]
        assert "model" not in corrected  # an explicitly named model is never rewritten


def test_merging_corrected_args_gives_a_request_that_needs_no_fold(registry):
    from cfgpu_mcp.task_manager import validate_request

    args = dict(
        model="MiniMax-H3", prompt=PROMPT, first_frame=FRAME,
        reference_images=[KAEL, BIGWEI], image_refs=[KAEL, BIGWEI],
        reference_audios=VOICES, audio_refs=VOICES, duration_seconds=5,
        resolution="480p", aspect_ratio="16:9", validate_only=True,
    )
    corrected = validate_request(registry.get("MiniMax-H3"), GenerateVideoInput(**args))["corrected_args"]
    again = GenerateVideoInput(**{**args, **corrected})

    assert again._schema_corrections == {}
    assert again.reference_images == [FRAME, KAEL, BIGWEI]


def test_without_image_refs_only_the_frame_gets_a_handle():
    req = _req(prompt=f"首帧 [[{FRAME}]]，两位人物见参考图。", image_refs=None,
               reference_audios=None, audio_refs=None)

    assert req.reference_images == [FRAME, KAEL, BIGWEI]
    assert req.image_refs == [FRAME, "", ""]


def test_first_and_last_frame_fold_in_order():
    req = _req(
        prompt=f"以 [[{FRAME}]] 开场、[[end]] 收尾，KAEL [[{KAEL}]]。",
        first_frame=FRAME, last_frame="end",
        reference_images=[KAEL], image_refs=[KAEL], reference_audios=None, audio_refs=None,
    )

    assert (req.first_frame, req.last_frame) == (None, None)
    assert req.reference_images == [FRAME, "end", KAEL]
    assert req.image_refs == [FRAME, "end", KAEL]


def test_an_unnamed_frame_is_left_alone_and_still_conflicts(registry):
    req = _req(prompt=f"KAEL [[{KAEL}]] 指着 [[{BIGWEI}]]。", image_refs=[KAEL, BIGWEI],
               reference_audios=None, audio_refs=None)

    assert req.first_frame == FRAME and req._schema_corrections == {}
    ok, reason = registry.get("MiniMax-H3").supports(req)
    assert not ok and "mutually exclusive" in reason


def test_a_frame_with_no_other_reference_is_not_turned_into_a_reference_video():
    """first_frame alone is image-to-video; folding it would change the scenario."""
    with pytest.raises(ValueError, match="a frame cannot be named with"):
        GenerateVideoInput(prompt=f"让 [[{FRAME}]] 动起来", first_frame=FRAME, validate_only=True)


def test_only_one_of_two_frames_named_is_not_folded():
    req = _req(prompt=f"首帧 [[{FRAME}]]，KAEL [[{KAEL}]]", last_frame="end",
               image_refs=[KAEL, BIGWEI], reference_audios=None, audio_refs=None)

    assert (req.first_frame, req.last_frame) == (FRAME, "end")
    assert req._deferred_frame_refs == [FRAME]  # reported after supports(), see below


# ── order: the frames/references conflict is reported before what it causes ────────
#
# A handle mistake that exists only because frames ride beside references — the frame
# named as [[...]], or listed in image_refs as a reference image — disappears once the
# caller follows the conflict refusal. Reporting it first sent the agent to fix the
# symptom and then hit the conflict on the next round trip.


def _preflight(**kwargs):
    import asyncio

    from cfgpu_mcp.errors import CFGPUError
    from cfgpu_mcp.service.video import generate_video

    try:
        return asyncio.run(generate_video(validate_only=True, duration_seconds=5, **kwargs)), None
    except CFGPUError as e:
        return None, e.user_message


@pytest.mark.parametrize("kwargs", [
    # two frames, only the first named: no fold, the placeholder is a consequence
    dict(prompt=f"首帧 [[{FRAME}]] KAEL [[{KAEL}]]", first_frame=FRAME, last_frame="end",
         reference_images=[KAEL], image_refs=[KAEL]),
    # the frame listed in image_refs, never named in the prompt
    dict(prompt=f"KAEL [[{KAEL}]]", first_frame=FRAME, reference_images=[KAEL],
         image_refs=[FRAME, KAEL]),
], ids=["named-frame", "frame-in-image-refs"])
def test_a_model_refusing_frames_beside_references_reports_the_conflict_first(registry, kwargs):
    _, message = _preflight(model="MiniMax-H3", **kwargs)

    assert message is not None
    assert "mutually exclusive" in message
    assert "cannot be named" not in message and "不对应" not in message


def test_a_model_accepting_the_combination_then_reports_the_frame_placeholder(registry):
    _, message = _preflight(
        model="kling-v3-omni", prompt=f"首帧 [[{FRAME}]] KAEL [[{KAEL}]]",
        first_frame=FRAME, last_frame="end", reference_images=[KAEL], image_refs=[KAEL],
    )

    assert message is not None
    assert "names first_frame, and a frame cannot be named" in message


def test_an_unrelated_typo_is_still_refused_at_the_schema():
    """Only mistakes the conflict causes are deferred; a misspelt handle is not one."""
    with pytest.raises(ValueError, match=r"\[\[typo\]\] names no handle"):
        _req(prompt=f"[[typo]] KAEL [[{KAEL}]]", image_refs=[KAEL, BIGWEI],
             reference_audios=None, audio_refs=None)


def test_a_frame_value_that_already_names_a_reference_is_not_moved():
    """[[x]] already resolves to that reference, so nothing is unresolved."""
    req = _req(prompt=f"[[{FRAME}]] 与 [[{KAEL}]]", reference_images=[FRAME, KAEL],
               image_refs=[FRAME, KAEL], reference_audios=None, audio_refs=None)

    assert req.first_frame == FRAME and req._schema_corrections == {}
