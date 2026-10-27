"""Render ``[[<handle>]]`` references in ``prompt`` into the selected model's dialect.

One rendering point for every request (I-DL7): ``TaskManager`` calls :func:`render_for`
immediately before ``build_payload``, on the billed path and the ``validate_only``
preflight alike, so what a preflight shows is what would be sent.

- **Image and understanding tools** keep ``regions.resolve_image_refs``, whose whole-image
  ordinal *is* the ``image-ordinal`` dialect (I-DL2). ``generate_image`` additionally
  renders ``[[text: …]]`` (text that must appear in the picture) first; the other
  structure markers are video-only and refused there. A request carrying regions is still
  rendered by the region-capable adapter's own ``build_payload``, because a token that is
  both a handle and a label can only be caught as ambiguous with both in view.
- **generate_video** parses the prompt into model-neutral nodes
  (``adapters/prompt_markup.py``) and renders each through the model's dialect
  (``adapters/dialect.py``). A ``[[handle]]``'s slot gives its kind, its position its
  number, and the dialect's numbering rule decides whether kinds count separately or as
  one sequence. Structure markers (``[[shot]]`` / ``[[say]]`` / ``[[text]]`` /
  ``[[sound]]`` / ``[[music]]``, P2) render inline, or — for H3 — into H3's own field
  layout behind a mode header derived from the request's frames.

The per-model refusals live in :func:`check_prompt_refs`, called from
``ModelAdapter.supports()`` so ``model="auto"`` routes around them instead of failing: a
placeholder naming a kind the model has no way to name, a marker the dialect cannot
write, a shot past the duration this model resolves to, a reference a ``required``
dialect's rendered prompt never names (D-DL3), and a rendered prompt over the model's
``prompt_limits``. Every message talks about the unified notation only; none names a
model's own label (I-DL5).

:func:`render_for` also applies two request facts that follow from the markup: D-DL10
(a model that rewrites its prompt server-side gets the rewrite off unless the caller set
it) and 万相 2.6's ``shot_type=multi`` for two or more shots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from cfgpu_mcp.adapters.dialect import (
    DEFAULT_BY_TASK_TYPE,
    REFS_FIELD,
    SLOT_KIND,
    PromptDialect,
    get_dialect,
)
from cfgpu_mcp.adapters import prompt_markup as markup
from cfgpu_mcp.adapters.regions import _PLACEHOLDER, is_real_call, resolve_image_refs
from cfgpu_mcp.errors import CFGPUError

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter


@dataclass(frozen=True)
class RenderOutcome:
    """The request to build a payload from, and how its prompt got that way.

    ``dialect`` (``<id>@<version>``) and ``source_prompt`` are set only when rendering
    changed the prompt; they are stashed on the task row so a rendered prompt stays
    explainable after the dialect file moves on (I-DL8).
    """

    req: Any
    dialect: Optional[str] = None
    source_prompt: Optional[str] = None


def dialect_for(adapter: "ModelAdapter") -> Optional[PromptDialect]:
    dialect_id = getattr(adapter, "prompt_dialect", None)
    if not isinstance(dialect_id, str):
        # An adapter built without from_config (test doubles, hand-made instances) has
        # no declaration; it gets the same default a silent adapter.yaml would.
        task_type = getattr(adapter, "task_type", None)
        dialect_id = DEFAULT_BY_TASK_TYPE.get(task_type) if isinstance(task_type, str) else None
    return get_dialect(dialect_id) if dialect_id else None


def _is_video(req: Any) -> bool:
    from cfgpu_mcp.tool_registry import GenerateVideoInput

    return isinstance(req, GenerateVideoInput)


def _is_image(req: Any) -> bool:
    from cfgpu_mcp.tool_registry import GenerateImageInput

    return isinstance(req, GenerateImageInput)


def _render_image_text(req: Any, dialect: Optional[PromptDialect]) -> Any:
    """Render ``[[text: …]]`` on an image prompt; every other marker is video-only.

    Handles and region labels are written back exactly as ``[[token]]`` so the region
    renderer that runs next sees them unchanged. Returns ``req`` itself when the prompt
    carries no structure marker.
    """
    prompt = req.prompt or ""
    if not _PLACEHOLDER.search(prompt):
        return req
    if is_real_call(req):
        # A real call writes a marker this tool cannot carry as text instead of refusing.
        def keep(node: markup.Node) -> bool:
            if not isinstance(node, markup.STRUCTURAL):
                return True  # handles and labels are the region renderer's, next
            return isinstance(node, markup.OnScreen) and dialect is not None and dialect.text is not None

        degraded = markup.degrade(prompt, keep)
        if degraded != prompt:
            req = req.model_copy(update={"prompt": degraded})
            prompt = degraded
    nodes = _parse(prompt)
    structural = [n for n in nodes if isinstance(n, markup.STRUCTURAL)]
    if not structural:
        return req
    if any(not isinstance(n, markup.OnScreen) for n in structural) or dialect is None or dialect.text is None:
        raise CFGPUError(
            error_type="invalid_params",
            user_message=(
                "图片生成的 prompt 只支持 [[text: 文字]] 这一种结构标记；[[shot …]] / [[say …]] / "
                "[[sound: …]] / [[music: …]] 只用于视频。请改用自然语言描述，本次请求未发送。"
            ),
            card_hint=False,
        )
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, markup.Text):
            parts.append(node.text)
        elif isinstance(node, markup.Ref):
            parts.append(f"[[{node.token}]]")
        else:
            parts.append(dialect.text.format(text=node.text))
    return req.model_copy(update={"prompt": "".join(parts)})


def _handles(req: Any) -> dict[str, tuple[str, int]]:
    """``handle -> (kind, index)`` over the three reference slots (schema-validated unique)."""
    out: dict[str, tuple[str, int]] = {}
    for slot, kind in SLOT_KIND.items():
        for i, handle in enumerate(getattr(req, REFS_FIELD[kind]) or []):
            if handle:
                out[handle] = (kind, i)
    return out


def _counts(req: Any) -> dict[str, int]:
    return {kind: len(getattr(req, slot) or []) for slot, kind in SLOT_KIND.items()}


class _Unrenderable(Exception):
    """A node this model's dialect has no way to write."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


