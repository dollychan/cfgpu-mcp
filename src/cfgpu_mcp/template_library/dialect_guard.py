"""Detect model-specific prompt dialect in text that should be model-neutral.

The template library stores one model-neutral ``content`` per record; the server renders
it into each model's dialect at generation time. A record whose content already carries
one model's reference labels or shot syntax would be returned to the agent verbatim, and
the agent would learn to write that dialect for every model — the failure the design
exists to prevent (I-DL5 / I-TL5). The bundle builder therefore refuses such content.

The patterns are deliberately narrow: they target the exact spellings listed in the
design's dialect table, not general words. ``[商品名]``-style slot placeholders and
ordinary prose such as "the image on the left" must pass.

``[[`` is refused as well until the server-side renderer for the unified notation lands:
today ``generate_image`` treats ``[[...]]`` as a material handle, so a template carrying
one would fail at generation time.
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
    ("wan26-character-tag", re.compile(r"\bcharacter\d+\b")),
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


def find_dialect(text: str) -> list[DialectHit]:
    """Every dialect spelling found in ``text``, in rule order then position."""
    hits: list[DialectHit] = []
    for rule, pattern in _RULES:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 20)
            hits.append(DialectHit(rule, text[start : match.end() + 20].replace("\n", " ")))
    return hits
