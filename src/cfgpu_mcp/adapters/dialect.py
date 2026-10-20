"""Prompt dialects: how a model wants a reference material named inside ``prompt``.

The caller writes one model-neutral form — ``[[<handle>]]`` next to a ``*_refs`` list —
and the server renders it into whatever this model was trained to read: ``图片1`` for
Seedance, ``<Picture 1>`` for MiniMax H3, ``视频1 视频2 图片3`` (one sequence across
kinds) for 万相 2.7 r2v. Which form a model reads is data, declared once in
``capabilities/prompt_dialects.yaml`` and referenced from ``adapter.yaml`` by
``prompt_dialect:`` (docs/template-library-and-dialect.md D-DL1).

None of this is ever shown to an agent (I-DL5). A dialect the agent can see is a dialect
the agent will copy, and then mix with the next model's — the failure the unified
notation exists to remove. So the dialect id, the label templates and the rendered
prompt stay inside the server: the agent-facing schema talks only about ``[[handle]]``,
and the rendered prompt reaches only ``structuredContent.payload``.

A dialect also says how the structure markers render (P2): shot headers and their time
format, dialogue, on-screen text, sound/music, the H3 field layouts and mode headers, the
server-side rewrite D-DL10 turns off, and 万相 2.6's ``shot_type``. Every one of those is
a template or a choice from a closed set in this module; the YAML never holds logic
(D-DL11).

The file is validated when first loaded and any error raises: a typo in a template or a
numbering name would otherwise render a plausible, billed prompt that points at the
wrong material.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

DIALECTS_FILE = Path(__file__).resolve().parent.parent / "capabilities" / "prompt_dialects.yaml"

#: Material kinds a reference can be, in the order the three slots are listed.
KINDS: tuple[str, ...] = ("image", "video", "audio")

#: Reference slot → kind. ``first_frame`` / ``last_frame`` are deliberately absent: a
#: frame is the video's literal opening/closing picture, not a reference the prompt
#: casts in a role, and no model numbers it alongside the references.
SLOT_KIND: dict[str, str] = {
    "reference_images": "image",
    "reference_videos": "video",
    "reference_audios": "audio",
}

#: Kind → the caller-handle list that names that slot's items.
REFS_FIELD: dict[str, str] = {"image": "image_refs", "video": "video_refs", "audio": "audio_refs"}

#: The dialect a model gets when its adapter.yaml declares none. Image and understanding
#: models share the ordinal ``regions`` has always rendered (I-DL2). A video model with
#: no verified convention names references in plain words, which no model misreads as a
#: different reference and which is never refused (D-DL6: unverified → natural).
DEFAULT_BY_TASK_TYPE: dict[str, str] = {
    "image": "image-ordinal",
    "understand": "image-ordinal",
    "video": "natural",
}

_NUMBERINGS = frozenset({"per_kind", "global_ordered"})
_TEMPLATE_FIELDS = frozenset({"n"})
_KEYS = frozenset({
    "version", "extends", "ref", "numbering", "order", "required",
    "layout", "shot", "say", "text", "sound", "music", "header", "rewrite", "multi_shot",
})

#: How the rendered pieces are assembled (D-DL11). ``inline`` keeps the caller's order;
#: the H3 layouts move sound/music into their own fields and prefix a mode header.
LAYOUTS = frozenset({"inline", "h3_fields", "h3_ref2va"})
#: Shot time formats: ``secs`` → ``3.5``; ``mmss_ms`` → ``00:03.500``.
SHOT_TIMES = frozenset({"secs", "mmss_ms"})
#: Dialogue language policy: name it (``detect`` from the script unless the caller said)
#: or leave it out.
SAY_LANGS = frozenset({"detect", "omit"})
#: Server-side prompt rewrite this model performs, and so the request field D-DL10 turns
#: off when the prompt carries markup.
REWRITES = frozenset({"prompt_extend"})
#: How a multi-shot prompt is backed by a request parameter (万相 2.6's ``shot_type``).
MULTI_SHOTS = frozenset({"wan26_shot_type"})
#: H3 mode headers, chosen from the request's frames (never written by the caller).
HEADER_MODES = frozenset({"i2va", "fl2va", "l2va"})

_SHOT_FIELDS = frozenset({"index", "start", "end"})
_SAY_FIELDS = frozenset({"speaker", "lang", "text"})
_TEXT_FIELDS = frozenset({"text"})
_HEADER_FIELDS = frozenset({"duration", "last_index"})


@dataclass(frozen=True)
class ShotStyle:
    first: str
    next: str
    time: str = "secs"


@dataclass(frozen=True)
class SayStyle:
    speaker: str
    anonymous: str
    lang: str = "omit"


@dataclass(frozen=True)
class PromptDialect:
    id: str
    version: int
    ref: Mapping[str, str]
    numbering: str = "per_kind"
    order: tuple[str, ...] = field(default=KINDS)
    required: bool = False
    layout: str = "inline"
    shot: Optional[ShotStyle] = None
    say: Optional[SayStyle] = None
    text: Optional[str] = None
    sound: Optional[str] = None
    music: Optional[str] = None
    header: Mapping[str, str] = field(default_factory=dict)
    rewrite: Optional[str] = None
    multi_shot: Optional[str] = None

    @property
    def key(self) -> str:
        """``<id>@<version>`` — what a task row records about how its prompt was rendered."""
        return f"{self.id}@{self.version}"

    def number(self, kind: str, index: int, counts: Mapping[str, int]) -> int:
        """1-based number of item ``index`` of ``kind``, given how many of each kind exist.

        ``counts`` matters only for ``global_ordered``, where the images of a request with
        two reference videos start at 3.
        """
        if self.numbering == "per_kind":
            return index + 1
        offset = 0
        for k in self.order:
            if k == kind:
                return offset + index + 1
            offset += counts.get(k, 0)
        raise ValueError(f"dialect {self.id!r} does not order kind {kind!r}")

    def label(self, kind: str, index: int, counts: Mapping[str, int]) -> Optional[str]:
        """The rendered label, or ``None`` when this model has no way to name ``kind``."""
        template = self.ref.get(kind)
        if template is None:
            return None
        return template.format(n=self.number(kind, index, counts))


def _template_fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


def _template(dialect_id: str, where: str, template: Any, allowed: frozenset[str], need: str | None) -> str:
    """Validate one structural template: a non-empty string over ``allowed`` fields."""
    if not isinstance(template, str) or not template:
        raise ValueError(f"prompt dialect {dialect_id!r}: {where} must be a non-empty string")
    fields = _template_fields(template)
    if fields - allowed:
        raise ValueError(
            f"prompt dialect {dialect_id!r}: {where} uses unknown fields {sorted(fields - allowed)} "
            f"(allowed: {sorted(allowed)})"
        )
    if need and need not in fields:
        raise ValueError(f"prompt dialect {dialect_id!r}: {where} must use the {{{need}}} field")
    if "[[" in template or "]]" in template:
        raise ValueError(f"prompt dialect {dialect_id!r}: {where} may not contain [[ or ]]")
    return template


def _closed(dialect_id: str, key: str, value: Any, allowed: frozenset[str]) -> Any:
    if value is not None and value not in allowed:
        raise ValueError(f"prompt dialect {dialect_id!r}: {key} must be one of {sorted(allowed)}")
    return value


def _build_structure(dialect_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """The structural half of a dialect (P2): shots, dialogue, on-screen text, sound."""
    out: dict[str, Any] = {}
    layout = raw.get("layout", "inline")
    out["layout"] = _closed(dialect_id, "layout", layout, LAYOUTS)

    shot = raw.get("shot")
    if shot is not None:
        if not isinstance(shot, dict):
            raise ValueError(f"prompt dialect {dialect_id!r}: shot must be a mapping")
        unknown = set(shot) - {"each", "first", "next", "time"}
        if unknown:
            raise ValueError(f"prompt dialect {dialect_id!r}: shot has unknown keys {sorted(unknown)}")
        each = shot.get("each")
        first = shot.get("first", each)
        nxt = shot.get("next", each)
        out["shot"] = ShotStyle(
            _template(dialect_id, "shot.first", first, _SHOT_FIELDS, None),
            _template(dialect_id, "shot.next", nxt, _SHOT_FIELDS, None),
            _closed(dialect_id, "shot.time", shot.get("time", "secs"), SHOT_TIMES),
        )

    say = raw.get("say")
    if say is not None:
        if not isinstance(say, dict):
            raise ValueError(f"prompt dialect {dialect_id!r}: say must be a mapping")
        unknown = set(say) - {"any", "speaker", "anonymous", "lang"}
        if unknown:
            raise ValueError(f"prompt dialect {dialect_id!r}: say has unknown keys {sorted(unknown)}")
        any_ = say.get("any")
        out["say"] = SayStyle(
            _template(dialect_id, "say.speaker", say.get("speaker", any_), _SAY_FIELDS, "text"),
            _template(dialect_id, "say.anonymous", say.get("anonymous", any_), _SAY_FIELDS - {"speaker"}, "text"),
            _closed(dialect_id, "say.lang", say.get("lang", "omit"), SAY_LANGS),
        )

    for key in ("text", "sound", "music"):
        if raw.get(key) is not None:
            out[key] = _template(dialect_id, key, raw[key], _TEXT_FIELDS, "text")

    header = raw.get("header") or {}
    if not isinstance(header, dict):
        raise ValueError(f"prompt dialect {dialect_id!r}: header must be a mapping")
    if set(header) - HEADER_MODES:
        raise ValueError(f"prompt dialect {dialect_id!r}: header has unknown modes {sorted(set(header) - HEADER_MODES)}")
    out["header"] = {
        mode: _template(dialect_id, f"header.{mode}", template, _HEADER_FIELDS, None)
        for mode, template in header.items()
    }
    out["rewrite"] = _closed(dialect_id, "rewrite", raw.get("rewrite"), REWRITES)
    out["multi_shot"] = _closed(dialect_id, "multi_shot", raw.get("multi_shot"), MULTI_SHOTS)
    if out["layout"] != "inline" and out.get("shot") is None:
        raise ValueError(f"prompt dialect {dialect_id!r}: layout {out['layout']} needs a shot template")
    return out


def _build(dialect_id: str, raw: dict[str, Any]) -> PromptDialect:
    unknown = set(raw) - _KEYS
    if unknown:
        raise ValueError(f"prompt dialect {dialect_id!r}: unknown keys {sorted(unknown)}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"prompt dialect {dialect_id!r}: version must be a positive integer")
    ref = raw.get("ref") or {}
    if not isinstance(ref, dict) or not ref:
        raise ValueError(f"prompt dialect {dialect_id!r}: ref must map at least one kind to a template")
    bad_kinds = set(ref) - set(KINDS)
    if bad_kinds:
        raise ValueError(f"prompt dialect {dialect_id!r}: unknown kinds {sorted(bad_kinds)} (valid: {list(KINDS)})")
    for kind, template in ref.items():
        if not isinstance(template, str) or not template:
            raise ValueError(f"prompt dialect {dialect_id!r}: ref.{kind} must be a non-empty string")
        fields = _template_fields(template)
        if fields - _TEMPLATE_FIELDS or "n" not in fields:
            raise ValueError(
                f"prompt dialect {dialect_id!r}: ref.{kind} must use exactly the {{n}} field, got {sorted(fields)}"
            )
        if "[[" in template or "]]" in template:
            # A rendered label containing the notation would be re-read as a placeholder.
            raise ValueError(f"prompt dialect {dialect_id!r}: ref.{kind} may not contain [[ or ]]")
    numbering = raw.get("numbering", "per_kind")
    if numbering not in _NUMBERINGS:
        raise ValueError(f"prompt dialect {dialect_id!r}: numbering must be one of {sorted(_NUMBERINGS)}")
    order = tuple(raw.get("order") or KINDS)
    if set(order) - set(KINDS) or len(set(order)) != len(order):
        raise ValueError(f"prompt dialect {dialect_id!r}: order must list distinct kinds from {list(KINDS)}")
    if numbering == "global_ordered" and not set(ref) <= set(order):
        raise ValueError(f"prompt dialect {dialect_id!r}: global_ordered needs every ref kind in order")
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise ValueError(f"prompt dialect {dialect_id!r}: required must be a boolean")
    return PromptDialect(dialect_id, version, dict(ref), numbering, order, required, **_build_structure(dialect_id, raw))


def parse_dialects(data: Any) -> dict[str, PromptDialect]:
    """Validate and resolve a dialects document (exposed for tests)."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("prompt dialects: schema_version must be 1")
    raw_all = data.get("dialects")
    if not isinstance(raw_all, dict) or not raw_all:
        raise ValueError("prompt dialects: dialects must be a non-empty mapping")

    resolved: dict[str, dict[str, Any]] = {}

    def resolve(dialect_id: str, seen: tuple[str, ...]) -> dict[str, Any]:
        if dialect_id in resolved:
            return resolved[dialect_id]
        if dialect_id in seen:
            raise ValueError(f"prompt dialects: extends cycle {' -> '.join((*seen, dialect_id))}")
        raw = raw_all.get(dialect_id)
        if not isinstance(raw, dict):
            raise ValueError(f"prompt dialects: unknown dialect {dialect_id!r}")
        parent_id = raw.get("extends")
        merged: dict[str, Any] = {}
        if parent_id is not None:
            parent = resolve(parent_id, (*seen, dialect_id))
            merged.update({k: v for k, v in parent.items() if k != "version"})
        merged.update({k: v for k, v in raw.items() if k != "extends"})
        resolved[dialect_id] = merged
        return merged

    return {dialect_id: _build(dialect_id, resolve(dialect_id, ())) for dialect_id in raw_all}


