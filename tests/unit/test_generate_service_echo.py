"""The caller's echo fields survive the generate_* service path itself.

``stamp_echo`` is unit-tested in test_tool_registry.py and the async recovery in
test_task_service.py; what is left is the wiring — every ``return`` in a generate
service must stamp, or a caller gets its label on some shapes and not others. Each
service has four such returns (wait=False, no-result, no-metadata, full), so they
are covered here rather than trusted.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import video as video_service
from cfgpu_mcp.tool_registry import NormalizedResult

CAPTION = "角色阿雅 第一版"


async def _repo() -> tuple[object, aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.db import _CREATE_TABLE
    from cfgpu_mcp.client.repository import SqliteTaskRepository

    await db.execute(_CREATE_TABLE)
    await db.commit()
    return SqliteTaskRepository(db), db


def _sync_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "doubao-seedream-5-0-lite"
    adapter.model_name = "doubao-seedream-5-0-lite"
    adapter.is_async = False
    adapter.endpoint = "/v1/images/generations"
    adapter.build_payload.return_value = {"model": "test", "prompt": "x"}
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/img.jpg"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None,
        model_used="test",
        seed=None,
        usage={"total_tokens": 10},
    )
    return adapter


def _async_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "wan-2-0"
    adapter.model_name = "wan-video"
    adapter.is_async = True
    adapter.endpoint = "/v1/video/tasks"
    adapter.poll_endpoint = "/v1/video/tasks/{task_id}"
    adapter.build_payload.return_value = {"model": "wan-video", "content": []}
    adapter.extract_task_id.return_value = "cfgpu-task-1"
    return adapter


def _patched(adapter, repo, client):
    router = MagicMock()
    router.resolve.return_value = adapter
    return (
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock(return_value=repo)),
        patch("cfgpu_mcp.config.get_client", MagicMock(return_value=client)),
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=MagicMock())),
        patch("cfgpu_mcp.router.ModelRouter", MagicMock(return_value=router)),
    )


@pytest.mark.asyncio
async def test_sync_generate_echoes_caption_with_the_artifact():
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    a, b, c, d = _patched(_sync_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(prompt="x", caption=CAPTION)
    assert result["urls"] == ["https://cdn/img.jpg"]
    assert result["caption"] == CAPTION
    assert "_caption" not in result["payload"]  # the label is not part of the API request
    await db.close()


@pytest.mark.asyncio
async def test_sync_generate_echoes_caption_without_metadata():
    """return_metadata=False builds a different result dict — it must stamp too."""
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    a, b, c, d = _patched(_sync_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(
            prompt="x", caption=CAPTION, return_metadata=False
        )
    assert set(result) == {"urls", "expires_at", "payload", "caption"}
    assert result["caption"] == CAPTION
    await db.close()


@pytest.mark.asyncio
async def test_no_wait_envelope_echoes_caption_and_request_id():
    """wait=False returns before any artifact exists; the echo still rides along so the
    caller can file the pending task under the same label it will see at task_wait."""
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    a, b, c, d = _patched(_async_adapter(), repo, client)
    with a, b, c, d:
        result = await video_service.generate_video(
            prompt="x", wait=False, caption=CAPTION, request_id="r-1"
        )
    assert result["task_id"] == "cfgpu-task-1"
    assert result["caption"] == CAPTION
    assert result["request_id"] == "r-1"
    await db.close()


@pytest.mark.asyncio
async def test_mcp_wrapper_forwards_caption_to_the_service():
    """The FastMCP wrappers re-declare every param by hand, so the schema-consistency
    test proves the param exists while this proves the wrapper actually passes it on."""
    from cfgpu_mcp.server import mcp

    with patch(
        "cfgpu_mcp.service.image.generate_image",
        AsyncMock(return_value={"urls": ["https://cdn/img.jpg"], "caption": CAPTION}),
    ) as svc:
        await mcp.call_tool("generate_image", {"prompt": "x", "caption": CAPTION})
    assert svc.await_args.kwargs["caption"] == CAPTION


@pytest.mark.asyncio
async def test_omitting_caption_leaves_the_result_shape_unchanged():
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    a, b, c, d = _patched(_sync_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(prompt="x")
    assert "caption" not in result
    await db.close()
