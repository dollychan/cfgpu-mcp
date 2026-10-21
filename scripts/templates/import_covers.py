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
    python3 scripts/templates/import_covers.py minimax-h3 ~/claude/Public/MiniMax-H3
    python3 scripts/templates/import_covers.py evolink ~/claude/Public/awesome-gpt-image-2-API-Prompts \
        --max-edge 640 --quality 80

``minimax-h3`` covers video records, so they are animated: each GIF or MP4 is re-encoded
as an animated WebP (long edge ≤ ``--anim-max-edge``, MP4s resampled to ``--anim-fps``).
MP4s need an ``ffmpeg`` on PATH (or the ``imageio-ffmpeg`` package).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageSequence, UnidentifiedImageError

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

# Record -> file under MiniMax-H3 ``assets/``, shown whole as an animated cover; an image
# record instead gets one GIF frame as a still JPEG (``(file, frame index)``). The GIFs
# are the demo clips of the H3 genre skills the style/template records were rewritten
# from; the MP4s are the README case outputs. Left out on purpose:
# brand-promo-video-generator (a MiniMax Hub product promo, i.e. a real brand),
# minimalist-product-ad-generator (real consumer products), and case-video-h3-voice-edit
# (live-action footage of a real person).
H3_COVERS: dict[str, str | tuple[str, int]] = {
    "style-video-cartoon-3d-short": "3d-animation-short-generator.gif",
    "tpl-video-cartoon-3d-shot": "3d-animation-short-generator.gif",
    "tpl-image-coop-game-menu": ("co-op-game-intro-generator.gif", 18),
    "tpl-video-coop-game-intro-15s": "co-op-game-intro-generator.gif",
    "style-video-handdrawn-live-fusion": "handdrawn-live-video-generator.gif",
    "tpl-video-handdrawn-live-fusion-15s": "handdrawn-live-video-generator.gif",
    "style-video-mv-lyric-typography": "music-video-subtitle-generator.gif",
    "tpl-video-mv-shot-script": "music-video-subtitle-generator.gif",
    "style-video-paper-collage": "paper-collage-explainer-generator.gif",
    "tpl-video-paper-collage-assembly": "paper-collage-explainer-generator.gif",
    "style-video-papercraft-stop-motion": "papercraft-stop-motion-explainer.gif",
    "tpl-video-papercraft-i2v-5s": "papercraft-stop-motion-explainer.gif",
    "tpl-image-papercraft-keyframe": ("papercraft-stop-motion-explainer.gif", 6),
    "case-video-h3-fleet-jump": "t2va.mp4",
    "case-video-h3-ramen-focus-pull": "i2va.mp4",
}

#: A source file and its cover form: animated (``True``), the image itself (``False``),
#: or the still JPEG of one GIF frame (frame index).
Source = tuple[Path, bool | int]
Found = dict[str, tuple[Source, dict[str, Any]]]


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
            (src, False),
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
            (data / case["image"].lstrip("/"), False),
            {
                "origin": f"freestylefly/awesome-gpt-image-2 gallery case {case_id}",
                "author": case.get("sourceLabel"),
                "url": case.get("sourceUrl"),
            },
        )
    return found


def minimax_h3_covers(repo: Path) -> Found:
    found: Found = {}
    for record_id, spec in H3_COVERS.items():
        name, form = spec if isinstance(spec, tuple) else (spec, True)
        found[record_id] = (
            (repo / "assets" / name, form),
            {
                "origin": f"MiniMax-AI/MiniMax-H3 assets/{name}",
                "author": "MiniMax",
                "url": f"https://github.com/MiniMax-AI/MiniMax-H3/blob/main/assets/{name}",
            },
        )
    return found


