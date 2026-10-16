"""Record and catalog schema for the official template library.

One record is one immutable ``(id, version)`` of a prompt template, a worked case, or a
style direction. The source of truth is the git directory ``templates/`` (one YAML file
per record version, reviewed by PR); ``scripts/templates/build_bundle.py`` validates it
and compiles ``templates.bundle.json``, which is the only thing the server reads.

Two field groups behave differently on purpose:

* **Immutable once published** — ``kind``, ``media``, ``content``, ``slots``,
  ``dialect_variants``. A Production that recorded ``id + version`` must get the same
  text back forever, so changing any of these means publishing a new version. The bundle
  builder enforces it by hashing exactly these fields (:func:`immutable_hash`).
* **Editable metadata** — title, summary, facets, tags, rank, status. Retuning search or
  deprecating a record must not force a new version.

``content`` is written in the caller-facing notation (plain prose plus ``[slot]``
placeholders). It never carries a model-specific prompt dialect: those exist only inside
the server, and a record that contains one would teach the agent to imitate it.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

Kind = Literal["case", "style", "template"]
Media = Literal["image", "video", "audio"]
Status = Literal["active", "deprecated", "withdrawn"]

#: Licenses a record may be published under. A source whose license is not one of these
#: does not enter the library at all (I-TL4); "first-party" is content written in-house.
LICENSE_WHITELIST = frozenset({"MIT", "CC0-1.0", "CC-BY-4.0", "Apache-2.0", "first-party"})

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
_FACET = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")

#: The fields a published ``(id, version)`` may never change.
IMMUTABLE_FIELDS = ("kind", "media", "content", "slots", "dialect_variants")


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    required: bool = True
    hint: Optional[str] = Field(default=None, max_length=200)


class Cover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_key: str = Field(min_length=1)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=1)
    license: str
    author: Optional[str] = None
    url: Optional[str] = None

    @field_validator("license")
    @classmethod
    def _license_whitelisted(cls, v: str) -> str:
        if v not in LICENSE_WHITELIST:
            raise ValueError(f"license {v!r} is not in the whitelist {sorted(LICENSE_WHITELIST)}")
        return v


class TemplateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = Field(ge=1)
    status: Status = "active"
    rank: int = Field(default=1000, ge=0)
    kind: Kind
    media: Media
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=600)
    genres: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    content: Optional[str] = None
    withdrawn_reason: Optional[str] = None
    #: Vendor-tested renderings keyed by dialect id. Server-internal: never returned.
    dialect_variants: dict[str, str] = Field(default_factory=dict)
    verified_on: list[str] = Field(default_factory=list)
    cover: Optional[Cover] = None
    source: Source

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError(f"id {v!r} must match {_ID.pattern}")
        return v

    @field_validator("genres", "scenes", "styles")
    @classmethod
    def _facet_values(cls, v: list[str]) -> list[str]:
        bad = [item for item in v if not _FACET.match(item)]
        if bad:
            raise ValueError(f"facet values must be lowercase kebab-case, got {bad}")
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate facet values in {v}")
        return v

    @field_validator("tags", "verified_on")
    @classmethod
    def _unique_non_blank(cls, v: list[str]) -> list[str]:
        if any(not item.strip() for item in v):
            raise ValueError("entries must not be blank")
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate entries in {v}")
        return v

    @model_validator(mode="after")
    def _content_matches_status(self) -> "TemplateRecord":
        if self.status == "withdrawn":
            # A withdrawn record answers "this was taken down, here is why" and nothing
            # else: keeping the text would defeat a takedown done for licensing reasons.
            if self.content is not None or self.dialect_variants:
                raise ValueError("a withdrawn record must not carry content or dialect_variants")
            if not self.withdrawn_reason:
                raise ValueError("a withdrawn record needs withdrawn_reason")
        else:
            if not self.content or not self.content.strip():
                raise ValueError("an active or deprecated record needs content")
            if self.withdrawn_reason is not None:
                raise ValueError("withdrawn_reason is only for withdrawn records")
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate slot names in {names}")
        return self

    @property
    def key(self) -> tuple[str, int]:
        return (self.id, self.version)


class FacetVocabulary(BaseModel):
    """The closed set of facet values; a record may only use these (CI check)."""

    model_config = ConfigDict(extra="forbid")

    genres: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)

    def check(self, record: TemplateRecord) -> list[str]:
        problems = []
        for facet in ("genres", "scenes", "styles"):
            unknown = sorted(set(getattr(record, facet)) - set(getattr(self, facet)))
            if unknown:
                problems.append(f"{record.id}@{record.version}: {facet} not in catalog.yaml: {unknown}")
        return problems


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    #: Bumped when a published record is removed or changes meaning (I-TL1).
    catalog_version: int = Field(ge=1)
    facets: FacetVocabulary


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def immutable_hash(record: TemplateRecord) -> str:
    """Hash of the fields a published version may never change."""
    data = record.model_dump(mode="json", include=set(IMMUTABLE_FIELDS))
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
