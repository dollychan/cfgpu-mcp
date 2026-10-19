"""Copy cover images into ``templates/covers/`` and record their credits (design §6.1, Q-TL4).

Covers are stored as ``templates/covers/<id>/<version>.jpg``, the same layout as their
OSS object key ``templates/public/covers/<id>/<version>.jpg``. They are card thumbnails,
not originals: each image is downscaled to at most ``--max-edge`` pixels on the long edge
and re-encoded as JPEG, so the directory stays small enough to live in git. The upstream
originals stay in their source repository.

Every cover so far is a third-party example image: the source repositories are MIT, but
the images themselves belong to their original authors. They are shown only as a reference
for what a record can produce, never as material the library grants. Each one gets an
entry in ``templates/covers/credits.yaml`` naming where it came from and who made it.

Only covers whose record exists under ``templates/records/`` are written; a cover is tied
to the record version it was imported for (the latest one at import time). Needs Pillow,
which is not a project dependency — run it with any Python that has it:

    python3 scripts/templates/import_covers.py gpt-image2 ~/claude/Private/GPT-Image2-Skill
    python3 scripts/templates/import_covers.py awesome-styles ~/claude/Public/awesome-gpt-image-2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDS = REPO_ROOT / "templates" / "records"
COVERS = REPO_ROOT / "templates" / "covers"
CREDITS = COVERS / "credits.yaml"

CREDITS_NOTICE = (
    "封面均为第三方示例图，只作参考，用来展示一条记录大致能产出什么。版权归各条目注明的原作者；"
    "来源仓库的许可证只覆盖其文本，不覆盖这些图片。不得把封面当作生成素材，也不得作为本库自有作品再分发；"
    "原作者要求下架时，删除对应条目和文件。"
    " / Cover images are third-party examples shown for reference only. Copyright stays with the "
    "original author named in each entry; the source repository's license covers its text, not "
    "these images. Never use a cover as generation material or redistribute it as the library's "
    "own work; on a takedown request, delete the entry and its file."
)

_CASE_HEADING = re.compile(r"^### No\. (\d+) · ")
_IMAGE_LINE = re.compile(r"^- Image: `([^`]+)`")
_LIST_IMAGE = re.compile(r"^  - `([^`]+)`")
_RECORD_FILE = re.compile(r"^(?P<id>.+)\.v(?P<version>\d+)\.yaml$")

# Style record -> awesome-gpt-image-2 gallery case used as its cover. Picked from the
# style-library's "Example cases" by looking at each image. Styles left out on purpose,
# because every listed example shows a real person or a real brand:
#   ui-screenshot-system (Meta product ad / @OpenAI post / Trump), sports-campaign-poster
#   (real footballers, club crests), ink-double-exposure-poster (real footballer),
#   personalized-beauty-report (YSL branding), realistic-photography (edit of an uploaded
#   real-person photo), 3d-collectible-toy (Trump).
# poster-layout-system uses case 5, not its first example 345 (a real film and actress).
AWESOME_STYLE_COVERS: dict[str, int] = {
    "style-infographic-engine": 334,
    "style-scientific-scale-diagram": 341,
    "style-poster-layout-system": 5,
    "style-conceptual-typography-poster": 355,
    "style-nature-science-poster": 339,
    "style-product-commerce-visual": 373,
    "style-brand-identity-package": 354,
    "style-brand-touchpoint-board": 362,
    "style-architecture-space": 331,
    "style-street-accident-moment": 376,
    "style-illustration-art-style": 346,
    "style-character-design-sheet": 347,
    "style-scene-storytelling": 330,
    "style-history-classical-themes": 375,
    "style-document-publishing": 360,
    "style-concept-product-breakdown": 370,
}

Found = dict[str, tuple[Path, dict[str, Any]]]


def latest_records() -> dict[str, Path]:
    latest: dict[str, tuple[int, Path]] = {}
    for path in RECORDS.glob("*/*.yaml"):
        m = _RECORD_FILE.match(path.name)
        if m and int(m["version"]) > latest.get(m["id"], (0, path))[0]:
            latest[m["id"]] = (int(m["version"]), path)
    return {record_id: path for record_id, (_, path) in latest.items()}


def gpt_image2_covers(repo: Path, records: dict[str, Path]) -> Found:
    """Map ``case-gpt2-NNNN`` to the image listed under its gallery heading.

    A case shows either one ``- Image:`` line or an ``- Images:`` list; the list form is
    used by edit cases (input first, result last), so the last entry is the cover.
    """
    images: dict[str, Path] = {}
    for md in sorted(
        (repo / "skills" / "gpt-image" / "references").glob("gallery-*.md")
    ):
        current = None
        in_list = False
        for line in md.read_text(encoding="utf-8").splitlines():
            heading = _CASE_HEADING.match(line)
            if heading:
                current = f"case-gpt2-{int(heading.group(1)):04d}"
                in_list = False
                continue
            if current is None:
                continue
            image = _IMAGE_LINE.match(line)
            if image:
                images.setdefault(current, repo / image.group(1))
            elif line.startswith("- Images:"):
                in_list = True
            elif in_list and (item := _LIST_IMAGE.match(line)):
                images[current] = repo / item.group(1)
            elif in_list and line.startswith("- "):
                in_list = False

    found: Found = {}
    for record_id, src in images.items():
        if record_id not in records:
            continue
        source = yaml.safe_load(records[record_id].read_text(encoding="utf-8"))[
            "source"
        ]
        found[record_id] = (
            src,
            {
                "origin": "wuyoscar/GPT-Image2-Skill",
                "author": source.get("author"),
                "url": source.get("url"),
            },
        )
    for record_id in sorted(
        i for i in records if i.startswith("case-gpt2-") and i not in images
    ):
        print(f"no cover listed for {record_id}", file=sys.stderr)
    return found


def awesome_style_covers(repo: Path) -> Found:
    data = repo / "data"
    cases = {
        c["id"]: c
        for c in json.loads((data / "cases.json").read_text(encoding="utf-8"))["cases"]
    }
    found: Found = {}
    for record_id, case_id in AWESOME_STYLE_COVERS.items():
        case = cases[case_id]
        found[record_id] = (
            data / case["image"].lstrip("/"),
            {
                "origin": f"freestylefly/awesome-gpt-image-2 gallery case {case_id}",
                "author": case.get("sourceLabel"),
                "url": case.get("sourceUrl"),
            },
        )
    return found


def write_cover(src: Path, dest: Path, max_edge: int, quality: int) -> None:
    with Image.open(src) as img:
        img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)


def load_credits() -> dict[str, Any]:
    if CREDITS.exists():
        return yaml.safe_load(CREDITS.read_text(encoding="utf-8")).get("covers") or {}
    return {}


def save_credits(covers: dict[str, Any]) -> None:
    body = {"notice": CREDITS_NOTICE, "covers": dict(sorted(covers.items()))}
    CREDITS.write_text(
        yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", choices=["gpt-image2", "awesome-styles"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-edge", type=int, default=1024)
    parser.add_argument("--quality", type=int, default=85)
    args = parser.parse_args()

    records = latest_records()
    repo = args.path.expanduser()
    if args.source == "gpt-image2":
        found = gpt_image2_covers(repo, records)
    else:
        found = awesome_style_covers(repo)

    credits = load_credits()
    written = 0
    for record_id, (src, credit) in sorted(found.items()):
        if record_id not in records:
            print(f"skip {record_id}: no record", file=sys.stderr)
            continue
        if not src.is_file():
            print(f"skip {record_id}: missing image {src}", file=sys.stderr)
            continue
        version = int(_RECORD_FILE.match(records[record_id].name)["version"])
        key = f"{record_id}/{version}.jpg"
        write_cover(src, COVERS / key, args.max_edge, args.quality)
        credits[key] = {**credit, "usage": "reference-only"}
        written += 1
    save_credits(credits)
    print(f"wrote {written} covers to {COVERS.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
