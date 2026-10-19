"""Public read-only HTTP service for template covers (``cfgpu-mcp-public``).

Deliberately separate from the MCP streamable-HTTP app (``http_app.py``): that one binds
the caller's ``Authorization`` token and belongs on a private network, while this one
answers anonymous browsers. Sharing a process or middleware would put an
unauthenticated public surface next to token handling for no gain.

What it serves is fixed at startup: the cover paths of the shipped template bundle
(``covers/<id>/<version>.jpg``), each mapped to its file under ``public_http.covers_dir``.
A request is answered only when its path is exactly one of those keys, so there is no
path joining, no directory listing and no way to reach any other file. Everything else
is 404. A cover listed in the bundle whose file is missing is logged and left out.

Covers are third-party examples kept for reference only (``templates/covers/credits.yaml``).
``Cache-Control`` is a bounded ``max-age`` rather than ``immutable`` so that removing a
cover on a takedown request stops it being served from caches within a day.
"""

from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from cfgpu_mcp.template_library.bundle import Bundle, load_bundle

logger = logging.getLogger(__name__)

#: ``templates/covers`` in a source checkout (src/cfgpu_mcp/public_http.py → repo root).
DEFAULT_COVERS_DIR = Path(__file__).resolve().parents[2] / "templates" / "covers"
_COVERS_PREFIX = "covers/"


def cover_files(bundle: Bundle, covers_dir: Path) -> dict[str, Path]:
    """Map each servable URL path (``covers/<id>/<version>.jpg``) to its file."""
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


def build_public_app(files: dict[str, Path], *, cache_max_age: int = 86400) -> Starlette:
    headers = {
        "Cache-Control": f"public, max-age={cache_max_age}",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    }

    async def cover(request: Request) -> Response:
        file = files.get(_COVERS_PREFIX + request.path_params["rest"])
        if file is None:
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(file, media_type="image/jpeg", headers=headers)

    async def healthz(request: Request) -> Response:
        return PlainTextResponse(f"ok {len(files)} covers")

    return Starlette(
        routes=[
            Route("/covers/{rest:path}", cover, methods=["GET", "HEAD"]),
            Route("/healthz", healthz, methods=["GET", "HEAD"]),
        ]
    )


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
    app = build_public_app(files, cache_max_age=settings.cache_max_age)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
