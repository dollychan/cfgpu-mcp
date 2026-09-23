"""Call-scoped media downloads; no model adapter or background task ownership."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from anyio import CancelScope

from cfgpu_mcp import config
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.settings import MediaDownloadSettings

_semaphore: asyncio.Semaphore | None = None


def _thread_id(url: str, token: str | None) -> str:
    secret = os.getenv("CFGPU_DOWNLOAD_CONTEXT_SECRET")
    if not secret:
        raise CFGPUError("auth", "Media download context is not configured on this MCP host.")
    try:
        token = token or ""
        if len(token) > 2048:
            raise ValueError("context too large")
        body, signature = token.split(".")
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        scope = json.loads(base64.b64decode(body, altchars=b"-_", validate=True))
        thread_id = scope["thread_id"]
        issued = scope["issued_at"]
        if not isinstance(issued, int) or not -30 <= time.time() - issued <= 300:
            raise ValueError("expired context")
        if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread_id):
            raise ValueError("thread id")
        if scope["url_hash"] != hashlib.sha256(url.encode()).hexdigest():
            raise ValueError("URL binding")
        return thread_id
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise CFGPUError("auth", "A valid current-thread download context is required.") from exc


def _directories(home: str | None, thread_id: str) -> tuple[Path, Path]:
    if not home:
        raise CFGPUError("invalid_params", "Configure media_download.home_dir to the shared thread storage root.", card_hint=False)
    root = Path(home).expanduser().resolve()
    thread = root / "threads" / thread_id
    outputs = (thread / "user-data" / "outputs").resolve()
    staging = (thread / "user-data" / "workspace" / ".media-download").resolve()
    if thread.resolve() != thread or not all(p.is_relative_to(thread) for p in (outputs, staging)):
        raise CFGPUError("invalid_params", "Download directories must remain inside the current thread.", card_hint=False)
    outputs.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    return outputs, staging


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        # The worker has its own process group, including any FFmpeg descendants.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def _run_worker(url: str, directory: Path, settings: MediaDownloadSettings, height: int | None = None) -> dict:
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cfgpu_mcp.service.media_download",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **options,
    )
    try:
        payload = {"url": url, "height": height, "directory": str(directory), "settings": asdict(settings)}
        stdout, _stderr = await process.communicate(json.dumps(payload).encode())
        if process.returncode:
            raise CFGPUError("task_failed", "The media download process exited unexpectedly. Check the MCP installation.")
        return json.loads(stdout)
    except BaseException:
        # Shield cleanup from the request's cancellation scope.
        with CancelScope(shield=True):
            await _terminate(process)
        raise


def _publish(result: dict, staging: Path, outputs: Path) -> dict:
    ready = []
    seen = set()
    for item in result.get("files", []):
        source = Path(item["path"]).resolve()
        if not source.is_relative_to(staging) or not source.is_file() or source.stat().st_size == 0:
            raise CFGPUError("task_failed", "Downloader returned an invalid or incomplete output file.")
        if source not in seen:
            ready.append((source, item))
            seen.add(source)
    errors = [str(error)[:700] for error in result.get("errors", [])][:10]
    if not ready:
        raise CFGPUError("task_failed", "Media download failed: " + ("; ".join(errors) or "no downloadable media was found."))
    files = []
    published = []
    try:
        for source, item in ready:
            destination = outputs / f"download-{uuid.uuid4().hex[:12]}-{source.name.replace('#', '_')}"
            source.replace(destination)
            published.append(destination)
            files.append(
                {
                    "path": f"/mnt/user-data/outputs/{destination.name}",
                    "name": destination.name,
                    "title": str(item.get("title") or source.stem)[:300],
                    "size": destination.stat().st_size,
                    "mime_type": mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
                }
            )
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    response = {"status": "succeeded", "files": files}
    if errors:
        response["partial_errors"] = errors
    return response


async def download_media(url: str, *, context_header: str | None, height: int | None = None) -> dict:
    """Download a URL in the calling thread, returning only completed output files."""
    global _semaphore
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise CFGPUError("invalid_params", "Provide an HTTP(S) media page or media file URL without embedded credentials.", card_hint=False)
    thread_id = _thread_id(url, context_header)
    settings = config.get_settings().media_download
    outputs, scratch = _directories(settings.home_dir, thread_id)
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_parallel)
    try:
        async with asyncio.timeout(settings.timeout), _semaphore:
            with tempfile.TemporaryDirectory(prefix="download-", dir=scratch) as temporary:
                staging = Path(temporary).resolve()
                result = await _run_worker(url, staging, settings, height)
                return _publish(result, staging, outputs)
    except TimeoutError as exc:
        raise CFGPUError("timeout", "Media download exceeded the configured deadline and was stopped; incomplete files were removed.", retryable=True) from exc


def _download_in_worker(payload: dict) -> dict:
    from yt_dlp import YoutubeDL
    from yt_dlp.postprocessor.common import PostProcessor

    files: list[dict] = []
    errors: list[str] = []

    class Logger:
        def debug(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            if len(errors) < 10:
                errors.append(str(message)[:700])

    class CollectFile(PostProcessor):
        def run(self, info):
            files.append({"path": info["filepath"], "title": info.get("title")})
            return [], info

    def on_demand(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            reason = "Ongoing and upcoming live streams are not supported; provide an on-demand video or completed replay."
            if reason not in errors:
                errors.append(reason)
            return reason
        return None

    settings = payload["settings"]
    ffmpeg_location = settings.get("ffmpeg_location")
    binary_dir = ffmpeg_location
    if binary_dir and Path(binary_dir).is_file():
        binary_dir = str(Path(binary_dir).parent)
    if not all(shutil.which(binary, path=binary_dir) for binary in ("ffmpeg", "ffprobe")):
        raise RuntimeError("Install FFmpeg and ffprobe on the MCP host or configure media_download.ffmpeg_location.")

    height = payload.get("height")
    options = {
        "outtmpl": str(Path(payload["directory"]) / "%(title).100B [%(id)s].%(ext)s"),
        "format": f"bv*[height={height}]+ba/b[height={height}]" if height is not None else "bv*+ba/b",
        "windowsfilenames": True,
        "quiet": True,
        "noprogress": True,
        "logger": Logger(),
        "noplaylist": True,
        "ignoreerrors": "only_download",
        "match_filter": on_demand,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": False,
        "ffmpeg_location": ffmpeg_location,
        "cookiefile": settings.get("cookies_file"),
        "js_runtimes": {"deno": {"path": settings["deno_path"]} if settings.get("deno_path") else {}},
    }
    try:
        with contextlib.redirect_stdout(sys.stderr), YoutubeDL(options) as ydl:
            if options["cookiefile"]:
                # Load the operator's cookies into memory without saving changes back
                # into a credential file shared by concurrent download processes.
                _ = ydl.cookiejar
                ydl.params["cookiefile"] = None
            ydl.add_post_processor(CollectFile(ydl), when="after_move")
            ydl.download([payload["url"]])
    except Exception as exc:
        message = str(exc)[:700]
        if message not in errors:
            errors.append(message)
    return {"files": files, "errors": errors}


def _worker_main() -> None:
    try:
        result = _download_in_worker(json.load(sys.stdin))
    except ImportError:
        result = {"files": [], "errors": ["Install the cfgpu-mcp media extra in the MCP service's Python environment."]}
    except Exception as exc:
        result = {"files": [], "errors": [str(exc)[:700]]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    _worker_main()
