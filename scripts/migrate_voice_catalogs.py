"""Generate ``voices.yaml`` catalogs from the platform's ``model_audio_voices`` export.

The export is the source of truth for system voices: every ``voices.yaml`` this writes is
a generated file, so to change a voice, change the export and re-run. Gender, age group
and description come straight from the export — nothing is inferred from voice names,
which got 4 of 24 guesses wrong when it was tried (``Portuguese_Rudolph`` is female).

    python scripts/migrate_voice_catalogs.py EXPORT.json           # write (refuses to overwrite)
    python scripts/migrate_voice_catalogs.py EXPORT.json --force   # regenerate
    python scripts/migrate_voice_catalogs.py --check               # validate every voices.yaml

Before overwriting, it prints which voice ids a regeneration adds and removes: a removed
id stops passing generate_audio's local voice check, so that list is worth reading.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cfgpu_mcp.voice_catalog import (  # noqa: E402
    VOICES_FILENAME,
    LanguageVocabulary,
    load_language_vocabulary,
    load_voice_catalog,
)

MODELS_DIR = ROOT / "src" / "cfgpu_mcp" / "models"

# Export vendor → the model directory that owns its catalog. MiniMax Turbo has no entry:
# it reads HD's catalog through ``extends``.
VENDOR_ADAPTERS = {
    "minimax": "minimax-speech-2-8-hd",
    "volcengine": "seed-tts-2-0",
}

# Each adapter's ``_DEFAULT_VOICE`` (audio_tts.py), marked ``default: true`` when the export
# lists it. Kept here rather than imported, because importing the adapter module loads the
# very catalogs this script writes; a unit test pins the two together.
DEFAULT_VOICES = {
    "minimax-speech-2-8-hd": "male-qn-qingse",
    "seed-tts-2-0": "zh_female_xiaohe_uranus_bigtts",
}

GENDERS = {"男": "male", "女": "female"}
AGE_GROUPS = {"儿童": "child", "青年": "young", "中年": "middle_aged", "老年": "senior"}

# Export spellings that are not caller-facing aliases in voice_languages.yaml.
RAW_LANGUAGES = {"中文 (普通话)": "zh", "中文 (粤语)": "yue"}

# An accent that names a regional variant becomes the language code; any other accent
# stays as display text in ``accent``. "标准口音" says nothing and is dropped.
ACCENT_LANGUAGES = {
    "英语-美音": "en-US",
    "英语-英音": "en-GB",
    "英语-英格兰口音": "en-GB",
    "英语-澳洲口音": "en-AU",
    "英语-印度口音": "en-IN",
}
NEUTRAL_ACCENTS = {"标准口音"}

# Variant tags that refine a base language (葡萄牙语 + 巴西葡萄牙语 → pt-BR).
VARIANT_TAGS = {"美式英语": "en-US", "巴西葡萄牙语": "pt-BR", "墨西哥西语": "es-MX"}

# Tags that say nothing a caller can select on: gender / age / language restate a field,
# platform "同款" markers and 通用场景 are marketing, S2S-SC is an internal code.
UNINFORMATIVE_TAGS = {"通用场景", "S2S-SC"}
# The export's tag spellings of RAW_LANGUAGES.
LANGUAGE_TAGS = {"中文-普通话", "中文-粤语"}


def _tags(raw: Optional[str]) -> list[str]:
    """The export stores tags as a JSON string; some entries hold several, comma-joined."""
    if not raw:
        return []
    return [part.strip() for tag in json.loads(raw) for part in tag.split(",") if part.strip()]


def languages_for(row: dict[str, Any], vocabulary: LanguageVocabulary) -> tuple[list[str], Optional[str]]:
    """Return ``(language codes, leftover accent text)`` for one export row."""
    raw = row["language"]
    base = RAW_LANGUAGES.get(raw) or vocabulary.resolve(raw)
    if base is None:
        raise ValueError(f"{row['voice_id']!r}: unmapped language {raw!r}; add it to voice_languages.yaml")

    accent = row.get("accent")
    variant = ACCENT_LANGUAGES.get(accent) if accent else None
    if variant is None:
        variant = next((VARIANT_TAGS[t] for t in _tags(row["tags"]) if t in VARIANT_TAGS), None)
    # A variant replaces its base only when it is that base's subtag; a stray tag on a
    # voice in another language must not relabel it.
    code = variant if variant and variant.startswith(base + "-") else base

    leftover = accent if accent and accent not in NEUTRAL_ACCENTS and accent not in ACCENT_LANGUAGES else None
    return [code], leftover


def selection_tags(row: dict[str, Any], vocabulary: LanguageVocabulary) -> list[str]:
    """Style traits from the name, then the export's scenario tags, minus restated fields."""
    name, _, traits = row["voice_name"].partition(" - ")
    tags = [t.strip() for t in traits.replace("，", ",").split(",") if t.strip()]
    for tag in _tags(row["tags"]):
        if (
            tag in GENDERS
            or tag in AGE_GROUPS
            or tag in UNINFORMATIVE_TAGS
            or tag in NEUTRAL_ACCENTS
            or tag in ACCENT_LANGUAGES
            or tag in VARIANT_TAGS
            or tag.endswith("同款")
            or tag in LANGUAGE_TAGS
            or vocabulary.resolve(tag) is not None
            or tag.startswith(("中国-", "英语-"))  # accents, already in `accent` / `languages`
        ):
            continue
        tags.append(tag)
    return list(dict.fromkeys(tags))


