"""Public read-only HTTP service for template covers (``cfgpu-mcp-public``).

Deliberately separate from the MCP streamable-HTTP app (``http_app.py``): that one binds
the caller's ``Authorization`` token and belongs on a private network, while this one
answers anonymous browsers. Sharing a process or middleware would put an
unauthenticated public surface next to token handling for no gain.

What it serves is fixed at startup: the cover paths of the shipped template bundle
(``covers/<id>/<version>.<jpg|webp|gif>``), each mapped to its file under
``public_http.covers_dir``; the Content-Type follows the extension.
A request is answered only when its path is exactly one of those keys, so there is no
path joining, no directory listing and no way to reach any other file. Everything else
is 404. A cover listed in the bundle whose file is missing is logged and left out.

Covers are third-party examples kept for reference only (``templates/covers/credits.yaml``).

With ``public_http.skill_roots`` set it also serves DeerFlow skill covers under
``/skill-covers/<alias>/<skill dir>/covers/<file>`` (§6.8). Those change whenever a user
publishes, so their whitelist is rebuilt by a periodic rescan (``SkillCoverIndex``) rather
than fixed at startup — but a request is still only an exact lookup in that dict.
``Cache-Control`` is a bounded ``max-age`` rather than ``immutable`` so that removing a
cover on a takedown request stops it being served from caches within a day.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from cfgpu_mcp.template_library.bundle import Bundle, load_bundle
from cfgpu_mcp.template_library.schema import COVER_MEDIA_TYPES

logger = logging.getLogger(__name__)

#: ``templates/covers`` in a source checkout (src/cfgpu_mcp/public_http.py → repo root).
DEFAULT_COVERS_DIR = Path(__file__).resolve().parents[2] / "templates" / "covers"
_COVERS_PREFIX = "covers/"

#: Skill cover types (I-TL8). Wider than template covers: a skill may ship a short video.
SKILL_COVER_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
_ALIAS = re.compile(r"^[a-z][a-z0-9_]*$")


def cover_files(bundle: Bundle, covers_dir: Path) -> dict[str, Path]:
    """Map each servable URL path (``covers/<id>/<version>.<ext>``) to its file."""
    files: dict[str, Path] = {}
    for record in bundle.records:
        if record.cover is None or record.status == "withdrawn":
            continue
        file = covers_dir / record.cover.path.removeprefix(_COVERS_PREFIX)
        if file.is_file():
            files[record.cover.path] = file
        else:
            logger.warning("cover %s listed in the bundle but missing under %s", record.cover.path, covers_dir)
    return files


class SkillCoverIndex:
    """The servable DeerFlow skill covers, ``{"<alias>/<rel path>": file}``.

    ``refresh`` walks every root and swaps in a fresh dict; readers only ever look a key up.
    What qualifies (I-TL8): a file directly inside a ``covers/`` directory whose parent
    holds a ``SKILL.md``, with a whitelisted extension, not a symlink, resolving inside its
    root, and not under ``evals/fixtures``. Below a skill dir only ``covers/`` is entered —
    the same package boundary DeerFlow's loader draws, and it keeps references / scripts
    out of the walk. SKILL.md content is never read: whether a cover is declared, and its
    size, is DeerFlow's upload validation, not this service's business.
    """

    def __init__(self, roots: dict[str, Path | str]) -> None:
        for alias in roots:
            if not _ALIAS.match(alias):
                raise ValueError(f"public_http.skill_roots alias {alias!r} must match {_ALIAS.pattern}")
        self.roots = {alias: Path(path).expanduser() for alias, path in roots.items()}
        self._by_root: dict[str, dict[str, Path]] = {alias: {} for alias in self.roots}
        self.files: dict[str, Path] = {}

    def refresh(self) -> None:
        by_root = dict(self._by_root)
        for alias, root in self.roots.items():
            try:
                by_root[alias] = _scan_skill_root(alias, root)
            except OSError as exc:
                # A shared-disk blip must not blank every card: keep the last good scan.
                logger.warning("skill cover root %s (%s) unreadable, keeping the previous scan: %s", alias, root, exc)
        self._by_root = by_root
        self.files = {key: file for files in by_root.values() for key, file in files.items()}


def _scan_skill_root(alias: str, root: Path) -> dict[str, Path]:
    base = root.resolve(strict=True)
    os.listdir(base)  # an unreadable root raises here instead of reading as empty

    def skipped(error: OSError) -> None:
        logger.warning("skill cover scan under %s skipped %s: %s", alias, error.filename, error)

    files: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(base, onerror=skipped):
        current = Path(dirpath)
        rel_dir = current.relative_to(base)
        parts = rel_dir.parts
        if any(parts[i : i + 2] == ("evals", "fixtures") for i in range(len(parts))):
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        if "SKILL.md" in filenames:
            dirnames[:] = [name for name in dirnames if name == "covers"]
        if current.name != "covers" or not (current.parent / "SKILL.md").is_file():
            continue
        for name in filenames:
            file = current / name
            if file.suffix.lower() not in SKILL_COVER_MEDIA_TYPES or file.is_symlink() or not file.is_file():
                continue
            if not file.resolve().is_relative_to(base):
                continue
            files[f"{alias}/{(rel_dir / name).as_posix()}"] = file
    return files


def build_public_app(
    files: dict[str, Path],
    *,
    cache_max_age: int = 86400,
    skill_covers: SkillCoverIndex | None = None,
    skill_rescan_interval: float = 60,
) -> Starlette:
    headers = {
        "Cache-Control": f"public, max-age={cache_max_age}",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    }

    async def cover(request: Request) -> Response:
        file = files.get(_COVERS_PREFIX + request.path_params["rest"])
        if file is None:
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(file, media_type=COVER_MEDIA_TYPES[file.suffix], headers=headers)

    async def skill_cover(request: Request) -> Response:
        file = skill_covers.files.get(request.path_params["rest"])
        # A file unpublished since the last scan is a 404, not a FileResponse error.
        if file is None or not file.is_file():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(file, media_type=SKILL_COVER_MEDIA_TYPES[file.suffix.lower()], headers=headers)

    async def healthz(request: Request) -> Response:
        text = f"ok {len(files)} covers"
        if skill_covers is not None:
            text += f", {len(skill_covers.files)} skill covers"
        return PlainTextResponse(text)

    routes = [
        Route("/covers/{rest:path}", cover, methods=["GET", "HEAD"]),
        Route("/healthz", healthz, methods=["GET", "HEAD"]),
    ]
    if skill_covers is None:
        return Starlette(routes=routes)

    async def rescan_forever() -> None:
        while True:
            await asyncio.sleep(skill_rescan_interval)
            try:
                await asyncio.to_thread(skill_covers.refresh)
            except Exception:  # keep serving the last scan; never let the loop die
                logger.exception("skill cover rescan failed")

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        task = asyncio.create_task(rescan_forever())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    routes.append(Route("/skill-covers/{rest:path}", skill_cover, methods=["GET", "HEAD"]))
    return Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    import os

    import uvicorn

    from cfgpu_mcp import config

    log_level = os.getenv("CFGPU_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.WARNING), force=True)

    settings = config.get_settings().public_http
    covers_dir = Path(settings.covers_dir).expanduser() if settings.covers_dir else DEFAULT_COVERS_DIR
    if not covers_dir.is_dir():
        raise SystemExit(f"public_http.covers_dir {covers_dir} does not exist")
    if not settings.public_base_url:
        logger.warning("public_http.public_base_url is unset: the MCP tools will return no cover URLs")
    files = cover_files(load_bundle(), covers_dir)
    skill_covers = None
    if settings.skill_roots:
        skill_covers = SkillCoverIndex(settings.skill_roots)
        skill_covers.refresh()  # serve from the first request, not after one interval
        logger.info("serving %d skill covers from %s", len(skill_covers.files), sorted(settings.skill_roots))
    app = build_public_app(
        files,
        cache_max_age=settings.cache_max_age,
        skill_covers=skill_covers,
        skill_rescan_interval=settings.skill_rescan_interval,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
