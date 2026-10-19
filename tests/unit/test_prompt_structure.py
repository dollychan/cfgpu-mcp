"""Prompt structure markers (P2): shots, dialogue, on-screen text, sound, music.

Covers docs/template-library-and-dialect.md §4.2 / §4.6 / §4.7 / §4.9 / §4.10 for the
structural half of the unified notation: the model-neutral parser, the dialect
declaration's structural fields, one golden render per layout, the H3 field layout and
mode header, the per-model refusals (duration, unsupported marker, length), the D-DL10
rewrite default, 万相 2.6's ``shot_type`` and the non-exposure rule for every message.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cfgpu_mcp.adapters import prompt_markup as markup
from cfgpu_mcp.adapters.dialect import parse_dialects, parse_prompt_limits
from cfgpu_mcp.adapters.prompt_render import (
    PROMPT_NOTE,
    STRUCTURE_NOTE,
    check_prompt_refs,
    handwritten_reference_note,
    render_for,
)
from cfgpu_mcp.tool_registry import GenerateImageInput
from cfgpu_mcp.config import load_registry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import validate_request
from cfgpu_mcp.tool_registry import GenerateVideoInput

IMG = "https://x/i{}.png"
VID = "https://x/v{}.mp4"

#: Model-specific spellings that must never reach a caller in an error (I-DL5).
_LABELS = ("第1个镜头", "[Shot", "<Picture", "图片1", "视频1", "character1", "[Image", "秒]：", "says:", "<d>")

STORY = (
    "[[shot 0-3]] [[hero]] 在雨夜街头行走，镜头跟随。[[sound: 雨声]]\n"
    "[[shot 3-6]] 她抬头看向招牌 [[text: 营业中]]，说 [[say S1: 就是这里。]] [[music: 低沉的钢琴]]"
)


@pytest.fixture(scope="module")
def registry():
    return load_registry(disabled_models=[])


def _video(**kw) -> GenerateVideoInput:
    kw.setdefault("resolution", None)
    return GenerateVideoInput(**kw)


def _rendered(adapter, req) -> GenerateVideoInput:
    return render_for(adapter, req).req


# ── parser ───────────────────────────────────────────────────────────────────


def test_parse_splits_every_marker_into_its_node():
    nodes = markup.parse(STORY)
    kinds = [type(n).__name__ for n in nodes if not isinstance(n, markup.Text)]
    assert kinds == ["Shot", "Ref", "Sound", "Shot", "OnScreen", "Say", "Music"]
    first, second = markup.shots(nodes)
    assert (first.start, first.end, first.index) == (Decimal(0), Decimal(3), 1)
    assert (second.start, second.end, second.index) == (Decimal(3), Decimal(6), 2)
    say = next(n for n in nodes if isinstance(n, markup.Say))
    assert (say.speaker, say.text, say.lang) == ("S1", "就是这里。", None)


@pytest.mark.parametrize("token, speaker, lang", [
    ("say: hi", None, None),
    ("say S2: hi", "S2", None),
    ("say S1, S2: hi", "S1,S2", None),
    ("say S1 lang=en: hi", "S1", "English"),
    ("say lang=yue：你好", None, "Cantonese"),
    ("SAY s1: hi", "s1", None),
])
def test_say_variants(token, speaker, lang):
    (node,) = markup.parse(f"[[{token}]]")
    assert (node.speaker, node.lang) == (speaker, lang)


@pytest.mark.parametrize("prompt, fragment", [
    ("[[shot 3-1]]", "ends before it starts"),
    ("[[shot 2-2]]", "ends before it starts"),
    ("[[shot 0-3]] a [[shot 2-5]] b", "must not overlap"),
    ("[[shot 3]]", "not a shot marker"),
    ("[[shot a-b]]", "not a shot marker"),
    ("[[say 你好]]", "colon before the words"),
    ("[[say:]]", "colon before the words"),
    ("[[text]]", "needs a colon"),
    ("[[music:  ]]", "needs a colon"),
])
def test_malformed_markup_is_refused(prompt, fragment):
    with pytest.raises(markup.MarkupError, match=fragment):
        markup.parse(prompt)


def test_shots_may_leave_gaps_and_use_decimals():
    nodes = markup.parse("[[shot 0-2.5]] a [[shot 3.5-6]] b")
    assert [s.start for s in markup.shots(nodes)] == [Decimal("0"), Decimal("3.5")]


def test_keywords_win_only_as_whole_words():
    assert markup.is_reserved("text") and markup.is_reserved("shot 1") and markup.is_reserved("Say")
    assert not markup.is_reserved("textile") and not markup.is_reserved("Shot1") and not markup.is_reserved("m_a1")
    # A handle that merely starts with a keyword's letters is still a handle.
    assert isinstance(markup.parse("[[sounds_good]]")[0], markup.Ref)


def test_time_formats_and_language_detection():
    assert markup.format_seconds(Decimal("3.0")) == "3"
    assert markup.format_seconds(Decimal("10")) == "10"
    assert markup.format_seconds(Decimal("3.50")) == "3.5"
    assert markup.format_mmss_ms(Decimal("3.5")) == "00:03.500"
    assert markup.format_mmss_ms(Decimal("75")) == "01:15.000"
    assert markup.detect_lang("就是这里") == "Chinese"
    assert markup.detect_lang("ここです") == "Japanese"
    assert markup.detect_lang("여기야") == "Korean"
    assert markup.detect_lang("right here") == "English"


# ── schema layer (model-independent) ────────────────────────────────────────


@pytest.mark.parametrize("prompt", ["[[shot 3-1]] x", "[[say 你好]]", "[[text]]", "[[shot 0-3]] a [[shot 1-4]] b"])
def test_schema_refuses_malformed_markup_before_any_model_is_chosen(prompt):
    with pytest.raises(ValidationError, match="prompt markup is malformed"):
        _video(prompt=prompt)


@pytest.mark.parametrize("handle", ["text", "shot", "say x", "music:x"])
def test_a_handle_spelled_like_a_keyword_is_refused(handle):
    with pytest.raises(ValidationError, match="reserved prompt keywords"):
        _video(prompt="x", reference_images=[IMG.format(1)], image_refs=[handle])


def test_structure_alone_needs_no_handle_lists():
    req = _video(prompt="[[shot 0-3]] 走 [[say: 你好]] [[text: 开业]] [[sound: 风]] [[music: 鼓]]")
    assert req.image_refs is None


# ── declaration ──────────────────────────────────────────────────────────────


def _doc(**dialects) -> dict:
    return {"schema_version": 1, "dialects": dialects}


@pytest.mark.parametrize("extra, fragment", [
    ({"layout": "grid"}, "layout must be one of"),
    ({"shot": {"each": "{when}"}}, "unknown fields"),
    ({"shot": {"each": "x", "when": 1}}, "unknown keys"),
    ({"shot": {"each": "x", "time": "frames"}}, "shot.time must be one of"),
    ({"say": {"any": "{speaker}"}}, "must use the {text} field"),
    ({"say": {"any": "{text}", "lang": "guess"}}, "say.lang must be one of"),
    ({"text": "plain"}, "must use the {text} field"),
    ({"music": "[[{text}]]"}, "may not contain"),
    ({"header": {"t2va": "x"}}, "unknown modes"),
    ({"rewrite": "optimize_prompt"}, "rewrite must be one of"),
    ({"multi_shot": "kling"}, "multi_shot must be one of"),
    ({"layout": "h3_fields"}, "needs a shot template"),
])
def test_structural_fields_are_a_closed_set(extra, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_dialects(_doc(d={"version": 1, "ref": {"image": "x{n}"}, **extra}))


def test_prompt_limits_are_validated():
    assert parse_prompt_limits("a", None) is None
    assert parse_prompt_limits("a", {}) is None
    assert parse_prompt_limits("a", {"chars": 7000}) == {"chars": 7000}
    for bad in ({"tokens": 5}, {"chars": 0}, {"chars": True}, {"chars": "7000"}, [7000]):
        with pytest.raises(ValueError):
            parse_prompt_limits("a", bad)


def test_limits_follow_the_extends_chain(registry):
    assert registry.get("wan-video").prompt_limits == {"zh_chars": 500, "en_words": 1000}
    assert registry.get("doubao-seedance-2-0-fast").prompt_limits == {"zh_chars": 500, "en_words": 1000}
    assert registry.get("doubao-seedance-2-5").prompt_limits is None  # its card states none
    assert registry.get("MiniMax-H3").prompt_limits == {"chars": 7000}


# ── golden renders, one per layout ───────────────────────────────────────────


def test_seedance_inline(registry):
    req = _video(prompt=STORY, reference_images=[IMG.format(1)], image_refs=["hero"], duration_seconds=10)
    assert _rendered(registry.get("wan-video"), req).prompt == (
        "[0-3秒]：图片1 在雨夜街头行走，镜头跟随。音效：雨声\n"
        "[3-6秒]：她抬头看向招牌 “营业中”，说 “就是这里。” 背景音乐：低沉的钢琴"
    )


def test_wan27_r2v_numbers_videos_first_inside_shots(registry):
    req = _video(
        prompt="[[shot 0-3]] [[hero]] 模仿 [[dance]] 的动作",
        reference_images=[IMG.format(1)], image_refs=["hero"],
        reference_videos=[VID.format(1)], video_refs=["dance"],
        duration_seconds=5,
    )
    assert _rendered(registry.get("wan2.7-r2v"), req).prompt == "第1个镜头[0-3秒] 图片2 模仿 视频1 的动作"


def test_natural_is_neutral(registry):
    req = _video(prompt="[[shot 0-2.5]] [[hero]] 挥手 [[say: 你好]]", reference_images=[IMG.format(1)], image_refs=["hero"], duration_seconds=5)
    assert _rendered(registry.get("doubao-seedance-1-5-pro"), req).prompt == "0–2.5 秒：参考图1 挥手 “你好”"


def test_h3_fields_layout(registry):
    req = _video(prompt=STORY, reference_images=[IMG.format(1)], image_refs=["hero"], duration_seconds=10)
    assert _rendered(registry.get("MiniMax-H3"), req).prompt == (
        "integrated_multimodal_description: [Shot 1] <Picture 1> 在雨夜街头行走，镜头跟随。 "
        "[Shot 2] At 00:03.000, 她抬头看向招牌 \"营业中\"，说 (S1) says: <d>[Chinese] 就是这里。</d>\n\n"
        "overall_soundscape: 雨声\n\n"
        "non_diegetic_music: 低沉的钢琴"
    )


def test_h3_opens_on_shot_one_and_leaves_unwritten_fields_out(registry):
    """No [[shot]] still gets [Shot 1]; an unwritten soundscape is omitted, never "N/A"
    (which H3's own guide reads as *complete silence*)."""
    req = _video(prompt="女孩挥手 [[say S1 lang=en: Hi]]", duration_seconds=5)
    prompt = _rendered(registry.get("MiniMax-H3"), req).prompt
    assert prompt == "integrated_multimodal_description: [Shot 1] 女孩挥手 (S1) says: <d>[English] Hi</d>"
    assert "N/A" not in prompt


@pytest.mark.parametrize("frames, header", [
    ({"first_frame": IMG.format(1)},
     "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."),
    ({"first_frame": IMG.format(1), "last_frame": IMG.format(2)},
     "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second "
     "mark of the target video; Picture 2 (from Shot 2) aligns with the 6.00-second mark of the target video."),
    ({"last_frame": IMG.format(2)},
     "How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the "
     "6.00-second mark of the target video."),
])
def test_h3_mode_header_is_derived_from_the_frames(registry, frames, header):
    req = _video(prompt="[[shot 0-3]] 起身 [[shot 3-6]] 走开", duration_seconds=6, **frames)
    prompt = _rendered(registry.get("cfdream/minimax-h3"), req).prompt
    assert prompt.split("\n\n")[0] == header
    assert prompt.split("\n\n")[1].startswith("integrated_multimodal_description: [Shot 1] 起身")


def test_h3_text_to_video_has_no_header(registry):
    req = _video(prompt="[[shot 0-3]] 起身", duration_seconds=5)
    assert _rendered(registry.get("cfdream/minimax-h3"), req).prompt.startswith("integrated_multimodal_description:")


def test_h3_ref2va_uses_its_own_layout(registry):
    req = _video(
        prompt="[[shot 0-3]] [[nurse]] 微笑 [[say S1: 欢迎]] [[music: 钢琴]]",
        reference_images=[IMG.format(1)], image_refs=["nurse"], duration_seconds=5,
    )
    assert _rendered(registry.get("cfdream/minimax-h3-r2v"), req).prompt == (
        "detailed_description:\n[Shot 1] <Picture 1> 微笑 (S1) says: <d>[Chinese] 欢迎</d>\n\n"
        "non_diegetic_music:\n钢琴"
    )


def test_references_alone_keep_the_p1_render(registry):
    """No structural marker → no field layout: a references-only prompt renders as before."""
    req = _video(prompt="[[hero]] 在走", reference_images=[IMG.format(1)], image_refs=["hero"], duration_seconds=5)
    assert _rendered(registry.get("MiniMax-H3"), req).prompt == "<Picture 1> 在走"


def test_render_is_idempotent_and_recorded(registry):
    adapter = registry.get("wan-video")
    req = _video(prompt="[[shot 0-3]] 走", duration_seconds=5)
    outcome = render_for(adapter, req)
    assert outcome.dialect == "seedance@1" and outcome.source_prompt == "[[shot 0-3]] 走"
    assert render_for(adapter, outcome.req).req.prompt == outcome.req.prompt


# ── per-model refusals (supports(), so auto routes around them) ─────────────


def test_a_shot_past_the_models_own_duration_is_refused_for_that_model(registry):
    """With duration_seconds unset each model resolves its own length, so the check is
    per model — and auto routes to one whose length fits (Seedance 2.5 decides its own)."""
    adapter = registry.get("wan-video")  # resolves to 5 s
    ok, reason = adapter.supports(_video(prompt="[[shot 0-8]] 走"))
    assert not ok and "[[shot 0-8]]" in reason and "5 秒" in reason
    assert adapter.supports(_video(prompt="[[shot 0-5]] 走"))[0]
    assert registry.get("doubao-seedance-2-5").supports(_video(prompt="[[shot 0-8]] 走"))[0]


def test_a_shot_past_an_explicit_duration_is_refused_at_the_schema():
    """An explicit duration is the same on every model, so no model could take it."""
    with pytest.raises(ValidationError, match=r"\[\[shot 0-8\]\] ends after the video does"):
        _video(prompt="[[shot 0-8]] 走", duration_seconds=5)
    assert _video(prompt="[[shot 0-8]] 走", duration_seconds=8)
    assert _video(prompt="[[shot 0-8]] 走", duration_seconds=-1)  # the model decides: judged per model


def test_a_marker_the_dialect_cannot_write_is_refused():
    adapter = SimpleNamespace(model_name="m", prompt_dialect="image-ordinal", task_type="video", prompt_limits=None,
                              resolve_duration_seconds=lambda req: 5)
    ok, reason = check_prompt_refs(adapter, _video(prompt="[[shot 0-3]] 走"))
    assert not ok and "[[shot …]]" in reason


def test_the_length_limit_is_measured_after_rendering(registry):
    """The rendered shot label 「[0-3秒]：」 carries one CJK character of its own, so 500
    characters as written are 501 as sent — and that is what the card's ceiling bounds."""
    adapter = registry.get("wan-video")
    ok, reason = adapter.supports(_video(prompt="[[shot 0-3]] " + "雨" * 500, duration_seconds=5))
    assert not ok and "500 个汉字" in reason and "501" in reason
    assert adapter.supports(_video(prompt="[[shot 0-3]] " + "雨" * 499, duration_seconds=5))[0]


def test_a_prompt_without_markup_is_not_length_checked(registry):
    """The server did not touch it, so nothing changes for it (no new refusal)."""
    assert registry.get("wan-video").supports(_video(prompt="雨" * 800, duration_seconds=5))[0]


def test_render_for_raises_what_supports_refuses(registry):
    with pytest.raises(CFGPUError):
        render_for(registry.get("wan-video"), _video(prompt="[[shot 0-8]] 走"))


def test_no_refusal_names_a_model_label(registry):
    messages = [
        registry.get("wan-video").supports(_video(prompt="[[shot 0-8]] 走"))[1],
        registry.get("wan-video").supports(_video(prompt="[[shot 0-3]] " + "雨" * 500, duration_seconds=5))[1],
        check_prompt_refs(SimpleNamespace(model_name="m", prompt_dialect="image-ordinal", task_type="video",
                                          prompt_limits=None, resolve_duration_seconds=lambda r: 5),
                          _video(prompt="[[shot 0-3]] [[say: hi]]"))[1],
    ]
    for kw in ({"prompt": "[[shot 3-1]]"}, {"prompt": "[[say 你好]]"}, {"prompt": "[[text]]"},
               {"prompt": "[[shot 0-8]]", "duration_seconds": 5}):
        with pytest.raises(ValidationError) as info:
            _video(**kw)
        messages.append(str(info.value))
    for message in messages:
        assert message
        for label in _LABELS:
            assert label not in message, (label, message)


# ── D-DL10: the server rewrite is off unless the caller asked for it ────────


def test_markup_turns_an_unset_rewrite_off(registry):
    adapter = registry.get("wan2.7-t2v")
    out = _rendered(adapter, _video(prompt="[[shot 0-3]] 走", duration_seconds=5))
    assert out.prompt_extend is False
    assert adapter.build_payload(out)["parameters"]["prompt_extend"] is False


def test_references_alone_also_turn_it_off(registry):
    req = _video(prompt="[[hero]] 走", reference_images=[IMG.format(1)], image_refs=["hero"], duration_seconds=5)
    assert _rendered(registry.get("wan2.7-r2v"), req).prompt_extend is False


@pytest.mark.parametrize("kw", [{"prompt_extend": True}, {"model_specific": {"parameters": {"prompt_extend": True}}}])
def test_an_explicit_choice_wins(registry, kw):
    adapter = registry.get("wan2.7-t2v")
    out = _rendered(adapter, _video(prompt="[[shot 0-3]] 走", duration_seconds=5, **kw))
    assert adapter.build_payload(out)["parameters"]["prompt_extend"] is True


def test_no_markup_keeps_the_api_default(registry):
    adapter = registry.get("wan2.7-t2v")
    out = _rendered(adapter, _video(prompt="走", duration_seconds=5))
    assert out.prompt_extend is None
    assert adapter.build_payload(out)["parameters"]["prompt_extend"] is True


def test_models_without_the_rewrite_are_untouched(registry):
    out = _rendered(registry.get("wan-video"), _video(prompt="[[shot 0-3]] 走", duration_seconds=5))
    assert out.prompt_extend is None


# ── 万相 2.6: several shots need shot_type=multi ─────────────────────────────


def test_two_shots_set_multi(registry):
    adapter = registry.get("wan2.6-t2v")
    out = _rendered(adapter, _video(prompt="[[shot 0-3]] 甲 [[shot 3-5]] 乙", duration_seconds=5))
    payload = adapter.build_payload(out)
    assert payload["parameters"]["shot_type"] == "multi"
    assert payload["parameters"]["prompt_extend"] is True  # 2.6 renders shots only with the rewrite on
    assert payload["input"]["prompt"] == "第1个镜头[0-3秒] 甲 第2个镜头[3-5秒] 乙"


def test_one_shot_sets_nothing_and_a_caller_choice_wins(registry):
    adapter = registry.get("wan2.6-t2v")
    one = _rendered(adapter, _video(prompt="[[shot 0-3]] 甲", duration_seconds=5))
    assert "shot_type" not in adapter.build_payload(one)["parameters"]
    mine = _rendered(adapter, _video(prompt="[[shot 0-3]] 甲 [[shot 3-5]] 乙", duration_seconds=5,
                                     model_specific={"parameters": {"shot_type": "single", "seed": 7}}))
    params = adapter.build_payload(mine)["parameters"]
    assert params["shot_type"] == "single" and params["seed"] == 7


# ── end to end through the preflight ────────────────────────────────────────


def test_validate_only_shows_the_rendered_request(registry):
    adapter = registry.get("wan2.7-t2v")
    result = validate_request(adapter, _video(model="wan2.7-t2v", prompt="[[shot 0-2]] 甲 [[shot 2-5]] 乙", duration_seconds=5))
    assert result["validated"] is True
    assert result["payload"]["input"]["prompt"] == "第1个镜头[0-2秒] 甲 第2个镜头[2-5秒] 乙"
    assert result["payload"]["parameters"]["prompt_extend"] is False
    # The rendered form is a payload fact only; nothing tells the caller to adopt it (I-DL6).
    assert "prompt" not in result["corrected_args"]


def test_validate_only_refuses_a_shot_past_the_duration(registry):
    adapter = registry.get("wan2.7-t2v")
    with pytest.raises(CFGPUError) as info:
        validate_request(adapter, _video(model="wan2.7-t2v", prompt="[[shot 0-9]] 甲"))
    assert "[[shot 0-9]]" in info.value.user_message


# ── hand-written structure gets a note, never a refusal ─────────────────────


@pytest.mark.parametrize("prompt", [
    "第1个镜头[0-3秒] 全景：雨夜街头",
    "[0-3秒]：走 [3-6秒]：跑",
    "[Shot 1] she walks [Shot 2] At 00:03.000, she runs",
    "integrated_multimodal_description: a girl",
    "she says: <d>[English] hi</d>",
])
def test_hand_written_structure_on_a_video_call_is_noted(prompt):
    assert handwritten_reference_note(_video(prompt=prompt)) == STRUCTURE_NOTE


def test_the_unified_notation_and_ordinary_prose_are_not_noted():
    assert handwritten_reference_note(_video(prompt="[[shot 0-3]] 全景 [[say S1: 你好]]")) is None
    assert handwritten_reference_note(_video(prompt="镜头缓慢推进，三秒后切到特写")) is None


def test_structure_is_noted_on_video_only():
    assert handwritten_reference_note(GenerateImageInput(prompt="海报，[0-3秒]：标题")) is None


def test_both_notes_can_apply_at_once():
    req = _video(prompt="第1个镜头[0-3秒] 图片1 在走", reference_images=[IMG.format(1)])
    assert handwritten_reference_note(req) == PROMPT_NOTE + "\n" + STRUCTURE_NOTE


def test_the_structure_note_names_no_model_label():
    for label in ("第1个镜头", "[Shot", "<d>", "秒]：", "integrated_multimodal_description"):
        assert label not in STRUCTURE_NOTE


# ── generate_image: [[text: …]] only ─────────────────────────────────────────


def test_image_text_marker_renders_and_handles_still_resolve(registry):
    adapter = registry.get("gpt-image-2")
    req = GenerateImageInput(prompt="海报顶部大标题 [[text: 秋日拿铁]]，[[cup]] 放在中间", model="gpt-image-2",
                             reference_images=[IMG.format(1)], image_refs=["cup"])
    outcome = render_for(adapter, req)
    assert outcome.req.prompt == '海报顶部大标题 "秋日拿铁"，图1 放在中间'
    assert outcome.dialect == "image-ordinal@1" and outcome.source_prompt == req.prompt


def test_image_text_marker_alongside_regions(registry):
    """With regions the region adapter renders handles and labels itself; [[text]] is
    rendered first and the region tokens reach it unchanged."""
    from cfgpu_mcp.tool_registry import RegionSpec

    adapter = registry.get("doubao-seedream-5-0-pro")
    req = GenerateImageInput(
        prompt="把[[标记1]]换成写着 [[text: OPEN]] 的招牌", model="doubao-seedream-5-0-pro",
        reference_images=[IMG.format(1)], image_refs=["m_a1"],
        regions=[RegionSpec(image_index=0, box=[0.1, 0.2, 0.3, 0.4], label="标记1")],
    )
    payload = adapter.build_payload(render_for(adapter, req).req)
    assert '"OPEN"' in payload["prompt"] and "<bbox>" in payload["prompt"]
    assert "[[" not in payload["prompt"] and "标记1" not in payload["prompt"]


@pytest.mark.parametrize("prompt", ["[[shot 0-3]] 海报", "海报 [[say: 你好]]", "[[music: 钢琴]] 海报", "[[sound: 雨]]"])
def test_video_only_markers_are_refused_on_images(registry, prompt):
    with pytest.raises(CFGPUError) as info:
        render_for(registry.get("gpt-image-2"), GenerateImageInput(prompt=prompt, model="gpt-image-2"))
    assert "[[text: 文字]]" in info.value.user_message
    for label in _LABELS:
        assert label not in info.value.user_message


def test_an_image_prompt_without_markers_is_untouched(registry):
    req = GenerateImageInput(prompt="一杯拿铁，标题写“秋日”", model="gpt-image-2")
    assert render_for(registry.get("gpt-image-2"), req).req is req
