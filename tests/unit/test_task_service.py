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


def _adapter(is_async: bool):
    adapter = MagicMock()
    adapter.adapter_id = "m"
    adapter.is_async = is_async
    adapter.poll_endpoint = "/v1/tasks/{task_id}" if is_async else None
    return adapter


def _patch_config(db, client, adapter):
    registry = MagicMock()
    registry.get.return_value = adapter
    return (
        patch("cfgpu_mcp.config.get_db", AsyncMock(return_value=db)),
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
async def test_get_status_async_model_repolls():
    db = await _db_with_succeeded_task("wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "succeeded", "output": {}})
    adapter = _adapter(is_async=True)
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    adapter.extract_status.return_value = "succeeded"
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-1", model_used="wan-video", seed=None, cost_tokens=None,
    )
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    client.get.assert_awaited_once()
    # Flattened to match generate_*: urls live at the top level, not under "result"
    assert result["urls"] == ["https://cdn/v.mp4"]
    assert "result" not in result
    await db.close()