@lru_cache(maxsize=1)
def load_dialects() -> dict[str, PromptDialect]:
    return parse_dialects(yaml.safe_load(DIALECTS_FILE.read_text(encoding="utf-8")))


def get_dialect(dialect_id: str) -> PromptDialect:
    try:
        return load_dialects()[dialect_id]
    except KeyError:
        raise KeyError(f"unknown prompt dialect {dialect_id!r}") from None


def resolve_dialect_id(adapter_id: str, task_type: str, declared: Any) -> Optional[str]:
    """The dialect an adapter uses: its declaration, else the task-type default.

    Raises for an unknown id so a typo in adapter.yaml fails at startup rather than at
    the first request that happens to carry a reference.
    """
    if declared is None:
        return DEFAULT_BY_TASK_TYPE.get(task_type)
    if task_type not in DEFAULT_BY_TASK_TYPE:
        raise ValueError(f"{adapter_id}: prompt_dialect is declared for image/video/understand models only")
    if not isinstance(declared, str) or declared not in load_dialects():
        raise ValueError(
            f"{adapter_id}: unknown prompt_dialect {declared!r} (valid: {sorted(load_dialects())})"
        )
    return declared


#: Units a ``prompt_limits:`` block may bound (see ``ModelAdapter.prompt_limits``).
PROMPT_LIMIT_UNITS = frozenset({"chars", "zh_chars", "en_words"})


def parse_prompt_limits(adapter_id: str, raw: Any) -> Optional[dict[str, int]]:
    """Validate an adapter's ``prompt_limits:`` (``None`` / ``{}`` = no limit)."""
    if not raw:
        return None
    if not isinstance(raw, dict) or set(raw) - PROMPT_LIMIT_UNITS:
        raise ValueError(f"{adapter_id}: prompt_limits keys must be among {sorted(PROMPT_LIMIT_UNITS)}")
    for unit, value in raw.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{adapter_id}: prompt_limits.{unit} must be a positive integer")
    return dict(raw)
