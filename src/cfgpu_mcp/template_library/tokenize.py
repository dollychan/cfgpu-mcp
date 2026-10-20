"""Tokenizer for template search: Latin words plus CJK character bigrams.

Chinese has no word separators, so a whole-query match finds nothing and a per-character
match finds everything; character bigrams are the standard index-free middle ground. The
algorithm follows DeerFlow's session search (``deerflow/agents/session/search.py``) but is
written out here rather than imported: this package must not depend on the host.

Folding is case- and width-insensitive (``ＡＢＣ`` == ``abc``) and length-preserving, so
a character whose folded form is longer than one character is left alone.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_right
from typing import Iterator


class _FoldTable(dict):
    def __missing__(self, key: int) -> str:
        folded = unicodedata.normalize("NFKC", chr(key)).casefold()
        value = folded if len(folded) == 1 else chr(key)
        self[key] = value
        return value


_FOLD_TABLE = _FoldTable()

_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0x2E80, 0x2EFF),
    (0x3040, 0x30FF),
    (0x3130, 0x318F),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_CJK_STARTS = tuple(start for start, _ in _CJK_RANGES)


def normalize(text: str) -> str:
    if text.isascii():
        return text.lower()
    return text.translate(_FOLD_TABLE)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    index = bisect_right(_CJK_STARTS, code) - 1
    if index < 0:
        return False
    start, end = _CJK_RANGES[index]
    return start <= code <= end


def iter_tokens(text: str) -> Iterator[str]:
    """Tokens of already-normalized ``text``. A one-character CJK run keeps the character."""
    length = len(text)
    index = 0
    while index < length:
        ch = text[index]
        if is_cjk(ch):
            end = index
            while end < length and is_cjk(text[end]):
                end += 1
            run = text[index:end]
            if len(run) == 1:
                yield run
            else:
                for offset in range(len(run) - 1):
                    yield run[offset : offset + 2]
            index = end
        elif ch.isalnum():
            end = index
            while end < length and text[end].isalnum() and not is_cjk(text[end]):
                end += 1
            yield text[index:end]
            index = end
        else:
            index += 1


def tokenize(text: str) -> list[str]:
    return list(iter_tokens(normalize(text)))


def query_terms(query: str) -> list[str]:
    """Distinct query tokens in first-seen order."""
    seen: list[str] = []
    for token in tokenize(query):
        if token not in seen:
            seen.append(token)
    return seen