def _system_rows(export: list[dict[str, Any]], vendor: str) -> list[dict[str, Any]]:
    rows = [
        r for r in export
        if r["vendor_code"] == vendor and not r["is_delete"] and not r["is_custom"] and not r["user_id"]
    ]
    return sorted(rows, key=lambda r: (r["sort"], r["id"]))


def build_catalog(
    export: list[dict[str, Any]], vendor: str, vocabulary: LanguageVocabulary
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``(catalog, notes)``; ``notes`` maps voice id → a reason to look at it."""
    adapter_id = VENDOR_ADAPTERS[vendor]
    rows = _system_rows(export, vendor)
    if not rows:
        raise ValueError(f"export has no system voices for vendor {vendor!r}")

    notes: dict[str, str] = {}
    by_label: dict[tuple[str, str], list[str]] = defaultdict(list)
    voices: list[dict[str, Any]] = []
    for row in rows:
        voice_id = row["voice_id"]  # verbatim — never strip or normalise a handle
        if row["gender"] not in GENDERS:
            raise ValueError(f"{voice_id!r}: unmapped gender {row['gender']!r}")
        if row["age_group"] not in AGE_GROUPS:
            raise ValueError(f"{voice_id!r}: unmapped age_group {row['age_group']!r}")
        label = row["voice_name"].partition(" - ")[0].strip()
        languages, accent = languages_for(row, vocabulary)
        entry: dict[str, Any] = {
            "voice": voice_id,
            "label": label,
            "languages": languages,
            "gender": GENDERS[row["gender"]],
            "age": AGE_GROUPS[row["age_group"]],
            "accent": accent,
            "description": (row.get("description") or "").strip() or None,
            "tags": selection_tags(row, vocabulary),
        }
        if voice_id == DEFAULT_VOICES.get(adapter_id):
            entry["default"] = True
        voices.append(entry)
        by_label[(label, row["language"])].append(voice_id)

    # Two ids under one name in one language: a caller choosing by label cannot tell them
    # apart, and one of them is often a data-entry slip. Kept (the export is the source of
    # truth) but surfaced.
    for (label, _), ids in by_label.items():
        for voice_id in ids if len(ids) > 1 else []:
            others = ", ".join(i for i in ids if i != voice_id)
            notes[voice_id] = f"shares label {label!r} and language with {others}"

    latest = max(datetime.strptime(r["updated_at"], "%d/%m/%Y %H:%M:%S.%f") for r in rows)
    catalog = {
        "schema_version": 1,
        "source": f"model_audio_voices export, vendor {vendor}, updated {latest:%Y-%m-%d}",
        "voices": voices,
    }
    return catalog, notes


# ── rendering ───────────────────────────────────────────────────────────────

def _q(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar; it keeps every byte of the handle,
    # and quoting codes keeps YAML from reading Norwegian `no` as false.
    return json.dumps(value, ensure_ascii=False)


def _list(values: list[str]) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def render(catalog: dict[str, Any], notes: dict[str, str], adapter_id: str) -> str:
    lines = [
        f"# System voices for {adapter_id}. GENERATED by scripts/migrate_voice_catalogs.py from",
        "# the model_audio_voices export — do not edit by hand; change the export and re-run.",
        "schema_version: 1",
        f"source: {_q(catalog['source'])}",
        "voices:",
    ]
    for v in catalog["voices"]:
        note = f"  # NOTE: {notes[v['voice']]}" if v["voice"] in notes else ""
        lines += [
            f"  - voice: {_q(v['voice'])}{note}",
            f"    label: {_q(v['label'])}",
            f"    languages: {_list(v['languages'])}",
            f"    gender: {v['gender']}",
            f"    age: {v['age']}",
        ]
        if v["accent"]:
            lines.append(f"    accent: {_q(v['accent'])}")
        if v["description"]:
            lines.append(f"    description: {_q(v['description'])}")
        lines.append(f"    tags: {_list(v['tags'])}")
        if v.get("default"):
            lines.append("    default: true")
    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────

def _check() -> int:
    vocabulary = load_language_vocabulary()
    paths = sorted(MODELS_DIR.glob(f"*/{VOICES_FILENAME}"))
    if not paths:
        print("no voices.yaml found", file=sys.stderr)
        return 1
    failed = 0
    for path in paths:
        try:
            catalog = load_voice_catalog(path, vocabulary)
            print(f"ok    {path.relative_to(ROOT)} ({len(catalog.voices)} voices)")
        except Exception as e:  # report every file, not just the first failure
            failed += 1
            print(f"FAIL  {path.relative_to(ROOT)}\n{e}", file=sys.stderr)
    return 1 if failed else 0


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {v["voice"] for v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("voices", [])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("export", nargs="?", type=Path, help="model_audio_voices JSON export")
    parser.add_argument("--force", action="store_true", help="overwrite existing voices.yaml")
    parser.add_argument("--check", action="store_true", help="validate existing voices.yaml and exit")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if args.export is None:
        parser.error("an export file is required unless --check is given")

    export = json.loads(args.export.read_text(encoding="utf-8"))
    vocabulary = load_language_vocabulary()
    for vendor, adapter_id in VENDOR_ADAPTERS.items():
        target = MODELS_DIR / adapter_id / VOICES_FILENAME
        catalog, notes = build_catalog(export, vendor, vocabulary)
        before, after = _existing_ids(target), {v["voice"] for v in catalog["voices"]}
        print(
            f"{adapter_id}: {len(after)} voices "
            f"(+{len(after - before)} / -{len(before - after)} vs current)"
        )
        for voice_id in sorted(before - after):
            print(f"      removed {voice_id!r}")
        for voice_id, note in notes.items():
            print(f"      NOTE {voice_id!r}: {note}")
        if not any(v.get("default") for v in catalog["voices"]):
            print(f"      default voice {DEFAULT_VOICES[adapter_id]!r} is not in the export")
        if target.exists() and not args.force:
            print(f"      skip: {target.relative_to(ROOT)} exists (use --force)", file=sys.stderr)
            continue
        target.write_text(render(catalog, notes, adapter_id), encoding="utf-8")
        load_voice_catalog(target, vocabulary)  # never leave an invalid file behind unnoticed
        print(f"      wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
