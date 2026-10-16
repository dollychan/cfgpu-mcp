"""In-memory search index over one loaded bundle (design §6.6).

Built once per bundle, read-only afterwards. ``search`` is filter-then-score:

1. facets (``kind`` / ``media`` / ``genres`` / ``scenes`` / ``styles``) narrow the
   candidate set; only ``active`` records are candidates;
2. with a query, candidates are scored with BM25 over weighted fields plus a phrase
   bonus; with no query it is browse mode, ordered by the curated ``rank``;
3. a ``model`` boosts records verified on that model without excluding the rest — the
   content is model-neutral, so every record can be rendered for every model;
4. ties break on ``rank`` then ``id``, so the same bundle and query always return the
   same order (I-TL6).

The result never carries ``dialect_variants`` or any dialect id (I-DL5).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from cfgpu_mcp.template_library.bundle import Bundle
from cfgpu_mcp.template_library.schema import TemplateRecord
from cfgpu_mcp.template_library.tokenize import is_cjk, normalize, query_terms, tokenize

#: Field weights. ``content`` is indexed at a low weight: titles are short, and a user
#: asking for "大教堂" should find the case whose prompt is about a cathedral interior.
FIELD_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "tags": 2.0,
    "summary": 1.0,
    "facets": 1.0,
    "content": 0.5,
}
_K1 = 1.2
_B = 0.75
_PHRASE_BONUS = 1.5
_PHRASE_FLOOR = 0.5
_MODEL_BOOST = 1.25
FACETS = ("genres", "scenes", "styles")
MAX_LIMIT = 20

# Fields returned on a search card. Kept small: a card is for choosing, get_template is
# for reading.
_CARD_FIELDS = ("id", "version", "kind", "media", "title", "summary", "genres", "scenes", "styles", "tags", "verified_on")


def _record_fields(record: TemplateRecord) -> dict[str, str]:
    return {
        "title": record.title,
        "tags": " ".join(record.tags),
        "summary": record.summary,
        "facets": " ".join([*record.genres, *record.scenes, *record.styles]).replace("-", " "),
        "content": record.content or "",
    }


@dataclass
class _Doc:
    record: TemplateRecord
    tf: dict[str, float]
    length: float
    phrase_text: str


class TemplateIndex:
    def __init__(self, bundle: Bundle):
        self.bundle = bundle
        self.catalog_version = bundle.catalog_version
        self._by_key: dict[tuple[str, int], TemplateRecord] = {r.key: r for r in bundle.records}
        self._latest_active: dict[str, TemplateRecord] = {}
        self._latest_any: dict[str, TemplateRecord] = {}
        for record in bundle.records:  # sorted by (id, version): later wins
            self._latest_any[record.id] = record
            if record.status == "active":
                self._latest_active[record.id] = record

        # Only the latest active version of each id is searchable; older versions stay
        # reachable through get_template(id, version).
        self._docs: list[_Doc] = []
        df: Counter[str] = Counter()
        for record in sorted(self._latest_active.values(), key=lambda r: r.id):
            tf: dict[str, float] = defaultdict(float)
            length = 0.0
            for name, text in _record_fields(record).items():
                weight = FIELD_WEIGHTS[name]
                for token in tokenize(text):
                    tf[token] += weight
                    length += weight
            df.update(tf.keys())
            phrase_text = normalize(" ".join([record.title, record.summary, " ".join(record.tags)]))
            self._docs.append(_Doc(record, dict(tf), length, phrase_text))
        self._df = df
        self._n = len(self._docs)
        self._avg_len = (sum(d.length for d in self._docs) / self._n) if self._n else 1.0

    # ── facets ────────────────────────────────────────────────────────────────

    @property
    def facets(self) -> dict[str, list[str]]:
        return self.bundle.catalog.facets.model_dump()

    def _check_facets(self, requested: dict[str, Optional[list[str]]]) -> None:
        vocab = self.facets
        for facet, values in requested.items():
            unknown = sorted(set(values or []) - set(vocab[facet]))
            if unknown:
                raise ValueError(f"unknown {facet} {unknown}; valid values: {vocab[facet]}")

    # ── search ────────────────────────────────────────────────────────────────

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def _bm25(self, doc: _Doc, terms: list[str]) -> tuple[float, set[str]]:
        score = 0.0
        matched: set[str] = set()
        norm = _K1 * (1 - _B + _B * doc.length / self._avg_len)
        for term in terms:
            if len(term) == 1 and is_cjk(term):
                # A lone CJK character never appears as a bigram token; match it by
                # substring so a one-character query still works.
                if term in doc.phrase_text or term in normalize(doc.record.content or ""):
                    matched.add(term)
                    score += self._idf(term) * 0.5
                continue
            tf = doc.tf.get(term)
            if tf:
                matched.add(term)
                score += self._idf(term) * tf * (_K1 + 1) / (tf + norm)
        return score, matched

    def search(
        self,
        *,
        query: Optional[str] = None,
        kind: Optional[str] = None,
        media: Optional[str] = None,
        genres: Optional[list[str]] = None,
        scenes: Optional[list[str]] = None,
        styles: Optional[list[str]] = None,
        model: Optional[str] = None,
        limit: int = 10,
        cursor: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        if cursor < 0:
            raise ValueError("cursor must be >= 0")
        requested = {"genres": genres, "scenes": scenes, "styles": styles}
        self._check_facets(requested)

        candidates = []
        for doc in self._docs:
            record = doc.record
            if kind and record.kind != kind:
                continue
            if media and record.media != media:
                continue
            # Within one facet any value matches; across facets every facet must match.
            if any(values and not set(values) & set(getattr(record, facet)) for facet, values in requested.items()):
                continue
            candidates.append(doc)

        terms = query_terms(query) if query and query.strip() else []
        phrase = normalize(query.strip()) if query else ""
        scored: list[tuple[float, TemplateRecord]] = []
        matched_any: set[str] = set()
        for doc in candidates:
            verified = bool(model) and model in doc.record.verified_on
            if terms:
                score, matched = self._bm25(doc, terms)
                if len(phrase) >= 2 and phrase in doc.phrase_text:
                    score = max(score * _PHRASE_BONUS, _PHRASE_FLOOR)
                if score <= 0:
                    continue
                matched_any |= matched
                if verified:
                    score *= _MODEL_BOOST
            else:
                score = 1.0 if verified else 0.0
            scored.append((score, doc.record))

        scored.sort(key=lambda item: (-round(item[0], 9), item[1].rank, item[1].id))
        total = len(scored)
        page = scored[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < total else None
        return {
            "available": True,
            "catalog_version": self.catalog_version,
            "mode": "search" if terms else "browse",
            "total": total,
            "next_cursor": next_cursor,
            "matched_terms": [t for t in terms if t in matched_any],
            "missing_terms": [t for t in terms if t not in matched_any],
            "results": [self._card(record, score if terms else None) for score, record in page],
        }

    @staticmethod
    def _card(record: TemplateRecord, score: Optional[float]) -> dict[str, Any]:
        card = record.model_dump(mode="json", include=set(_CARD_FIELDS))
        card["cover"] = record.cover.object_key if record.cover else None
        card["source"] = {"author": record.source.author, "origin": record.source.origin}
        if score is not None:
            card["score"] = round(score, 4)
        return card

    # ── get ───────────────────────────────────────────────────────────────────

    def get(self, template_id: str, version: Optional[int] = None) -> dict[str, Any]:
        if version is None:
            record = self._latest_active.get(template_id) or self._latest_any.get(template_id)
        else:
            record = self._by_key.get((template_id, version))
        if record is None:
            where = f"{template_id}@{version}" if version is not None else template_id
            raise ValueError(f"no template {where!r}; use an id exactly as a search result returned it")
        if record.status == "withdrawn":
            return {
                "id": record.id,
                "version": record.version,
                "status": "withdrawn",
                "withdrawn_reason": record.withdrawn_reason,
                "catalog_version": self.catalog_version,
            }
        data = record.model_dump(mode="json", exclude={"dialect_variants", "withdrawn_reason"})
        data["cover"] = record.cover.object_key if record.cover else None
        data["catalog_version"] = self.catalog_version
        return data