def evolink_covers(repo: Path, records: dict[str, Path]) -> Found:
    """Each ``case-evolink-*`` record's first output image (``output.jpg``).

    The case list is parsed by ``import_sources.evolink_cases``, so a record and its
    cover come from one reading of the source files.
    """
    from import_sources import EVOLINK_ORIGIN, evolink_cases, evolink_record_id

    found: Found = {}
    for case in evolink_cases(repo):
        record_id = evolink_record_id(case)
        if record_id not in records:
            continue
        if not case["cover"]:
            print(f"no cover listed for {record_id}", file=sys.stderr)
            continue
        found[record_id] = (
            (repo / case["cover"], False),
            {"origin": EVOLINK_ORIGIN, "author": case["author"], "url": case["url"]},
        )
    return found


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit("an MP4 cover needs ffmpeg on PATH or the imageio-ffmpeg package")
    return imageio_ffmpeg.get_ffmpeg_exe()


def _frames(src: Path, max_edge: int, fps: int) -> tuple[list[Image.Image], list[int]]:
    """Frames (RGB, long edge ≤ ``max_edge``) and per-frame durations in ms."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    if src.suffix.lower() == ".gif":
        with Image.open(src) as img:
            for frame in ImageSequence.Iterator(img):
                durations.append(frame.info.get("duration") or 100)
                frames.append(frame.convert("RGB"))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [_ffmpeg(), "-v", "error", "-i", str(src), "-vf", f"fps={fps}", f"{tmp}/%05d.png"],
                check=True,
            )
            for png in sorted(Path(tmp).glob("*.png")):
                with Image.open(png) as img:
                    frames.append(img.convert("RGB"))
                durations.append(round(1000 / fps))
    for frame in frames:
        frame.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return frames, durations


def cover_key(record_id: str, version: int, src: Source) -> str:
    return f"{record_id}/{version}.{'webp' if src[1] is True else 'jpg'}"


def write_cover(src: Source, dest: Path, args: argparse.Namespace) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    path, form = src
    if form is True:
        frames, durations = _frames(path, args.anim_max_edge, args.anim_fps)
        frames[0].save(
            dest, "WEBP", save_all=True, append_images=frames[1:], duration=durations,
            loop=0, quality=args.anim_quality, method=6,
        )
        return
    with Image.open(path) as img:
        if form is not False:
            img.seek(form)
        img = img.convert("RGB")
        img.thumbnail((args.max_edge, args.max_edge), Image.Resampling.LANCZOS)
        img.save(dest, "JPEG", quality=args.quality, optimize=True, progressive=True)


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
    parser.add_argument("source", choices=["gpt-image2", "awesome-styles", "minimax-h3", "evolink"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-edge", type=int, default=1024)
    parser.add_argument("--quality", type=int, default=85)
    parser.add_argument("--anim-max-edge", type=int, default=480)
    parser.add_argument("--anim-fps", type=int, default=8)
    parser.add_argument("--anim-quality", type=int, default=70)
    args = parser.parse_args()

    records = latest_records()
    repo = args.path.expanduser()
    if args.source == "gpt-image2":
        found = gpt_image2_covers(repo, records)
    elif args.source == "awesome-styles":
        found = awesome_style_covers(repo)
    elif args.source == "evolink":
        found = evolink_covers(repo, records)
    else:
        found = minimax_h3_covers(repo)

    credits = load_credits()
    written = 0
    for record_id, (src, credit) in sorted(found.items()):
        if record_id not in records:
            print(f"skip {record_id}: no record", file=sys.stderr)
            continue
        if not src[0].is_file():
            print(f"skip {record_id}: missing image {src[0]}", file=sys.stderr)
            continue
        version = int(_RECORD_FILE.match(records[record_id].name)["version"])
        key = cover_key(record_id, version, src)
        try:
            write_cover(src, COVERS / key, args)
        except UnidentifiedImageError:  # e.g. an MP4 saved under a .jpg name upstream
            print(f"skip {record_id}: not a readable image {src[0]}", file=sys.stderr)
            continue
        # One cover per record version: drop a previous one in another format.
        for old in [k for k in credits if k.rpartition(".")[0] == f"{record_id}/{version}" and k != key]:
            del credits[old]
            (COVERS / old).unlink(missing_ok=True)
        credits[key] = {**credit, "usage": "reference-only"}
        written += 1
    save_credits(credits)
    print(f"wrote {written} covers to {COVERS.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
