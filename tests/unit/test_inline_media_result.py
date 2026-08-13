"""An inline-media artifact survives every result shape that can carry it.

MiniMax speech returns the audio inline (base64 blob, no URL), so for that model
``inline_media`` *is* the artifact — a shape that drops it returns nothing at all.
Three shapes reach the caller and each used to test ``urls`` alone:

- ``generate_*(return_metadata=False)`` — the lean dict, now built by ``lean_result``
- ``task_status`` / ``task_wait`` — ``_present``, reachable for a *sync* inline-media
  model via ``generate_audio(wait=False)``
- the MCP split — the blob must land in structuredContent, never in the LLM content
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.db import _CREATE_TABLE
from cfgpu_mcp.service import audio as audio_service
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.tool_registry import NormalizedResult

BLOB = {"data": "SUQz", "mime_type": "audio/mpeg", "filename": "speech.mp3"}


async def _repo() -> tuple[object, aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.repository import SqliteTaskRepository

    await db.execute(_CREATE_TABLE)
    await db.commit()
    return SqliteTaskRepository(db), db


def _sync_audio_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "minimax-speech-2-8-hd"
    adapter.model_name = "MiniMax/speech-2.8-hd"
    adapter.is_async = False
    adapter.task_type = "audio"
    adapter.endpoint = "/voice/generations"
    adapter.build_payload.return_value = {"model": "MiniMax/speech-2.8-hd", "input": {"text": "x"}}
    adapter.parse_response.return_value = NormalizedResult(
        urls=[],  # MiniMax hands back a hex blob, never a URL
        inline_media=[BLOB],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None,
        model_used="MiniMax/speech-2.8-hd",
        seed=None,
        usage={"characters": 34},
    )
    return adapter


def _sync_image_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "doubao-seedream-5-0-lite"
    adapter.model_name = "doubao-seedream-5-0-lite"
    adapter.is_async = False
    adapter.task_type = "image"
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
async def test_lean_result_keeps_inline_media():
    """return_metadata=False suppresses metadata, not the artifact. Without this the
    MiniMax lean result is `{urls: [], expires_at, payload}` — no audio at all."""
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"output": {"data": {"audio": "494433"}}})
    a, b, c, d = _patched(_sync_audio_adapter(), repo, client)
    with a, b, c, d:
        result = await audio_service.generate_audio(text="x", return_metadata=False)
    assert result["inline_media"] == [BLOB]
    assert set(result) == {"urls", "expires_at", "payload", "inline_media"}
    # metadata is still suppressed — that is what the flag is for
    assert "usage" not in result and "model_used" not in result
    await db.close()


@pytest.mark.asyncio
async def test_lean_result_shape_unchanged_for_url_models():
    """The extra key is conditional: a URL-returning model's lean shape is untouched."""
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    a, b, c, d = _patched(_sync_image_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(prompt="x", return_metadata=False)
    assert set(result) == {"urls", "expires_at", "payload"}
    await db.close()


@pytest.mark.asyncio
async def test_task_status_presents_an_inline_media_only_task():
    """generate_audio(wait=False) on a sync model stores a succeeded row whose result
    has no urls; task_status must present it as the artifact, not as a bare envelope."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    await db_ops.insert_task(db, "task-1", "minimax-speech-2-8-hd", "pending", {"input": {"text": "x"}})
    await db_ops.update_task(
        db, "task-1", "succeeded",
        result={"urls": [], "inline_media": [BLOB], "expires_at": None, "usage": {"characters": 34}},
    )

    from cfgpu_mcp.client.repository import SqliteTaskRepository
    registry = MagicMock()
    registry.get.return_value = _sync_audio_adapter()
    client = MagicMock()
    client.get = AsyncMock()  # terminal row → no re-poll
    with (
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock(return_value=SqliteTaskRepository(db))),
        patch("cfgpu_mcp.config.get_client", MagicMock(return_value=client)),
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=registry)),
    ):
        result = await task_service.get_status("task-1")
    assert result["inline_media"] == [BLOB]
    assert "payload" in result  # full result shape, not the {task_id, status} envelope
    client.get.assert_not_awaited()
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,svc", [
    ("task_status", "cfgpu_mcp.service.task.get_status"),
    ("task_wait", "cfgpu_mcp.service.task.wait_for_task"),
])
async def test_task_tools_split_the_blob_out_of_the_llm_content(tool, svc):
    """task_status/task_wait must list inline_media in structured_keys exactly like
    generate_audio does — otherwise a base64 audio blob enters the model context."""
    from cfgpu_mcp.server import mcp

    result = {"urls": [], "inline_media": [BLOB], "expires_at": None,
              "usage": {"characters": 34}, "payload": {"model": "MiniMax/speech-2.8-hd"}}
    with patch(svc, AsyncMock(return_value=result)):
        out = await mcp.call_tool(tool, {"task_id": "task-1"})

    assert out.structuredContent["inline_media"] == [BLOB]
    content = json.loads(out.content[0].text)
    assert "inline_media" not in content
    assert content["artifact"] is True  # the LLM still learns an artifact exists
