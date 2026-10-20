"""The unified prompt notation, parsed into model-neutral nodes.

A caller writes one closed vocabulary inside ``prompt`` and nothing model-specific
(docs/template-library-and-dialect.md §4.2): ``[[<handle>]]`` names a reference
material, and five reserved keywords structure a video prompt —

==========================  ============================================================
``[[shot 0-3]]``            a shot starts; seconds, decimals allowed (``[[shot 3.5-6]]``)
``[[say S1: 就是这里。]]``    spoken words, kept verbatim; speaker optional
                            (``[[say: …]]``), language optional (``[[say S1 lang=en: …]]``)
``[[text: 营业中]]``          text that must appear in the picture, verbatim
``[[sound: 雨声]]``           ambient sound
``[[music: 钢琴]]``           background music the characters cannot hear
==========================  ============================================================

This module only turns a prompt into nodes and checks what is model-independent
(syntax, shot order). How each node reads on a given model is the renderer's business
(``adapters/prompt_render.py``), and is never described to the caller (I-DL5) — so no
error raised here names a model or a model's own wording.

Reserved keywords win over handles: ``[[text: x]]`` is always on-screen text, and a
handle spelled like a keyword is refused when the handle lists are validated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

from cfgpu_mcp.adapters.regions import _PLACEHOLDER

#: The reserved keywords, lowercase. A handle may not be one of these.
KEYWORDS: frozenset[str] = frozenset({"shot", "say", "text", "sound", "music"})

#: ``say`` language codes → the name a dialect writes. Anything else is title-cased.
_LANG_CODES = {
    "zh": "Chinese",
    "cn": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "jp": "Japanese",
    "ko": "Korean",
    "yue": "Cantonese",
}

_HEAD = re.compile(r"^([A-Za-z]+)(?=$|[\s:：=])")
_NUM = r"(\d+(?:\.\d+)?)"
_SHOT = re.compile(rf"^shot\s+{_NUM}\s*[-–~]\s*{_NUM}\s*(?:s|秒)?$", re.IGNORECASE)
_SAY = re.compile(
    r"^say"
    r"(?:\s+(?P<speaker>S\d+(?:\s*,\s*S\d+)*))?"
    r"(?:\s+lang\s*=\s*(?P<lang>[A-Za-z][A-Za-z-]*))?"
    r"\s*[:：]\s*(?P<text>.*\S)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FIELD = re.compile(r"^(?P<kw>text|sound|music)\s*[:：]\s*(?P<text>.*\S)\s*$", re.IGNORECASE | re.DOTALL)


class MarkupError(ValueError):
    """A ``[[...]]`` that uses a reserved keyword but is not well formed."""


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Ref:
    """``[[<handle>]]`` — the token exactly as written, stripped."""

    token: str


@dataclass(frozen=True)
class Shot:
    start: Decimal
    end: Decimal
    index: int  # 1-based, in prompt order


@dataclass(frozen=True)
class Say:
    speaker: Optional[str]
    text: str
    lang: Optional[str]  # explicit language name, or None to let the dialect decide


@dataclass(frozen=True)
class OnScreen:
    text: str


@dataclass(frozen=True)
class Sound:
    text: str


@dataclass(frozen=True)
class Music:
    text: str


Node = Union[Text, Ref, Shot, Say, OnScreen, Sound, Music]

#: Node classes that come from a reserved keyword.
STRUCTURAL = (Shot, Say, OnScreen, Sound, Music)


def is_reserved(handle: str) -> bool:
    """True when ``handle`` would be read as a keyword rather than a handle."""
    match = _HEAD.match(handle.strip())
    return bool(match) and match.group(1).lower() in KEYWORDS


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:  # pragma: no cover - the regex admits digits only
        raise MarkupError(f"[[shot {raw}]] is not a number of seconds") from None


def _lang(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _LANG_CODES.get(raw.lower(), raw[:1].upper() + raw[1:].lower())


def _structural(token: str, keyword: str, shot_count: int) -> Node:
    if keyword == "shot":
        match = _SHOT.match(token)
        if not match:
            raise MarkupError(
                f"[[{token}]] is not a shot marker; write [[shot <start>-<end>]] in seconds, "
                f"e.g. [[shot 0-3]] or [[shot 3.5-6]]."
            )
        start, end = _decimal(match.group(1)), _decimal(match.group(2))
        if start >= end:
            raise MarkupError(f"[[{token}]] ends before it starts; the end must be later than the start.")
        return Shot(start, end, shot_count + 1)
    if keyword == "say":
        match = _SAY.match(token)
        if not match:
            raise MarkupError(
                f"[[{token}]] is not a dialogue marker; write [[say S1: <words>]] (speaker "
                f"optional: [[say: <words>]]), with a colon before the words."
            )
        speaker = re.sub(r"\s+", "", match.group("speaker")) if match.group("speaker") else None
        return Say(speaker, match.group("text").strip(), _lang(match.group("lang")))
    match = _FIELD.match(token)
    if not match:
        raise MarkupError(f"[[{token}]] needs a colon and some content, e.g. [[{keyword}: …]].")
    text = match.group("text").strip()
    return {"text": OnScreen, "sound": Sound, "music": Music}[keyword](text)


def parse(prompt: str) -> list[Node]:
    """Split ``prompt`` into nodes; raise :class:`MarkupError` on malformed markup.

    Also enforces the one model-independent rule about shots: they appear in time
    order and do not overlap. Whether the last one fits the video is per-model, since
    the duration a model resolves to differs.
    """
    nodes: list[Node] = []
    cursor = 0
    shots = 0
    previous: Optional[Shot] = None
    for match in _PLACEHOLDER.finditer(prompt):
        if match.start() > cursor:
            nodes.append(Text(prompt[cursor : match.start()]))
        cursor = match.end()
        token = match.group(1).strip()
        head = _HEAD.match(token)
        keyword = head.group(1).lower() if head else ""
        if keyword not in KEYWORDS:
            nodes.append(Ref(token))
            continue
        node = _structural(token, keyword, shots)
        if isinstance(node, Shot):
            if previous is not None and node.start < previous.end:
                raise MarkupError(
                    f"[[shot {_fmt(node.start)}-{_fmt(node.end)}]] starts before the previous "
                    f"shot ends ({_fmt(previous.end)}s); shots must be in time order and must "
                    f"not overlap."
                )
            shots += 1
            previous = node
        nodes.append(node)
    if cursor < len(prompt):
        nodes.append(Text(prompt[cursor:]))
    return nodes


def _fmt(value: Decimal) -> str:
    """Seconds as written for people: ``3``, ``3.5`` — never ``3.0`` or ``3.50``."""
    text = format(value.normalize(), "f")
    return text


def format_seconds(value: Decimal) -> str:
    return _fmt(value)


def format_mmss_ms(value: Decimal) -> str:
    """``00:03.500`` — minutes, seconds, milliseconds."""
    millis = int((value * 1000).to_integral_value())
    minutes, rest = divmod(millis, 60_000)
    return f"{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def detect_lang(text: str) -> str:
    """The language a line of dialogue is in, by script — deterministic, never guessed.

    Kana before Han: Japanese is written with both, Chinese with Han only.
    """
    if re.search(r"[぀-ヿ]", text):
        return "Japanese"
    if re.search(r"[가-힯ᄀ-ᇿ]", text):
        return "Korean"
    if re.search(r"[一-鿿㐀-䶿]", text):
        return "Chinese"
    return "English"


def has_structure(nodes: list[Node]) -> bool:
    return any(isinstance(node, STRUCTURAL) for node in nodes)


def shots(nodes: list[Node]) -> list[Shot]:
    return [node for node in nodes if isinstance(node, Shot)]