@dataclass(frozen=True)
class _Rendered:
    prompt: str
    named: frozenset[tuple[str, int]]
    shot_count: int


def _duration(adapter: "ModelAdapter", req: Any) -> Optional[int]:
    """Seconds the video will last on this model, or ``None`` when the model decides."""
    resolve = getattr(adapter, "resolve_duration_seconds", None)
    try:
        seconds = resolve(req) if callable(resolve) else req.duration_seconds
    except Exception:  # a test double without the config this needs
        seconds = req.duration_seconds
    return seconds if isinstance(seconds, int) and seconds > 0 else None


def _header_mode(req: Any) -> Optional[str]:
    if req.first_frame and req.last_frame:
        return "fl2va"
    if req.first_frame:
        return "i2va"
    if req.last_frame:
        return "l2va"
    return None


def _shot_label(dialect: PromptDialect, shot: markup.Shot) -> str:
    style = dialect.shot
    assert style is not None
    fmt = markup.format_mmss_ms if style.time == "mmss_ms" else markup.format_seconds
    template = style.first if shot.index == 1 else style.next
    return template.format(index=shot.index, start=fmt(shot.start), end=fmt(shot.end))


def _say_label(dialect: PromptDialect, say: markup.Say) -> str:
    style = dialect.say
    assert style is not None
    lang = say.lang or (markup.detect_lang(say.text) if style.lang == "detect" else "")
    if say.speaker:
        return style.speaker.format(speaker=say.speaker, lang=lang, text=say.text)
    return style.anonymous.format(lang=lang, text=say.text)


def _join(parts: list[str], node: Any, piece: str) -> None:
    """Append ``piece``; a shot label starts its own segment, so it is spaced off."""
    if isinstance(node, markup.Shot) and parts and parts[-1] and not parts[-1][-1].isspace():
        parts.append(" ")
    parts.append(piece)


