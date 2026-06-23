import aiosqlite
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.db import _CREATE_TABLE
from cfgpu_mcp.service import task as task_service


async def _db_with_succeeded_task(adapter_id: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    # succeeded but result has no URLs → re-poll condition
    await db_ops.insert_task(db, "task-1", adapter_id, "pending", {"prompt": "x"})
    await db_ops.update_task(db, "task-1", "succeeded", result={"urls": []})
    return db


async def _db_with_pending_task(adapter_id: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    await db_ops.insert_task(db, "task-1", adapter_id, "pending", {"prompt": "x"})
    return db


async def _db_with_failed_task(adapter_id: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    await db_ops.insert_task(db, "task-1", adapter_id, "pending", {"prompt": "x"})
    await db_ops.update_task(db, "task-1", "failed", error="content blocked")
    return db


def _adapter(is_async: bool):
    adapter = MagicMock()
    adapter.adapter_id = "m"
    adapter.is_async = is_async
    adapter.poll_endpoint = "/v1/tasks/{task_id}" if is_async else None
    return adapter


def _patch_config(db, client, adapter):
    registry = MagicMock()
    registry.get.return_value = adapter
    from cfgpu_mcp.client.repository import SqliteTaskRepository
    repo = SqliteTaskRepository(db)
    return (
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock(return_value=repo)),
        patch("cfgpu_mcp.config.get_client", MagicMock(return_value=client)),
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=registry)),
    )


@pytest.mark.asyncio
async def test_get_status_sync_model_skips_repoll():
    db = await _db_with_succeeded_task("doubao-seedream-5-0-lite")
    client = MagicMock()
    client.get = AsyncMock()  # poll would call this
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=False))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    # Sync model: no poll attempted, stale result returned without raising
    client.get.assert_not_called()
    assert result["status"] == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_failed_task_raises_standard_error():
    from cfgpu_mcp.errors import CFGPUError
    # A failed task must surface the same {error_type: task_failed, adapter_id}
    # shape as task_wait / generate_* — not a divergent {status, error} envelope.
    db = await _db_with_failed_task("doubao-seedream-5-0-lite")
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc_info:
            await task_service.get_status("task-1")
    # Terminal failure: no re-poll attempted.
    client.get.assert_not_called()
    assert exc_info.value.error_type == "task_failed"
    assert exc_info.value.adapter_id == "doubao-seedream-5-0-lite"
    assert "content blocked" in exc_info.value.user_message
    await db.close()


@pytest.mark.asyncio
async def test_get_status_succeeded_async_task_is_terminal():
    """A task already persisted as 'succeeded' is terminal: get_status does NOT
    re-poll upstream (poll() converges a urls-less success to 'failed' at write
    time, so a stale succeeded record is never re-fetched on every read)."""
    db = await _db_with_succeeded_task("wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "succeeded", "output": {}})
    adapter = _adapter(is_async=True)
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    client.get.assert_not_called()           # terminal: no upstream poll
    assert result["status"] == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_repolls_pending_async_task():
    """Client-driven polling: a still-pending async task gets one live upstream
    poll on each get_status, so a wait=False client can drive it to completion."""
    db = await _db_with_pending_task("wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "succeeded", "output": {}})
    adapter = _adapter(is_async=True)
    from datetime import UTC, datetime, timedelta

    from cfgpu_mcp.tool_registry import NormalizedResult
    adapter.extract_status.return_value = "succeeded"
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-1", model_used="wan-video", seed=None, usage=None,
    )
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    client.get.assert_awaited_once()         # advanced via one upstream poll
    assert result["urls"] == ["https://cdn/v.mp4"]
    await db.close()


@pytest.mark.asyncio
async def test_get_status_result_includes_real_api_payload():
    """A succeeded task surfaces the real per-model API payload, with the internal
    _requested_aspect_ratio echo stripped."""
    from cfgpu_mcp.task_manager import _ASPECT_RATIO_KEY

    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    stored_payload = {"model": "wan-video", "prompt": "x", _ASPECT_RATIO_KEY: "16:9"}
    await db_ops.insert_task(db, "task-1", "wan-2-0", "pending", stored_payload)
    await db_ops.update_task(db, "task-1", "succeeded", result={"urls": ["https://cdn/v.mp4"]})

    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")

    assert result["payload"] == {"model": "wan-video", "prompt": "x"}
    assert _ASPECT_RATIO_KEY not in result["payload"]
    await db.close()
