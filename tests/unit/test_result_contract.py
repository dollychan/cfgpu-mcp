"""One result contract across generate_* / task_status / task_wait.

A caller holding one ``request_id`` has to answer a single question — *is this over?*
— from the tool result alone. That used to require reconstructing the answer from
``error_type`` x ``task_id``, because a wait that ran out of budget reported an
``error: True`` about a task that was running perfectly well. The rule is now:

    error  -> terminal (it failed, or it never took effect)
    artifact -> terminal (it succeeded)
    status   -> "pending" / "running", not done, come back with task_status

These tests pin the half that is easy to regress by accident: the paths where the
task is alive must never enter the error channel, whatever went wrong with the
*watching*. A regression here looks correct in every field it does return.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from cfgpu_mcp.adapters.base import PollConfig
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import task as task_service


async def _repo():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.db import _CREATE_TABLE
    from cfgpu_mcp.client.repository import SqliteTaskRepository

    await db.execute(_CREATE_TABLE)
    await db.commit()
    return SqliteTaskRepository(db), db


def _async_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "wan-2-0"
    adapter.model_name = "wan-video"
    adapter.is_async = True
    adapter.task_type = "video"
    adapter.endpoint = "/v1/video/tasks"
    adapter.poll_endpoint = "/v1/video/tasks/{task_id}"
    adapter.build_payload.return_value = {"model": "wan-video"}
    adapter.extract_task_id.return_value = "cfgpu-task-1"
    adapter.estimate_poll_timeout.return_value = 30
    # Real intervals so the tests do not spend a wall-clock minute proving a shape.
    adapter.poll_config = PollConfig(base_interval=0.01, max_interval=0.01, backoff_factor=1.0)
    return adapter


def _patched(adapter, repo, client):
    router = MagicMock()
    router.resolve.return_value = adapter
    registry = MagicMock()
    registry.get.return_value = adapter
    return (
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock(return_value=repo)),
        patch("cfgpu_mcp.config.get_client", MagicMock(return_value=client)),
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=registry)),
        patch("cfgpu_mcp.router.ModelRouter", MagicMock(return_value=router)),
    )


@pytest.mark.asyncio
async def test_a_wait_that_times_out_returns_the_pending_envelope_not_an_error():
    """★ The headline: ``generate_*(wait=True)`` that runs out of budget is not a failure.

    Same shape as ``wait=False``, because the caller's next move is the same. The
    ``request_id`` rides along on this shape too — it is what joins this result to the
    artifact that shows up one tool call later.
    """
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    client.get = AsyncMock(return_value={"id": "cfgpu-task-1", "status": "running"})
    a, b, c, d = _patched(_async_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(
            prompt="x", timeout=0, request_id="req-7",
        )

    assert "error" not in result
    assert "artifact" not in result
    assert result["status"] in ("pending", "running")
    assert result["task_id"] == "cfgpu-task-1"
    assert result["request_id"] == "req-7"
    # No last_error: polling was healthy, we simply stopped waiting. Its absence is the
    # difference between "we watched it run" and "we lost sight of it".
    assert "last_error" not in result
    await db.close()


@pytest.mark.asyncio
async def test_losing_sight_of_a_task_reports_why_without_calling_it_a_failure():
    """A credential rejected mid-poll stops the watching, not the job — it keeps running
    on someone else's GPU. So: still the pending envelope, with ``last_error`` carrying
    the ``error_type`` that says to fix the token *before* polling again."""
    repo, db = await _repo()
    client = MagicMock()
    client.post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    client.get = AsyncMock(
        side_effect=CFGPUError(error_type="auth", user_message="token 无效", retryable=False)
    )
    a, b, c, d = _patched(_async_adapter(), repo, client)
    with a, b, c, d:
        result = await image_service.generate_image(prompt="x", request_id="req-8")

    assert "error" not in result
    assert result["task_id"] == "cfgpu-task-1"
    assert result["last_error"]["error_type"] == "auth"
    assert result["last_error"]["retryable"] is False
    await db.close()


@pytest.mark.asyncio
async def test_task_wait_uses_the_same_envelope_as_generate():
    """The three tools must not diverge — a caller that switched from a blocking
    generate to the two-phase hop should not have to learn a second shape."""
    from cfgpu_mcp.client import db as db_ops

    repo, db = await _repo()
    await db_ops.insert_task(db, "cfgpu-task-1", "wan-2-0", "pending", {"prompt": "x"})
    client = MagicMock()
    client.get = AsyncMock(return_value={"id": "cfgpu-task-1", "status": "running"})
    a, b, c, _ = _patched(_async_adapter(), repo, client)
    with a, b, c:
        result = await task_service.wait_for_task("cfgpu-task-1", timeout=0)

    assert "error" not in result
    assert result["task_id"] == "cfgpu-task-1"
    assert result["status"] in ("pending", "running")
    await db.close()


@pytest.mark.asyncio
async def test_a_genuinely_failed_task_still_leaves_through_the_error_channel():
    """The other half of the rule. Failure is terminal *and* carries a remedy
    (``error_type`` / ``retryable`` / the card hint), so it stays an error — moving it
    into ``status: "failed"`` would trade an actionable shape for an enum value."""
    from cfgpu_mcp.client import db as db_ops

    repo, db = await _repo()
    await db_ops.insert_task(db, "cfgpu-task-1", "wan-2-0", "pending", {"prompt": "x"})
    await db_ops.update_task(db, "cfgpu-task-1", "failed", error="content blocked")
    client = MagicMock()
    a, b, c, _ = _patched(_async_adapter(), repo, client)
    with a, b, c:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("cfgpu-task-1")

    assert exc.value.error_type == "task_failed"
    assert exc.value.to_tool_result_dict()["error"] is True
    await db.close()


def test_pending_result_omits_last_error_when_there_is_none():
    """Presence is the signal, so an empty one must not be emitted."""
    from cfgpu_mcp.tool_registry import pending_result

    assert pending_result("t", "running") == {"task_id": "t", "status": "running"}
    assert pending_result("t", "running", {}) == {"task_id": "t", "status": "running"}
    assert "last_error" in pending_result("t", "running", {"error_type": "timeout"})
