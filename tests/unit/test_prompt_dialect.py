"""Prompt dialects: the caller writes ``[[handle]]``, the server writes each model's label.

Covers docs/template-library-and-dialect.md §4 for material references (P0 + P1):
the declaration file and its load-time checks, one golden render per dialect, the
per-model refusals in ``supports()``, the schema checks on ``generate_video``'s handle
lists, the non-exposure invariants (I-DL5 / I-DL6), and the hand-written-label note.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from cfgpu_mcp.adapters.dialect import (
    DEFAULT_BY_TASK_TYPE,
    get_dialect,
    load_dialects,
    parse_dialects,
)
from cfgpu_mcp.adapters.prompt_render import (
    PROMPT_NOTE,
    handwritten_reference_note,
    render_for,
    with_prompt_note,
)
from cfgpu_mcp.config import load_registry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import _PROMPT_DIALECT_KEY, _PROMPT_SOURCE_KEY, Task, _stash_internal, validate_request
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput, UnderstandVisionInput

IMG = "https://x/i{}.png"
VID = "https://x/v{}.mp4"
AUD = "https://x/a{}.mp3"


@pytest.fixture(scope="module")
def registry():
    return load_registry(disabled_models=[])


def _video(**kw) -> GenerateVideoInput:
    kw.setdefault("resolution", None)
    return GenerateVideoInput(**kw)


# ── declaration ──────────────────────────────────────────────────────────────


def test_every_declared_dialect_loads():
    dialects = load_dialects()
    assert {"image-ordinal", "seedance", "h3", "h3-ref2va", "wan27-r2v", "wan26-r2v", "happyhorse-r2v", "natural"} <= set(dialects)
    assert get_dialect("h3-ref2va").required is True
    assert get_dialect("h3-ref2va").ref == get_dialect("h3").ref  # inherited, not copied


def _doc(**dialects) -> dict:
    return {"schema_version": 1, "dialects": dialects}


@pytest.mark.parametrize(
    "doc, match",
    [
        (_doc(a={"version": 1, "ref": {"image": "图"}}), "exactly the"),
        (_doc(a={"version": 1, "ref": {"image": "图{n}{m}"}}), "exactly the"),
        (_doc(a={"version": 1, "ref": {"picture": "图{n}"}}), "unknown kinds"),
        (_doc(a={"version": 1, "ref": {"image": "[[{n}]]"}}), r"\[\["),
        (_doc(a={"version": 1, "ref": {"image": "图{n}"}, "numbering": "zigzag"}), "numbering"),
        (_doc(a={"version": 1, "ref": {"image": "图{n}", "audio": "音{n}"}, "numbering": "global_ordered", "order": ["image"]}), "global_ordered"),
        (_doc(a={"version": 0, "ref": {"image": "图{n}"}}), "version"),
        (_doc(a={"version": 1, "extends": "b", "ref": {"image": "图{n}"}}, b={"version": 1, "extends": "a", "ref": {"image": "x{n}"}}), "cycle"),
        (_doc(a={"version": 1, "ref": {"image": "图{n}"}, "colour": "red"}), "unknown keys"),
    ],
)
def test_a_malformed_dialect_fails_at_load(doc, match):
    """A typo here would render a plausible, billed prompt pointing at the wrong
    material, so it has to stop the server from starting instead."""
    with pytest.raises(ValueError, match=match):
        parse_dialects(doc)


#: Which models depart from their task type's default. Pinned so a dialect never
#: changes for a model without someone deciding it should.
_DECLARED = {
    "wan-video": "seedance",
    "wan-video-fast": "seedance",
    "doubao-seedance-2-0": "seedance",
    "doubao-seedance-2-0-fast": "seedance",
    "doubao-seedance-2-0-mini": "seedance",
    "doubao-seedance-2-5": "seedance",
    "MiniMax-H3": "h3",
    "minimax-turbo-h3": "h3",
    "cfdream/minimax-h3": "h3",
    "cfdream/minimax-h3-r2v": "h3-ref2va",
    "wan2.7-r2v": "wan27-r2v",
    "wan2.7-t2v": "wan27",
    "wan2.6-r2v": "wan26-r2v",
    "wan2.6-t2v": "wan-multishot",
    "wan2.6-i2v": "wan-multishot",
    "happyhorse-1.0-r2v": "happyhorse-r2v",
}


def test_each_model_resolves_to_the_pinned_dialect(registry):
    for adapter in registry.list_all():
        if adapter.task_type == "audio":
            assert adapter.prompt_dialect is None
            continue
        expected = _DECLARED.get(adapter.model_name, DEFAULT_BY_TASK_TYPE[adapter.task_type])
        assert adapter.prompt_dialect == expected, adapter.model_name


def test_image_and_understanding_models_keep_the_region_ordinal(registry):
    """Region rendering writes `regions._ordinal`, which *is* image-ordinal (I-DL2).
    Giving an image model another dialect would make its handles and its regions
    disagree about what the second image is called."""
    for adapter in registry.list_all():
        if adapter.task_type in ("image", "understand"):
            assert adapter.prompt_dialect == "image-ordinal", adapter.model_name


def test_an_unknown_declaration_fails_at_load():
    from cfgpu_mcp.adapters.dialect import resolve_dialect_id

    with pytest.raises(ValueError, match="unknown prompt_dialect"):
        resolve_dialect_id("x", "video", "seedanse")
    with pytest.raises(ValueError, match="image/video/understand"):
        resolve_dialect_id("x", "audio", "natural")


# ── golden renders ───────────────────────────────────────────────────────────


def _rendered(registry, model: str, **kw) -> str:
    adapter = registry.get(model)
    req = _video(model=model, **kw)
    assert adapter.supports(req) == (True, ""), adapter.supports(req)
    return render_for(adapter, req).req.prompt


def test_seedance_numbers_each_kind_from_one(registry):
    out = _rendered(
        registry, "doubao-seedance-2-0",
        prompt="把[[box]]里的香水换成[[cream]]，配乐用[[bgm]]",
        reference_images=[IMG.format(1)], image_refs=["cream"],
        reference_videos=[VID.format(1)], video_refs=["box"],
        reference_audios=[AUD.format(1)], audio_refs=["bgm"],
    )
    assert out == "把视频1里的香水换成图片1，配乐用音频1"


def test_h3_tags_each_kind(registry):
    out = _rendered(
        registry, "cfdream/minimax-h3-r2v",
        prompt="[[hero]] walks; voice like [[voice]]",
        reference_images=[IMG.format(1)], image_refs=["hero"],
        reference_audios=[AUD.format(1)], audio_refs=["voice"],
    )
    assert out == "<Picture 1> walks; voice like <Audio 1>"


def test_wan27_r2v_counts_videos_then_images_as_one_sequence(registry):
    """Two videos and one image are 视频1 视频2 图片3 — a per-kind count would call the
    image 图片1 and the model would cast the wrong subject."""
    out = _rendered(
        registry, "wan2.7-r2v",
        prompt="[[b]]抱着[[guitar]]弹琴，[[a]]看着[[b]]",
        reference_videos=[VID.format(1), VID.format(2)], video_refs=["a", "b"],
        reference_images=[IMG.format(1)], image_refs=["guitar"],
        duration_seconds=5,
    )
    assert out == "视频2抱着图片3弹琴，视频1看着视频2"


def test_wan26_r2v_names_subjects_in_upload_order(registry):
    out = _rendered(
        registry, "wan2.6-r2v",
        prompt="[[cat]]和[[dog]]在沙发上",
        reference_videos=[VID.format(1)], video_refs=["dog"],
        reference_images=[IMG.format(1)], image_refs=["cat"],
    )
    assert out == "character2和character1在沙发上"


def test_happyhorse_r2v(registry):
    out = _rendered(
        registry, "happyhorse-1.0-r2v",
        prompt="[[dress]]中的女性展开[[fan]]",
        reference_images=[IMG.format(1), IMG.format(2)], image_refs=["dress", "fan"],
    )
    assert out == "[Image 1]中的女性展开[Image 2]"


def test_natural_names_a_reference_in_plain_words(registry):
    out = _rendered(
        registry, "kling-v3-omni",
        prompt="[[hero]] runs",
        reference_images=[IMG.format(1)], image_refs=["hero"],
    )
    assert out == "参考图1 runs"


def test_rendering_is_deterministic_and_leaves_no_notation(registry):
    kw = dict(prompt="[[a]] 和 [[b]]", reference_images=[IMG.format(1), IMG.format(2)], image_refs=["a", "b"])
    outs = {_rendered(registry, "doubao-seedance-2-5", **kw) for _ in range(5)}
    assert len(outs) == 1
    (out,) = outs
    assert "[[" not in out


def test_a_prompt_without_placeholders_is_sent_untouched(registry):
    adapter = registry.get("doubao-seedance-2-0")
    req = _video(prompt="a cat", reference_images=[IMG.format(1)], image_refs=["c"])
    outcome = render_for(adapter, req)
    assert outcome.req is req and outcome.dialect is None


def test_image_tools_keep_rendering_the_whole_image_ordinal(registry):
    """I-DL2: the image path is byte-for-byte what `resolve_image_refs` always did."""
    adapter = registry.get("doubao-seedream-4-5")
    req = GenerateImageInput(prompt="把[[a]]的猫放进[[b]]", reference_images=[IMG.format(1), IMG.format(2)], image_refs=["a", "b"])
    outcome = render_for(adapter, req)
    assert outcome.req.prompt == "把图1的猫放进图2"
    assert outcome.dialect == "image-ordinal@1"


# ── per-model refusals ───────────────────────────────────────────────────────


def test_a_kind_the_model_cannot_name_is_refused_before_sending(registry):
    """万相 2.7 r2v takes audio as each subject's voice, never as something the prompt
    casts; a [[handle]] for it has no rendering and must not reach the model raw."""
    adapter = registry.get("wan2.7-r2v")
    req = _video(
        model="wan2.7-r2v", prompt="[[a]] 用 [[v]] 的声音说话", duration_seconds=5,
        reference_videos=[VID.format(1)], video_refs=["a"],
        reference_audios=[AUD.format(1)], audio_refs=["v"], validate_only=True,
    )
    ok, reason = adapter.supports(req)
    assert not ok and "[[v]]" in reason and "reference_audios" in reason
    with pytest.raises(CFGPUError):
        render_for(adapter, req)


def test_auto_routing_skips_a_model_that_would_ignore_an_untagged_reference(registry):
    from cfgpu_mcp.router import ModelRouter

    req = _video(
        model=["cfdream/minimax-h3-r2v", "doubao-seedance-2-0"],
        prompt="a hero walks", reference_images=[IMG.format(1)],
    )
    assert ModelRouter(registry).resolve(req).model_name == "doubao-seedance-2-0"


# ── schema ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kw, match",
    [
        (dict(reference_images=[IMG.format(1)], image_refs=["a", "b"]), "same length"),
        (dict(reference_images=[IMG.format(1)], reference_videos=[VID.format(1)], image_refs=["a"], video_refs=["a"]), "names both"),
        (dict(reference_images=[IMG.format(1)], image_refs=["a#1"]), "not a usable handle"),
        (dict(prompt="[[a#标记1]]", reference_images=[IMG.format(1)], image_refs=["a"]), "takes no regions"),
        (dict(prompt="[[ghost]]", reference_images=[IMG.format(1)], image_refs=["a"]), r"available here: \[\[a\]\]"),
        (dict(prompt="[[ghost]]"), "has no image_refs"),
    ],
)
def test_handle_lists_and_placeholders_are_checked_at_the_schema(kw, match):
    kw.setdefault("prompt", "x")
    with pytest.raises(ValueError, match=match):
        GenerateVideoInput(**kw, validate_only=True)


def test_an_empty_handle_marks_an_item_nobody_refers_to():
    req = GenerateVideoInput(prompt="[[b]]", reference_images=[IMG.format(1), IMG.format(2)], image_refs=["", "b"])
    assert req.image_refs == ["", "b"]


# ── never shown to the caller (I-DL5 / I-DL6) ────────────────────────────────


def _dialect_markers() -> set[str]:
    """Strings that only a dialect leak would put in agent-facing text: the ids that are
    not also ordinary words or model names, and each non-Chinese label as rendered.
    (「图片1」-style labels are ordinary Chinese and appear in prose legitimately.)"""
    markers = {"prompt_dialect", "image-ordinal", "h3-ref2va", "wan27-r2v", "wan26-r2v", "happyhorse-r2v"}
    for dialect in load_dialects().values():
        for template in dialect.ref.values():
            label = template.format(n=1)
            if label.isascii():
                markers.add(label)
    return markers


def test_no_tool_schema_mentions_a_dialect():
    from cfgpu_mcp.tool_registry import get_anthropic_tools

    text = json.dumps(get_anthropic_tools(), ensure_ascii=False)
    for marker in _dialect_markers():
        assert marker not in text, marker


def test_model_profiles_do_not_mention_a_dialect():
    from cfgpu_mcp.service.model import list_model_profiles

    text = json.dumps(asyncio.run(list_model_profiles()), ensure_ascii=False)
    for marker in _dialect_markers():
        assert marker not in text, marker


def test_preflight_shows_the_rendered_prompt_only_in_the_payload(registry):
    """The preflight's non-payload keys reach the model; if the rendered label rode
    there, a host that merges preflight output back into its call would teach the
    agent the label (I-DL6)."""
    adapter = registry.get("cfdream/minimax-h3-r2v")
    req = _video(model="cfdream/minimax-h3-r2v", prompt="[[hero]] waves", reference_images=[IMG.format(1)], image_refs=["hero"], validate_only=True)
    preflight = validate_request(adapter, req)
    assert "<Picture 1> waves" in json.dumps(preflight["payload"])
    visible = {k: v for k, v in preflight.items() if k != "payload"}
    assert "Picture" not in json.dumps(visible)
    assert "prompt" not in preflight["corrected_args"]


def test_the_row_keeps_how_the_prompt_was_rendered_but_no_response_shows_it(registry):
    adapter = registry.get("doubao-seedance-2-0")
    req = _video(prompt="[[a]] 跳舞", reference_images=[IMG.format(1)], image_refs=["a"])
    outcome = render_for(adapter, req)
    stored = _stash_internal(adapter.build_payload(outcome.req), req, aspect_ratio=False, rendered=outcome)
    assert stored[_PROMPT_DIALECT_KEY] == "seedance@1"
    assert stored[_PROMPT_SOURCE_KEY] == "[[a]] 跳舞"

    row = {
        "id": "t", "adapter_id": adapter.adapter_id, "status": "succeeded", "payload": stored,
        "result": None, "error": None, "created_at": None, "updated_at": None,
    }
    task = Task.__new__(Task)
    task.payload = row["payload"]
    public = task.public_payload()
    assert _PROMPT_DIALECT_KEY not in public and _PROMPT_SOURCE_KEY not in public


# ── hand-written labels ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prompt",
    ["<Picture 1> walks", "把图片1换成视频2里的", "[Image 1]中的女性", "character1在沙发上", "参考@图像1"],
)
def test_a_hand_written_label_earns_a_note(prompt):
    req = GenerateVideoInput(prompt=prompt, reference_images=[IMG.format(1)])
    assert handwritten_reference_note(req) == PROMPT_NOTE


def test_no_note_without_media_or_for_the_unified_notation():
    assert handwritten_reference_note(GenerateVideoInput(prompt="地图1号线")) is None
    assert handwritten_reference_note(GenerateVideoInput(prompt="把图1调亮")) is None  # no media at all
    req = GenerateVideoInput(prompt="[[a]] walks", reference_images=[IMG.format(1)], image_refs=["a"])
    assert handwritten_reference_note(req) is None


def test_the_note_names_no_model_label():
    for marker in _dialect_markers():
        assert marker not in PROMPT_NOTE


def test_the_note_rides_the_service_result():
    @with_prompt_note
    async def service(prompt: str = "", images: list[str] | None = None) -> dict:
        return {"text": "ok"}

    noted = asyncio.run(service(prompt="比较图1和图2", images=[IMG.format(1), IMG.format(2)]))
    assert noted["prompt_note"] == PROMPT_NOTE
    clean = asyncio.run(service(prompt="比较这两张", images=[IMG.format(1)]))
    assert "prompt_note" not in clean


def test_understand_vision_accepts_whole_image_handles(registry):
    adapter = registry.get("qwen3.6-plus")
    req = UnderstandVisionInput(prompt="[[a]]里有什么", images=[IMG.format(1)], image_refs=["a"])
    assert render_for(adapter, req).req.prompt == "图1里有什么"
