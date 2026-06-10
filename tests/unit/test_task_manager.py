import asyncio
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.task_manager import TaskManager, Task
from cfgpu_mcp.tool_registry import GenerateVideoInput, GenerateImageInput


async def _make_tm() -> tuple[TaskManager, aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.db import _CREATE_TABLE
    await db.execute(_CREATE_TABLE)
    await db.commit()
    client = AsyncMock()
    from cfgpu_mcp.client.repository import SqliteTaskRepository
    return TaskManager(client, SqliteTaskRepository(db)), db


def _sync_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "doubao-seedream-5-0-lite"
    adapter.cfgpu_model_id = "doubao-seedream-5-0-lite-api"
    adapter.is_async = False
    adapter.endpoint = "/v1/images/generations"
    adapter.build_payload.return_value = {"model": "test", "prompt": "x"}
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/img.jpg"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None,
        model_used="test",
        seed=None,
        cost_tokens=10,
    )
    return adapter


def _async_adapter(task_id: str = "cfgpu-task-1"):
    adapter = MagicMock()
    adapter.adapter_id = "wan-2-0"
    adapter.cfgpu_model_id = "wan-video"
    adapter.is_async = True
    adapter.endpoint = "/v1/video/tasks"
    adapter.poll_endpoint = "/v1/video/tasks/{task_id}"
    adapter.build_payload.return_value = {"model": "wan-video", "content": []}
    adapter.poll_config = MagicMock(
        base_interval=0.01, max_interval=0.1, backoff_factor=1.1, default_timeout=5
    )
    adapter.estimate_poll_timeout.return_value = 5
    adapter.extract_task_id.side_effect = lambda r: r.get("id") or r.get("task_id") or task_id
    adapter.extract_status.side_effect = lambda r: r.get("status", "running")
    return adapter


@pytest.mark.asyncio
async def test_sync_model_create_returns_succeeded():
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)
    assert task.status == "succeeded"
    assert task.result is not None
    await db.close()


@pytest.mark.asyncio
async def test_sync_create_falls_back_to_cfgpu_model_id():
    """When the API response omits `model`, model_used falls back to the
    adapter's cfgpu_model_id — critical for model='auto' where the caller has
    no other way to learn which model ran."""
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/img.jpg"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None,
        model_used=None,  # API didn't echo back "model"
        seed=None,
        cost_tokens=10,
    )
    tm._client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)
    assert task.result["model_used"] == "doubao-seedream-5-0-lite-api"
    await db.close()


@pytest.mark.asyncio
async def test_poll_falls_back_to_cfgpu_model_id():
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client.post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc",
        model_used=None,  # poll response carries no "model" field
        seed=None,
        cost_tokens=None,
    )
    tm._client.get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed",
        "content": {"videoUrl": "https://cdn/v.mp4"},
    })
    task = await tm.poll(task, adapter)
    assert task.result["model_used"] == "wan-video"
    await db.close()


@pytest.mark.asyncio
async def test_async_model_create_returns_pending():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client.post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)
    assert task.status == "pending"
    assert task.id == "cfgpu-task-1"
    await db.close()


@pytest.mark.asyncio
async def test_async_create_raises_when_no_task_id():
    from cfgpu_mcp.errors import CFGPUError
    tm, db = await _make_tm()
    adapter = _async_adapter()
    # Simulate an unexpected response shape: extract_task_id finds nothing.
    adapter.extract_task_id.side_effect = lambda r: None
    tm._client.post = AsyncMock(return_value={"unexpected": "shape"})
    req = GenerateVideoInput(prompt="x")
    with pytest.raises(CFGPUError) as exc_info:
        await tm.create(adapter, req)
    assert exc_info.value.error_type == "unknown"
    # No bogus pending row should have been written.
    assert await tm.list_running() == []
    await db.close()


@pytest.mark.asyncio
async def test_poll_updates_status():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client.post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc",
        model_used="wan-video",
        seed=None,
        cost_tokens=100,
    )
    tm._client.get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed", "model": "wan-video",
        "output": {"video_url": "https://cdn/v.mp4"}
    })
    task = await tm.poll(task, adapter)
    assert task.status == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_wait_sync_model_returns_immediately():
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)

    poll_called = False
    original_poll = tm.poll
    async def patched_poll(t, a):
        nonlocal poll_called
        poll_called = True
        return await original_poll(t, a)
    tm.poll = patched_poll  # type: ignore

    result = await tm.wait(task, adapter, req)
    assert not poll_called
    assert result.status == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_wait_times_out_and_raises():
    from cfgpu_mcp.errors import CFGPUError
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client.post = AsyncMock(return_value={"id": "task-timeout"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    tm._client.get = AsyncMock(return_value={"id": "task-timeout", "status": "running"})
    adapter.parse_response.return_value = None

    with pytest.raises(CFGPUError) as exc_info:
        await tm.wait(task, adapter, req, timeout=0)
    assert exc_info.value.error_type == "timeout"
    await db.close()


@pytest.mark.asyncio
async def test_status_raises_for_unknown_task_id():
    tm, db = await _make_tm()
    with pytest.raises(KeyError):
        await tm.status("nonexistent-id")
    await db.close()


@pytest.mark.asyncio
async def test_list_running_excludes_completed():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client.post = AsyncMock(return_value={"id": "task-run"})
    req = GenerateVideoInput(prompt="x")
    await tm.create(adapter, req)

    running = await tm.list_running()
    assert any(t.id == "task-run" for t in running)

    # Simulate completion
    from cfgpu_mcp.client import db as db_ops
    await db_ops.update_task(db, "task-run", "succeeded")

    running_after = await tm.list_running()
    assert not any(t.id == "task-run" for t in running_after)
    await db.close()
