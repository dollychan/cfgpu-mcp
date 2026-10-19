"""Compile the ``templates/`` source tree into a bundle, and load a bundle back.

Compilation (``compile_source``) is the CI gate of design §6.5: schema, ``(id, version)``
uniqueness, file-name/identity agreement, facet vocabulary, no model dialect in content,
``verified_on`` naming real models, and — against the previously published bundle —
immutability of every published version (I-TL1). It is the only writer of the bundle;
the server has no write path at all (I-TL2).

Loading (``load_bundle``) re-validates every record and the bundle checksum instead of
trusting the file (I-TL7): a truncated or hand-edited bundle must fail loudly at load, not
answer searches with half a catalog.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from cfgpu_mcp.template_library.dialect_guard import find_dialect, markup_problems
from cfgpu_mcp.template_library.schema import (
    SCHEMA_VERSION,
    Catalog,
    TemplateRecord,
    canonical_json,
    immutable_hash,
)

BUNDLE_PATH = Path(__file__).parent / "templates.bundle.json"
RECORDS_DIRNAME = "records"
CATALOG_FILENAME = "catalog.yaml"


class BundleError(ValueError):
    """The source tree or a bundle failed validation; ``problems`` lists every finding."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        preview = "\n".join(f"  - {p}" for p in problems[:30])
        more = f"\n  … and {len(problems) - 30} more" if len(problems) > 30 else ""
        super().__init__(f"{len(problems)} template library problem(s):\n{preview}{more}")


@dataclass(frozen=True)
class Bundle:
    schema_version: int
    catalog_version: int
    catalog: Catalog
    records: tuple[TemplateRecord, ...]
    immutable_hashes: dict[tuple[str, int], str] = field(default_factory=dict)
    checksum: str = ""


def _record_filename(record: TemplateRecord) -> str:
    return f"{record.id}.v{record.version}.yaml"


def _bundle_checksum(catalog_version: int, records_json: list[dict[str, Any]]) -> str:
    payload = canonical_json({"catalog_version": catalog_version, "records": records_json})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sorted(records: Iterable[TemplateRecord]) -> list[TemplateRecord]:
    return sorted(records, key=lambda r: (r.id, r.version))


def compile_source(
    source_dir: Path,
    *,
    previous: Optional[Bundle] = None,
    known_models: Optional[set[str]] = None,
    catalog_path: Optional[Path] = None,
) -> Bundle:
    """Validate ``source_dir`` and return the bundle it compiles to.

    ``previous`` is the currently published bundle; every ``(id, version)`` in it must
    still exist with identical immutable fields (a withdrawn record is the only allowed
    change of content — to none). ``known_models`` is the set of public model names a
    record's ``verified_on`` may name; ``None`` skips that check. ``catalog_path``
    defaults to ``source_dir/catalog.yaml`` (drafts in ``_staging`` borrow the real one).
    """
    problems: list[str] = []
    catalog_path = catalog_path or source_dir / CATALOG_FILENAME
    try:
        catalog = Catalog.model_validate(yaml.safe_load(catalog_path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - reported as a finding, not a crash
        raise BundleError([f"{catalog_path}: {exc}"]) from exc

    records: dict[tuple[str, int], TemplateRecord] = {}
    record_files = sorted((source_dir / RECORDS_DIRNAME).glob("*/*.yaml"))
    for path in record_files:
        rel = path.relative_to(source_dir)
        try:
            record = TemplateRecord.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rel}: {exc}")
            continue
        if path.parent.name != record.kind:
            problems.append(f"{rel}: kind {record.kind!r} but stored under {path.parent.name}/")
        if path.name != _record_filename(record):
            problems.append(f"{rel}: file name must be {_record_filename(record)}")
        if record.key in records:
            problems.append(f"{rel}: duplicate (id, version) {record.id}@{record.version}")
            continue
        records[record.key] = record

        problems.extend(catalog.facets.check(record))
        texts = [("content", record.content or ""), ("title", record.title), ("summary", record.summary)]
        for field_name, text in texts:
            for hit in find_dialect(text):
                # content may carry the unified notation; markup_problems judges it below.
                if field_name == "content" and hit.rule == "unified-notation":
                    continue
                problems.append(f"{rel}: {field_name} carries model dialect ({hit.rule}): …{hit.excerpt}…")
        for problem in markup_problems(record.content or "", record.media, [s.name for s in record.slots]):
            problems.append(f"{rel}: content {problem}")
        if known_models is not None:
            unknown = sorted(set(record.verified_on) - known_models)
            if unknown:
                problems.append(f"{rel}: verified_on names unknown models {unknown}")

    if previous is not None:
        for key, old_hash in previous.immutable_hashes.items():
            new = records.get(key)
            if new is None:
                problems.append(
                    f"{key[0]}@{key[1]} was published and is now missing; withdraw it instead of deleting"
                )
            elif new.status != "withdrawn" and immutable_hash(new) != old_hash:
                problems.append(
                    f"{key[0]}@{key[1]}: published content changed; publish version {key[1] + 1} instead"
                )

    if problems:
        raise BundleError(problems)

    ordered = _sorted(records.values())
    return Bundle(
        schema_version=SCHEMA_VERSION,
        catalog_version=catalog.catalog_version,
        catalog=catalog,
        records=tuple(ordered),
        immutable_hashes={r.key: immutable_hash(r) for r in ordered},
        checksum=_bundle_checksum(catalog.catalog_version, [r.model_dump(mode="json") for r in ordered]),
    )


def dump_bundle(bundle: Bundle) -> str:
    records_json = [r.model_dump(mode="json") for r in bundle.records]
    data = {
        "schema_version": bundle.schema_version,
        "catalog_version": bundle.catalog_version,
        "facets": bundle.catalog.facets.model_dump(mode="json"),
        "checksum": bundle.checksum,
        "immutable_hashes": {f"{rid}@{ver}": h for (rid, ver), h in bundle.immutable_hashes.items()},
        "records": records_json,
    }
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def parse_bundle(text: str) -> Bundle:
    """Parse and fully re-validate a bundle; raise :class:`BundleError` on any mismatch."""
    raw = json.loads(text)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise BundleError([f"unsupported bundle schema_version {raw.get('schema_version')!r}"])
    catalog = Catalog.model_validate(
        {"schema_version": SCHEMA_VERSION, "catalog_version": raw["catalog_version"], "facets": raw["facets"]}
    )
    records = _sorted(TemplateRecord.model_validate(item) for item in raw["records"])
    checksum = _bundle_checksum(catalog.catalog_version, [r.model_dump(mode="json") for r in records])
    if checksum != raw.get("checksum"):
        raise BundleError(["bundle checksum does not match its records (corrupted or hand-edited)"])
    hashes: dict[tuple[str, int], str] = {}
    for key, value in raw.get("immutable_hashes", {}).items():
        rid, _, ver = key.rpartition("@")
        hashes[(rid, int(ver))] = value
    return Bundle(
        schema_version=SCHEMA_VERSION,
        catalog_version=catalog.catalog_version,
        catalog=catalog,
        records=tuple(records),
        immutable_hashes=hashes,
        checksum=checksum,
    )


def load_bundle(path: Path = BUNDLE_PATH) -> Bundle:
    return parse_bundle(path.read_text(encoding="utf-8"))
