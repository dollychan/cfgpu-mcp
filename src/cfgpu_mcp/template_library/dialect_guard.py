"""Detect model-specific prompt dialect in text that should be model-neutral.

The template library stores one model-neutral ``content`` per record; the server renders
it into each model's dialect at generation time. A record whose content already carries
one model's reference labels or shot syntax would be returned to the agent verbatim, and
the agent would learn to write that dialect for every model — the failure the design
exists to prevent (I-DL5 / I-TL5). The bundle builder therefore refuses such content.

The patterns are deliberately narrow: they target the exact spellings listed in the
design's dialect table, not general words. ``[商品名]``-style slot placeholders and
ordinary prose such as "the image on the left" must pass.

``[[`` is flagged as well (rule ``unified-notation``). In a record's ``content`` the
bundle builder replaces that rule with :func:`markup_problems`: a template may carry the
unified *structure* markers its media renders (``[[shot]]`` / ``[[say]]`` / ``[[text]]`` /
``[[sound]]`` / ``[[music]]`` on video, ``[[text]]`` on image), never a material handle —
a template names materials with ``[slot]`` placeholders that the caller fills.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class DialectHit(NamedTuple):
    rule: str
    excerpt: str


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("h3-reference-tag", re.compile(r"<(?:Picture|Video|Audio) ?\d+>")),
    ("kling-reference-tag", re.compile(r"<<<(?:image|video)_\d+>>>")),
    ("bracket-image-index", re.compile(r"\[(?:Image|Video|Audio) ?\d+\]")),
    # Not \b: Chinese characters are word characters, so "character1在" has no boundary.
    ("wan26-character-tag", re.compile(r"(?<![A-Za-z])character\d+(?!\d)")),
    # "参考图1" is the image-ordinal dialect; "地图1号线" is not.
    ("cn-ordinal-reference", re.compile(r"(?:图片|视频|音频|图像)\d+|(?<!地)图\d+")),
    ("at-reference", re.compile(r"@(?:图片|视频|音频|图像|Image|Video|Audio)\d+")),
    ("h3-shot-header", re.compile(r"\[Shot \d+\]")),
    ("h3-mode-header", re.compile(r"(?m)^(?:I2VA|FL2VA|L2VA|T2VA|Ref2VA)\b")),
    ("h3-field", re.compile(r"\b(?:integrated_multimodal_description|overall_soundscape|non_diegetic_music)\b")),
    ("h3-markup", re.compile(r"<(?:scenetrans|cutoff)>|<d>\[")),
    ("wan-multishot-header", re.compile(r"第\d+个镜头\s*\[")),
    ("seconds-range-header", re.compile(r"\[\d+(?:\.\d+)?\s*[-–~]\s*\d+(?:\.\d+)?\s*(?:秒|s)\]")),
    ("unified-notation", re.compile(r"\[\[")),
)


#: Structure markers each media's renderer accepts (``adapters/prompt_render.py``).
_MARKERS_BY_MEDIA = {
    "video": ("Shot", "Say", "OnScreen", "Sound", "Music"),
    "image": ("OnScreen",),
    "audio": (),
}


def markup_problems(content: str, media: str, slot_names: list[str]) -> list[str]:
    """Why ``content``'s ``[[...]]`` markers would not render, or ``[]``.

    Slots may sit inside a marker (``[[say S1: [台词1]]]``, ``[[shot [开始秒]-[结束秒]]]``),
    which is not parseable until the caller fills it. So each declared ``[slot]`` is first
    replaced with the next integer in reading order — a number is valid wherever a slot
    can stand inside a marker, and increasing numbers keep shots in time order — and the
    result is parsed with the same parser ``generate_*`` uses.
    """
    from cfgpu_mcp.adapters import prompt_markup as markup

    if "[[" not in content:
        return []
    names = sorted(slot_names, key=len, reverse=True)
    counter = iter(range(1, 10_000))
    filled = content
    if names:
        pattern = re.compile("|".join(re.escape(f"[{name}]") for name in names))
        filled = pattern.sub(lambda _m: str(next(counter)), content)
    try:
        nodes = markup.parse(filled)
    except markup.MarkupError as exc:
        return [f"malformed marker: {exc}"]
    # A "[[" the parser never saw as a marker — usually an undeclared [slot] inside one —
    # would reach the model as literal text once the caller fills the rest.
    parsed = sum(1 for node in nodes if type(node).__name__ != "Text")
    if filled.count("[[") != parsed:
        return ["malformed marker: a [[...]] does not parse (an undeclared [slot] inside it, or a stray bracket)"]
    allowed = _MARKERS_BY_MEDIA.get(media, ())
    problems = []
    for node in nodes:
        kind = type(node).__name__
        if kind == "Text":
            continue
        if kind == "Ref":
            problems.append(
                f"[[{node.token}]] is a material handle; a template names materials with a [slot] "
                f"placeholder instead (declare it under slots:)"
            )
        elif kind not in allowed:
            problems.append(f"{kind} markers are not rendered for {media} prompts")
    return problems


def find_dialect(text: str) -> list[DialectHit]:
    """Every dialect spelling found in ``text``, in rule order then position."""
    hits: list[DialectHit] = []
    for rule, pattern in _RULES:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 20)
            hits.append(DialectHit(rule, text[start : match.end() + 20].replace("\n", " ")))
    return hits
