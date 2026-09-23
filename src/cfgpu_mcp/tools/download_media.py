"""MCP-only media downloader with a runtime-injected request context."""

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service.media_download import download_media as download


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def download_media(
        url: Annotated[str, Field(min_length=1, max_length=8192, description="Original HTTP(S) media page or video/audio file link supplied by the user.")],
        ctx: Context,
        height: Annotated[int | None, Field(gt=0, strict=True, description="Exact video height in pixels, e.g. 480/720/1080; omit for best available.")] = None,
    ) -> dict:
        """Download existing video or audio from a user-provided link using yt-dlp.

        Use when the user asks to download/save a video or audio link, or a linked
        clip must become a local material for editing. Pass the original page or
        media URL. Prefer an existing material id when the file is already in the
        library. Use webpage tools for articles and generation tools for new content.

        Waits for completion and saves files under /mnt/user-data/outputs/. The
        Agent registers them and returns material_id values for later media tools.
        Call present_files with these ids or output paths to let the user view or
        download them; registration alone is not delivery. Only claim delivery
        after present_files succeeds. Do not poll task_status/task_wait for this tool.

        Selects best available audio/video by default. Set height for a requested
        resolution; matching is exact and fails if unavailable. Do not silently
        select another resolution or transcode. Supports completed replays; ongoing
        and upcoming live streams are rejected. A video URL with a playlist parameter
        downloads that video; an explicit playlist URL may return multiple files.
        Report partial_errors alongside successful files. Do not promise a site's
        availability before a successful download. For extraction/authentication
        errors, report the actual error without repeatedly retrying. A cookies
        message may also reflect site compatibility or anti-bot issues; cookies
        are not a guaranteed fix. Credentials are configured by the operator;
        never request cookies in chat.
        """
        try:
            request = ctx.request_context.request
            header = request.headers.get("X-CFGPU-Download-Context") if request is not None else None
            return await download(url, context_header=header, height=height)
        except Exception as exc:
            return tool_error_dict(exc)
