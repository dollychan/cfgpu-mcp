"""Convert external template sources into draft records under ``templates/_staging/``.

Never writes ``templates/records/``: a draft is promoted only after review (license and
attribution checked, content free of model dialect — ``build_bundle.py --source
templates/_staging`` runs the same checks on the drafts). Re-running overwrites the drafts.

Sources:

* ``gpt-image2 <repo>``: wuyoscar/GPT-Image2-Skill (MIT). Reads
  ``skills/gpt-image/references/gallery-*.md``; one ``case`` record per numbered case.
* ``style-library <skill_dir>``: DeerFlow ``gpt-image-2-style-library`` (MIT). One
  ``style`` record per Template Index entry, one ``template`` record per ``text`` skeleton
  in ``references/templates/*.md``.

Usage::

    python scripts/templates/import_sources.py gpt-image2 ~/claude/Private/GPT-Image2-Skill
    python scripts/templates/import_sources.py style-library <deer-flow>/skills/public/cf-dream/image/gpt-image-2-style-library
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "templates" / "_staging"


def slug(text: str) -> str:
    text = text.lower().replace("&", " ").replace("/", " ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)

_FIELD_ORDER = (
    "id", "version", "status", "rank", "kind", "media", "title", "summary", "genres", "scenes",
    "styles", "tags", "slots", "verified_on", "source", "content",
)


def write_record(record: dict[str, Any]) -> Path:
    ordered = {k: record[k] for k in _FIELD_ORDER if k in record and record[k] not in (None, [], {})}
    path = STAGING / "records" / record["kind"] / f"{record['id']}.v{record['version']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(ordered, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10**9),
        encoding="utf-8",
    )
    return path


def first_sentence(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    match = re.search(r"(.+?[.!?。！？])(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


# ── GPT-Image2-Skill ──────────────────────────────────────────────────────────

_CASE_HEADING = re.compile(r"^### No\. (\d+) · (.+?)\s*$")
_META = re.compile(r"^- Metadata: (.+)$")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_GPT2_REPO = "https://github.com/wuyoscar/GPT-Image2-Skill"

# One genre vocabulary for every source. Gallery categories that are not subject-matter
# genres become tags instead; near-duplicate categories collapse onto one slug, or a
# genre filter would silently split the library in two.
_GALLERY_GENRE: dict[str, str | None] = {
    "edit-endpoint-showcase": None,
    "official-openai-cookbook-examples": None,
    "more-illustration-styles": "illustration",
}
_GALLERY_TAG: dict[str, str] = {
    "edit-endpoint-showcase": "image-edit",
    "official-openai-cookbook-examples": "openai-cookbook",
}


def _clean_title(title: str) -> str:
    title = re.sub(r"\(edit via `[^`]+`\)", "", title)
    title = re.sub(r"[^\w\s&'’,.:()/+→\-]", "", title)  # drop emoji such as 🆕
    return " ".join(title.split())


def import_gpt_image2(repo: Path) -> list[Path]:
    references = repo / "skills" / "gpt-image" / "references"
    written = []
    for md in sorted(references.glob("gallery-*.md")):
        lines = md.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            heading = _CASE_HEADING.match(lines[i])
            if not heading:
                i += 1
                continue
            number, raw_title = int(heading.group(1)), heading.group(2)
            meta = None
            content_lines: list[str] = []
            j = i + 1
            while j < len(lines) and not _CASE_HEADING.match(lines[j]):
                m = _META.match(lines[j])
                if m and meta is None:
                    meta = m.group(1)
                if lines[j].startswith("```text") and not content_lines:
                    j += 1
                    while j < len(lines) and lines[j].strip() != "```":
                        content_lines.append(lines[j])
                        j += 1
                j += 1
            i = j
            if meta is None or not content_lines:
                print(f"skip No. {number} in {md.name}: no metadata or prompt", file=sys.stderr)
                continue
            parts = [p.strip() for p in meta.split("·")]
            category = parts[0]
            ticks = [p.strip("`") for p in parts[1:] if p.startswith("`")]
            orientation = next((t for t in ticks if not re.match(r"^\d+x\d+$", t)), None)
            size = next((t for t in ticks if re.match(r"^\d+x\d+$", t)), None)
            rest = " · ".join(p for p in parts[1:] if not p.startswith("`"))
            author = None
            url = None
            author_m = re.search(r"Author: ([^·]+)", rest)
            if author_m:
                author = author_m.group(1).strip()
            link = _LINK.search(rest)
            if link:
                url = link.group(2)
            content = "\n".join(content_lines).strip()
            tags = [t for t in (orientation, size) if t]
            category_slug = slug(category)
            genre = _GALLERY_GENRE.get(category_slug, category_slug)
            if category_slug in _GALLERY_TAG:
                tags.append(_GALLERY_TAG[category_slug])
            record = {
                "id": f"case-gpt2-{number:04d}",
                "version": 1,
                "rank": number,
                "kind": "case",
                "media": "image",
                "title": _clean_title(raw_title),
                "summary": first_sentence(content),
                "genres": [genre] if genre else [],
                "tags": tags,
                "verified_on": ["cf-image-2"],
                "source": {
                    "origin": "wuyoscar/GPT-Image2-Skill",
                    "license": "MIT",
                    **({"author": author} if author else {}),
                    "url": url or f"{_GPT2_REPO}/blob/main/skills/gpt-image/references/{md.name}",
                },
                "content": content,
            }
            written.append(write_record(record))
    return written


# ── gpt-image-2-style-library ────────────────────────────────────────────────

_INDEX_HEADING = re.compile(r"^### (.+?) / (.+?)\s*$")
_FIELD = re.compile(r"^- (ID|Category|Styles|Scenes|Tags|Template source|Example cases): (.+)$")
_SLOT = re.compile(r"\[([^\[\]\n]{1,60})\]")
_STYLE_LIB_ORIGIN = "DeerFlow gpt-image-2-style-library (awesome-gpt-image-2)"

# Style-library categories mapped onto the gallery's genre slugs (same vocabulary).
_STYLE_CATEGORY_GENRES: dict[str, list[str]] = {
    "UI & Interfaces": ["ui-ux-mockups"],
    "Charts & Infographics": ["infographics-field-guides", "data-visualization"],
    "Posters & Typography": ["typography-posters"],
    "Products & E-commerce": ["product-food"],
    "Brand & Logos": ["brand-systems-identity"],
    "Architecture & Spaces": ["architecture-interior"],
    "Photography & Realism": ["photography"],
    "Illustration & Art": ["illustration"],
    "Characters & People": ["character-design"],
    "Scenes & Storytelling": ["scenes-storytelling"],
    "History & Classical Themes": ["history-classical"],
    "Documents & Publishing": ["documents-publishing"],
    "Other Use Cases": [],
}


def _genres_by_template_file(skill_dir: Path) -> dict[str, list[str]]:
    genres: dict[str, list[str]] = {}
    category = None
    for line in (skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines():
        m = _FIELD.match(line)
        if not m:
            continue
        if m.group(1) == "Category":
            category = m.group(2).strip()
        elif m.group(1) == "Template source" and category:
            file = re.search(r"\(references/templates/([^)]+)\)", m.group(2))
            if file:
                merged = genres.setdefault(file.group(1), [])
                merged.extend(g for g in _STYLE_CATEGORY_GENRES[category] if g not in merged)
    return genres


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _template_records(skill_dir: Path) -> dict[str, list[str]]:
    """Write one template record per ``text`` skeleton; return {file name: [ids]}."""
    by_file: dict[str, list[str]] = {}
    genres_by_file = _genres_by_template_file(skill_dir)
    for md in sorted((skill_dir / "references" / "templates").glob("*.md")):
        lines = md.read_text(encoding="utf-8").splitlines()
        section = next((l[4:].strip() for l in lines if l.startswith("### ")), md.stem)
        label = None
        n = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            bold = re.match(r"^\*\*(.+?)\*\*\s*$", line)
            if bold:
                label = bold.group(1).strip()
            if line.startswith("```text"):
                body: list[str] = []
                i += 1
                while i < len(lines) and lines[i].strip() != "```":
                    body.append(lines[i])
                    i += 1
                n += 1
                content = "\n".join(body).strip()
                slots = []
                for name in _SLOT.findall(content):
                    name = name.strip()
                    if name and name not in [s["name"] for s in slots]:
                        slots.append({"name": name, "required": True})
                template_id = f"tpl-style-{md.stem}-{n}"
                title_label = (label or "模板").replace("模板", "").strip() or "常规"
                record = {
                    "id": template_id,
                    "version": 1,
                    "rank": 500 + n,
                    "kind": "template",
                    "media": "image",
                    "title": f"{section} · {title_label}",
                    "summary": first_sentence(content.splitlines()[0] if content else ""),
                    "genres": genres_by_file.get(md.name, []),
                    "tags": [section, title_label],
                    "slots": slots,
                    "source": {"origin": _STYLE_LIB_ORIGIN, "license": "MIT"},
                    "content": content,
                }
                write_record(record)
                by_file.setdefault(md.name, []).append(template_id)
            i += 1
    return by_file


def import_style_library(skill_dir: Path) -> list[Path]:
    templates_by_file = _template_records(skill_dir)
    lines = (skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("## Template Index")
        end = lines.index("## Categories")
    except ValueError as exc:
        raise SystemExit(f"unexpected SKILL.md layout: {exc}") from exc
    written = []
    rank = 0
    i = start + 1
    while i < end:
        heading = _INDEX_HEADING.match(lines[i])
        if not heading:
            i += 1
            continue
        title_en, title_zh = heading.group(1), heading.group(2)
        fields: dict[str, str] = {}
        sections: dict[str, list[str]] = {"Use when": [], "Guidance": [], "Pitfalls": []}
        current = None
        j = i + 1
        while j < end and not _INDEX_HEADING.match(lines[j]):
            line = lines[j]
            m = _FIELD.match(line)
            if m:
                fields[m.group(1)] = m.group(2).strip().strip("`")
            elif line.rstrip(":").strip() in sections:
                current = line.rstrip(":").strip()
            elif current and line.strip().startswith("- "):
                sections[current].append(line.strip()[2:])
            j += 1
        i = j
        rank += 1
        template_file = re.search(r"\(references/templates/([^)]+)\)", fields.get("Template source", ""))
        skeletons = templates_by_file.get(template_file.group(1), []) if template_file else []
        use_when_zh = next((s[3:].strip() for s in sections["Use when"] if s.startswith("ZH:")), "")
        use_when_en = next((s[3:].strip() for s in sections["Use when"] if s.startswith("EN:")), "")
        body = [f"适用：{use_when_zh}" if use_when_zh else "", f"Use when: {use_when_en}" if use_when_en else ""]
        if sections["Guidance"]:
            body.append("要点 / Guidance:")
            body.extend(f"- {g}" for g in sections["Guidance"])
        if sections["Pitfalls"]:
            body.append("避坑 / Pitfalls:")
            body.extend(f"- {p}" for p in sections["Pitfalls"])
        if skeletons:
            body.append("可配合的模板骨架 / Skeletons: " + ", ".join(skeletons))
        record = {
            "id": f"style-{fields['ID']}",
            "version": 1,
            "rank": rank,
            "kind": "style",
            "media": "image",
            "title": f"{title_en} / {title_zh}",
            "summary": use_when_zh or use_when_en,
            "genres": _STYLE_CATEGORY_GENRES[fields["Category"]],
            "scenes": [slug(s) for s in _split_list(fields.get("Scenes", ""))],
            "styles": [slug(s) for s in _split_list(fields.get("Styles", ""))],
            "tags": _split_list(fields.get("Tags", "")),
            "source": {"origin": _STYLE_LIB_ORIGIN, "license": "MIT"},
            "content": "\n".join(line for line in body if line),
        }
        written.append(write_record(record))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", choices=["gpt-image2", "style-library"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.source == "gpt-image2":
        written = import_gpt_image2(args.path.expanduser())
    else:
        written = import_style_library(args.path.expanduser())
    print(f"wrote {len(written)} draft records under {STAGING.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