def _render_nodes(nodes: list[markup.Node], req: Any, dialect: PromptDialect, adapter: "ModelAdapter") -> _Rendered:
    """Render parsed nodes in ``dialect``; raise :class:`_Unrenderable` for a gap."""
    handles = _handles(req)
    counts = _counts(req)
    named: set[tuple[str, int]] = set()
    fields = dialect.layout != "inline"
    body: list[str] = []
    sounds: list[str] = []
    music: list[str] = []
    strip_next = False

    for node in nodes:
        if isinstance(node, markup.Text):
            text = node.text.lstrip(" \t") if strip_next else node.text
            strip_next = False
            body.append(text)
            continue
        strip_next = False
        if isinstance(node, markup.Ref):
            target = handles.get(node.token)
            if target is None:  # schema validation already refused this; defensive only
                raise CFGPUError(
                    error_type="invalid_params",
                    user_message=f"prompt 里的占位符 [[{node.token}]] 不对应任何素材句柄，本次请求未发送。",
                    card_hint=False,
                )
            label = dialect.label(target[0], target[1], counts)
            if label is None:
                raise _Unrenderable(f"[[{node.token}]]")
            named.add(target)
            body.append(label)
        elif isinstance(node, markup.Shot):
            if dialect.shot is None:
                raise _Unrenderable("[[shot …]]")
            _join(body, node, _shot_label(dialect, node))
            strip_next = True
        elif isinstance(node, markup.Say):
            if dialect.say is None:
                raise _Unrenderable("[[say …]]")
            body.append(_say_label(dialect, node))
        elif isinstance(node, markup.OnScreen):
            if dialect.text is None:
                raise _Unrenderable("[[text: …]]")
            body.append(dialect.text.format(text=node.text))
        elif isinstance(node, (markup.Sound, markup.Music)):
            if fields:
                (sounds if isinstance(node, markup.Sound) else music).append(node.text)
                continue
            template = dialect.sound if isinstance(node, markup.Sound) else dialect.music
            if template is None:
                raise _Unrenderable("[[sound: …]]" if isinstance(node, markup.Sound) else "[[music: …]]")
            body.append(template.format(text=node.text))

    shot_count = len(markup.shots(nodes))
    if not fields or not markup.has_structure(nodes):
        return _Rendered("".join(body), frozenset(named), shot_count)

    # H3 field layout: the body is one timeline that always opens on [Shot 1].
    text = re.sub(r"\s+", " ", "".join(body)).strip()
    if shot_count == 0:
        assert dialect.shot is not None
        text = dialect.shot.first.format(index=1, start="", end="") + text
    joined_sound = " ".join(sounds)
    joined_music = " ".join(music)
    if dialect.layout == "h3_ref2va":
        sections = [f"detailed_description:\n{text}"]
        if joined_sound:
            sections.append(f"overall_soundscape:\n{joined_sound}")
        if joined_music:
            sections.append(f"non_diegetic_music:\n{joined_music}")
    else:
        sections = [f"integrated_multimodal_description: {text}"]
        # A field the caller did not write is left out rather than filled with "N/A":
        # in H3's own guide "N/A" soundscape means *complete silence*, which is a claim
        # the caller never made.
        if joined_sound:
            sections.append(f"overall_soundscape: {joined_sound}")
        if joined_music:
            sections.append(f"non_diegetic_music: {joined_music}")
    mode = _header_mode(req)
    template = dialect.header.get(mode) if mode else None
    if template:
        duration = _duration(adapter, req)
        sections.insert(0, template.format(
            duration=f"{duration:.2f}" if duration else "",
            last_index=max(shot_count, 1),
        ))
    return _Rendered("\n\n".join(sections), frozenset(named), shot_count)


def _parse(prompt: str) -> list[markup.Node]:
    try:
        return markup.parse(prompt)
    except markup.MarkupError as exc:  # schema validation already refused this; defensive only
        raise CFGPUError(error_type="invalid_params", user_message=f"{exc} 本次请求未发送。", card_hint=False) from None


