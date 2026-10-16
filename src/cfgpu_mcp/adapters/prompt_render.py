"""Render ``[[<handle>]]`` references in ``prompt`` into the selected model's dialect.

One rendering point for every request (I-DL7): ``TaskManager`` calls :func:`render_for`
immediately before ``build_payload``, on the billed path and the ``validate_only``
preflight alike, so what a preflight shows is what would be sent.

- **Image and understanding tools** keep ``regions.resolve_image_refs``, whose whole-image
  ordinal *is* the ``image-ordinal`` dialect (I-DL2). A request carrying regions is still
  rendered by the region-capable adapter's own ``build_payload``, because a token that is
  both a handle and a label can only be caught as ambiguous with both in view.
- **generate_video** renders each ``[[handle]]`` through the model's dialect
  (``adapters/dialect.py``): the handle's slot gives its kind, its position gives its
  number, and the dialect's numbering rule decides whether kinds count separately or as
  one sequence.

Two per-model refusals live in :func:`check_prompt_refs`, called from
``ModelAdapter.supports()`` so ``model="auto"`` routes around them instead of failing:
a placeholder naming a kind the model has no way to name, and — for a dialect marked
``required`` — a reference the rendered prompt never names (D-DL3). Both messages talk
about ``[[handle]]`` only; neither names a model's own label (I-DL5).
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
from cfgpu_mcp.adapters.regions import _PLACEHOLDER, resolve_image_refs
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


def _render_video_prompt(prompt: str, req: Any, dialect: PromptDialect) -> tuple[str, set[tuple[str, int]]]:
    """Substitute every ``[[handle]]``; return the text and which references it named."""
    handles = _handles(req)
    counts = _counts(req)
    named: set[tuple[str, int]] = set()

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        target = handles.get(token)
        if target is None:  # schema validation already refused this; defensive only
            raise CFGPUError(
                error_type="invalid_params",
                user_message=f"prompt 里的占位符 [[{token}]] 不对应任何素材句柄，本次请求未发送。",
                card_hint=False,
            )
        kind, index = target
        label = dialect.label(kind, index, counts)
        if label is None:  # check_prompt_refs refuses this in supports(); defensive only
            raise CFGPUError(
                error_type="invalid_params",
                user_message=f"所选模型无法在 prompt 里点名 [[{token}]] 这类素材，本次请求未发送。",
                card_hint=False,
            )
        named.add(target)
        return label

    return _PLACEHOLDER.sub(substitute, prompt), named


def check_prompt_refs(adapter: "ModelAdapter", req: Any) -> tuple[bool, str]:
    """The per-model half of reference validation, for ``ModelAdapter.supports()``."""
    if not _is_video(req):
        return True, ""
    dialect = dialect_for(adapter)
    if dialect is None:
        return True, ""
    prompt = req.prompt or ""
    handles = _handles(req)

    for match in _PLACEHOLDER.finditer(prompt):
        token = match.group(1).strip()
        target = handles.get(token)
        if target is not None and target[0] not in dialect.ref:
            slot = next(s for s, k in SLOT_KIND.items() if k == target[0])
            return False, (
                f"{adapter.model_name} 不能在 prompt 里点名 {slot} 中的素材（[[{token}]]）："
                f"这类素材对该模型只有固定用途，不由 prompt 指派角色。请去掉这个占位符、用自然语言"
                f"描述它的用途，或换一个能在 prompt 里引用该类素材的模型。"
            )

    if not dialect.required:
        return True, ""
    counts = _counts(req)
    rendered, named = _render_video_prompt(prompt, req, dialect)
    missing: list[str] = []
    for slot, kind in SLOT_KIND.items():
        refs = getattr(req, REFS_FIELD[kind]) or []
        for i in range(counts[kind]):
            if (kind, i) in named:
                continue
            label = dialect.label(kind, i, counts)
            # A caller that still writes this model's own label by hand has named it too.
            if label is not None and label in rendered:
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
    return True, ""


def render_for(adapter: "ModelAdapter", req: Any) -> RenderOutcome:
    """The single rendering point before ``build_payload`` (I-DL7)."""
    dialect = dialect_for(adapter)
    if not _is_video(req):
        rendered_req = resolve_image_refs(req)
        if rendered_req is req:
            return RenderOutcome(req)
        return RenderOutcome(rendered_req, dialect.key if dialect else None, req.prompt)

    prompt = req.prompt or ""
    if dialect is None or not _PLACEHOLDER.search(prompt):
        return RenderOutcome(req)
    ok, reason = check_prompt_refs(adapter, req)
    if not ok:
        raise CFGPUError(error_type="invalid_params", user_message=reason, card_hint=False)
    rendered, _ = _render_video_prompt(prompt, req, dialect)
    return RenderOutcome(req.model_copy(update={"prompt": rendered}), dialect.key, prompt)


# ── hand-written labels ─────────────────────────────────────────────────────

_MEDIA_FIELDS = ("reference_images", "reference_videos", "reference_audios", "images", "video", "first_frame", "last_frame")

#: Reference spellings worth a nudge. Reused from the template library's dialect guard so
#: there is one list of "what a model-specific reference looks like"; shot/field headers
#: are left out because they are not references.
_LABEL_RULES = frozenset({
    "h3-reference-tag",
    "kling-reference-tag",
    "bracket-image-index",
    "wan26-character-tag",
    "cn-ordinal-reference",
    "at-reference",
})

PROMPT_NOTE = (
    "prompt 里出现了手写的素材序号写法。请改用 [[<句柄>]] 指代素材（句柄放在与素材列表同序同长的 "
    "*_refs 里）：同一个素材在不同模型、不同素材顺序下的写法和序号都不同，手写的序号可能指向别的素材。"
)


def handwritten_reference_note(req: Any) -> Optional[str]:
    """A nudge when the caller numbered materials itself instead of using ``[[handle]]``.

    Never a refusal: 「图1」 is also ordinary Chinese, and refusing a legitimate prompt
    costs more than a note. Fires only when the request actually carries media, since a
    reference with nothing to refer to is just prose. The note names no model's label.
    """
    from cfgpu_mcp.template_library.dialect_guard import find_dialect

    if not any(getattr(req, name, None) for name in _MEDIA_FIELDS):
        return None
    prose = _PLACEHOLDER.sub(" ", getattr(req, "prompt", "") or "")
    if any(hit.rule in _LABEL_RULES for hit in find_dialect(prose)):
        return PROMPT_NOTE
    return None


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
        note = handwritten_reference_note(SimpleNamespace(**bound.arguments))
        if note:
            result = {**result, "prompt_note": note}
        return result

    return wrapper