def _over_limit(adapter: "ModelAdapter", prompt: str) -> Optional[str]:
    limits = getattr(adapter, "prompt_limits", None)
    if not isinstance(limits, dict):
        return None
    measured = {
        "chars": (len(prompt), "字符"),
        "zh_chars": (len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", prompt)), "个汉字"),
        "en_words": (len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", prompt)), "个英文词"),
    }
    for unit, ceiling in limits.items():
        value, noun = measured[unit]
        if value > ceiling:
            return f"{ceiling} {noun}（这次是 {value}）"
    return None


def _lenient_for_model(req: Any) -> bool:
    """A real call naming one concrete model, so the per-model checks repair instead of refuse.

    With ``model="auto"`` (or a candidate list) the same checks are how the router steers
    *around* a model that would mangle the prompt — skipping one that cannot name a kind
    or silently ignores an unnamed reference costs nothing, since another model takes the
    call as written. Only once the choice is pinned (a caller that preflighted gets the
    concrete model back in ``corrected_args``) is there no better model to route to, and
    repairing becomes better than refusing.
    """
    if not is_real_call(req):
        return False
    model = getattr(req, "model", None)
    if isinstance(model, list):
        return len(model) == 1
    return isinstance(model, str) and model != "auto"


def _degrade_for(adapter: "ModelAdapter", req: Any, dialect: PromptDialect) -> Any:
    """A real call's video prompt with everything this model cannot render written as text.

    The per-model half of the lenient contract (the model-independent half ran at the
    schema layer): a ``[[handle]]`` whose kind this dialect cannot name, a marker it has
    no way to write, a shot past the duration this model resolves to. Returns ``req``
    itself when nothing changes.
    """
    prompt = req.prompt or ""
    if not _PLACEHOLDER.search(prompt):
        return req
    handles = _handles(req)
    counts = _counts(req)
    duration = _duration(adapter, req)
    fields = dialect.layout != "inline"

    def keep(node: markup.Node) -> bool:
        if isinstance(node, markup.Ref):
            target = handles.get(node.token)
            return target is not None and target[0] in dialect.ref and dialect.label(target[0], target[1], counts) is not None
        if isinstance(node, markup.Shot):
            return dialect.shot is not None and (duration is None or node.end <= duration)
        if isinstance(node, markup.Say):
            return dialect.say is not None
        if isinstance(node, markup.OnScreen):
            return dialect.text is not None
        if isinstance(node, markup.Sound):
            return fields or dialect.sound is not None
        if isinstance(node, markup.Music):
            return fields or dialect.music is not None
        return True

    degraded = markup.degrade(prompt, keep)
    return req if degraded == prompt else req.model_copy(update={"prompt": degraded})


def check_prompt_refs(adapter: "ModelAdapter", req: Any) -> tuple[bool, str]:
    """The per-model half of prompt validation, for ``ModelAdapter.supports()``.

    Everything here is about how this model would render the prompt; a prompt with no
    ``[[...]]`` is looked at only by a ``required`` dialect. Messages speak the unified
    notation only (I-DL5).

    A real call (``validate_only=false``) on a pinned model (:func:`_lenient_for_model`)
    refuses none of the placeholder cases: they are written as text first
    (:func:`_degrade_for`), and the ``required`` dialect's "reference not named" check is
    skipped. A person who edited the prompt on an
    approval card decided what the model is told; the preflight is where a planner's
    mistakes are reported. Only the prompt length limit still applies — it is not about
    placeholders, and the upstream would refuse it anyway.
    """
    if not _is_video(req):
        return True, ""
    dialect = dialect_for(adapter)
    if dialect is None:
        return True, ""
    lenient = _lenient_for_model(req)
    if lenient:
        req = _degrade_for(adapter, req, dialect)
    prompt = req.prompt or ""
    has_markup = bool(_PLACEHOLDER.search(prompt))
    # Without markup only a `required` dialect has anything to say: a reference the
    # prompt never names (perhaps named by hand in the model's own label).
    if not has_markup and not dialect.required:
        return True, ""
    nodes = _parse(prompt) if has_markup else [markup.Text(prompt)]
    handles = _handles(req)

    for node in nodes:
        if isinstance(node, markup.Ref):
            target = handles.get(node.token)
            if target is not None and target[0] not in dialect.ref:
                slot = next(s for s, k in SLOT_KIND.items() if k == target[0])
                return False, (
                    f"{adapter.model_name} 不能在 prompt 里点名 {slot} 中的素材（[[{node.token}]]）："
                    f"这类素材对该模型只有固定用途，不由 prompt 指派角色。请去掉这个占位符、用自然语言"
                    f"描述它的用途，或换一个能在 prompt 里引用该类素材的模型。"
                )

    duration = _duration(adapter, req)
    for shot in markup.shots(nodes):
        if duration is not None and shot.end > duration:
            return False, (
                f"[[shot {markup.format_seconds(shot.start)}-{markup.format_seconds(shot.end)}]] "
                f"超出了视频时长：{adapter.model_name} 这次生成 {duration} 秒。请把分镜写在时长之内，"
                f"或把 duration_seconds 设为能装下全部分镜的秒数。"
            )

    try:
        rendered = _render_nodes(nodes, req, dialect, adapter)
    except _Unrenderable as gap:
        return False, (
            f"{adapter.model_name} 不支持 prompt 里的 {gap.what} 标记。请改用自然语言描述，"
            f"或换一个支持这种标记的模型。"
        )

    if dialect.required and not lenient:
        counts = _counts(req)
        missing: list[str] = []
        for slot, kind in SLOT_KIND.items():
            refs = getattr(req, REFS_FIELD[kind]) or []
            for i in range(counts[kind]):
                if (kind, i) in rendered.named:
                    continue
                label = dialect.label(kind, i, counts)
                # A caller that still writes this model's own label by hand has named it too.
                if label is not None and label in rendered.prompt:
                    continue
                handle = refs[i] if i < len(refs) else ""
                missing.append(f"{slot}[{i}]" + (f"（[[{handle}]]）" if handle else ""))
        if missing:
            return False, (
                f"{adapter.model_name} 只使用 prompt 里点名的参考素材，没点名的会被静默忽略、照常计费。"
                f"以下参考素材没有在 prompt 中被引用：{'、'.join(missing)}。请在 prompt 里用 "
                f"[[<句柄>]] 写出每个素材在画面里扮演什么（句柄放在与素材列表同序同长的 image_refs / "
                f"video_refs / audio_refs 里），本次请求未发送。"
            )

    over = _over_limit(adapter, rendered.prompt) if has_markup else None
    if over:
        return False, (
            f"{adapter.model_name} 的 prompt 上限是 {over}（按替换完 [[...]] 后的最终文本计算）。"
            f"请精简 prompt，或换一个上限更高的模型。"
        )
    return True, ""


def _sets_parameter(req: Any, name: str) -> bool:
    """Did the caller set ``name`` itself, through ``model_specific``?"""
    extra = req.model_specific or {}
    params = extra.get("parameters")
    return name in extra or (isinstance(params, dict) and name in params)


def render_for(adapter: "ModelAdapter", req: Any) -> RenderOutcome:
    """The single rendering point before ``build_payload`` (I-DL7)."""
    dialect = dialect_for(adapter)
    if not _is_video(req):
        staged = _render_image_text(req, dialect) if _is_image(req) else req
        rendered_req = resolve_image_refs(staged)
        if rendered_req is req:
            return RenderOutcome(req)
        return RenderOutcome(rendered_req, dialect.key if dialect else None, req.prompt)

    source = req.prompt or ""
    if dialect is not None and _lenient_for_model(req):
        req = _degrade_for(adapter, req, dialect)
    prompt = req.prompt or ""
    if dialect is None or not _PLACEHOLDER.search(prompt):
        return RenderOutcome(req) if prompt == source else RenderOutcome(req, dialect.key if dialect else None, source)
    ok, reason = check_prompt_refs(adapter, req)
    if not ok:
        raise CFGPUError(error_type="invalid_params", user_message=reason, card_hint=False)
    rendered = _render_nodes(_parse(prompt), req, dialect, adapter)
    update: dict[str, Any] = {"prompt": rendered.prompt}
    # D-DL10: a server-side rewrite would renumber the labels and retime the shots just
    # rendered, so it is off unless the caller asked for it — through the typed field or
    # model_specific, either of which wins.
    if dialect.rewrite == "prompt_extend" and req.prompt_extend is None and not _sets_parameter(req, "prompt_extend"):
        update["prompt_extend"] = False
    # 万相 2.6 renders several shots only when told to; the shots are the caller's
    # instruction, so the parameter follows from them unless the caller set it.
    if dialect.multi_shot == "wan26_shot_type" and rendered.shot_count >= 2 and not _sets_parameter(req, "shot_type"):
        extra = dict(req.model_specific or {})
        params = dict(extra.get("parameters") or {})
        params["shot_type"] = "multi"
        extra["parameters"] = params
        update["model_specific"] = extra
    return RenderOutcome(req.model_copy(update=update), dialect.key, source)


# ── hand-written labels ─────────────────────────────────────────────────────

_MEDIA_FIELDS = ("reference_images", "reference_videos", "reference_audios", "images", "video", "first_frame", "last_frame")

#: Reference spellings worth a nudge. Reused from the template library's dialect guard so
#: there is one list of "what a model-specific reference looks like".
_LABEL_RULES = frozenset({
    "h3-reference-tag",
    "kling-reference-tag",
    "bracket-image-index",
    "wan26-character-tag",
    "cn-ordinal-reference",
    "at-reference",
})

#: Hand-written structure (P2): shot headers, time ranges, dialogue tags, field names.
#: Unlike a reference these are never ordinary prose, so they are noted with or without
#: media — on video calls only, the one tool the structure markers exist on.
_STRUCTURE_RULES = frozenset({
    "h3-shot-header",
    "h3-field",
    "h3-markup",
    "wan-multishot-header",
    "seconds-range-header",
})

PROMPT_NOTE = (
    "prompt 里出现了手写的素材序号写法。请改用 [[<句柄>]] 指代素材（句柄放在与素材列表同序同长的 "
    "*_refs 里）：同一个素材在不同模型、不同素材顺序下的写法和序号都不同，手写的序号可能指向别的素材。"
)

STRUCTURE_NOTE = (
    "prompt 里出现了手写的镜头编号、时间段或台词标签。请改用 [[shot 0-3]] 标出每个镜头（秒），"
    "[[say S1: 原话]] 写台词，[[text: 文字]] 写画面文字：各模型的镜头与台词格式不同，统一写法会按所选模型生成，"
    "手写的格式换了模型就可能被当成普通文字。"
)


def handwritten_reference_note(req: Any) -> Optional[str]:
    """A nudge when the caller wrote a model's own notation instead of the unified one.

    Never a refusal: 「图1」 is also ordinary Chinese, and refusing a legitimate prompt
    costs more than a note. A hand-numbered reference is noted only when the request
    actually carries media (a reference with nothing to refer to is just prose); a
    hand-written shot header or dialogue tag only on a video call (``duration_seconds``
    is the video tool's own parameter). Neither note names a model's label.
    """
    from cfgpu_mcp.template_library.dialect_guard import find_dialect

    prose = _PLACEHOLDER.sub(" ", getattr(req, "prompt", "") or "")
    rules = {hit.rule for hit in find_dialect(prose)}
    notes: list[str] = []
    if rules & _LABEL_RULES and any(getattr(req, name, None) for name in _MEDIA_FIELDS):
        notes.append(PROMPT_NOTE)
    if rules & _STRUCTURE_RULES and hasattr(req, "duration_seconds"):
        notes.append(STRUCTURE_NOTE)
    return "\n".join(notes) or None


def with_prompt_note(service: Any) -> Any:
    """Decorate an async generate/understand service to attach :data:`PROMPT_NOTE`.

    The note goes under ``prompt_note``, a top-level key, so the MCP layer's
    ``split_structured`` keeps it in the LLM-visible content — the caller that wrote the
    label is the one that has to read it. Computed from the call's own arguments, so
    every exposure (MCP, Mode B, LangGraph) gets it from the one shared service.
    """
    import functools
    import inspect
    from types import SimpleNamespace

    signature = inspect.signature(service)

    @functools.wraps(service)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await service(*args, **kwargs)
        if not isinstance(result, dict) or result.get("error"):
            return result
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()  # which tool this is shows in its parameters, passed or not
        note = handwritten_reference_note(SimpleNamespace(**bound.arguments))
        if note:
            result = {**result, "prompt_note": note}
        return result

    return wrapper
