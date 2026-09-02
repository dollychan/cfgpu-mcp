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
    adapter.cfgpu_model_id = "m-model-api"
    adapter.model_name = "m-model"
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
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_succeeded_task("doubao-seedream-5-0-lite")
    client = MagicMock()
    client.get = AsyncMock()  # poll would call this
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=False))
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("task-1")
    # The invariant under test: a sync model is never re-polled.
    client.get.assert_not_called()
    # And the row itself (succeeded, no artifact) converges to a terminal failure rather
    # than being handed back as `status: "succeeded"` with nothing in it — that shape
    # reads as "not done yet" under the result contract, i.e. poll a terminal row forever.
    assert exc.value.error_type == "task_failed"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_failed_task_raises_standard_error():
    from cfgpu_mcp.errors import CFGPUError
    # A failed task must surface the same {error_type: task_failed, model_id}
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
    assert exc_info.value.model_id == "m-model"
    assert "content blocked" in exc_info.value.user_message
    await db.close()


@pytest.mark.asyncio
async def test_get_status_succeeded_async_task_is_terminal():
    """A task already persisted as 'succeeded' is terminal: get_status does NOT
    re-poll upstream (poll() converges a urls-less success to 'failed' at write
    time, so a stale succeeded record is never re-fetched on every read).

    Being terminal, it must also *present* as terminal. A succeeded row with no
    artifact used to fall through to the non-terminal envelope, which the result
    contract reads as "poll again" — an instruction that can never be satisfied for a
    row nothing will ever re-poll.
    """
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_succeeded_task("wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(return_value={"status": "succeeded", "output": {}})
    adapter = _adapter(is_async=True)
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("task-1")
    client.get.assert_not_called()           # terminal: no upstream poll
    assert exc.value.error_type == "task_failed"
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


# ── request_id correlation echo ────────────────────────────────────────────────

from cfgpu_mcp.task_manager import _CAPTION_KEY, _LABEL_KEY, _REQUEST_ID_KEY


async def _db_with_task(status: str, adapter_id: str, *, result=None, error=None,
                        request_id: str | None = None,
                        caption: str | None = None,
                        label: str | None = None) -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    payload = {"prompt": "x"}
    if request_id:
        payload[_REQUEST_ID_KEY] = request_id
    if caption:
        payload[_CAPTION_KEY] = caption
    if label:
        payload[_LABEL_KEY] = label
    await db_ops.insert_task(db, "task-1", adapter_id, "pending", payload)
    if status != "pending":
        await db_ops.update_task(db, "task-1", status, result=result, error=error)
    return db


@pytest.mark.asyncio
async def test_get_status_echoes_request_id_on_success():
    """A succeeded async task echoes the caller's request_id (recovered from the
    stored payload) so the artifact can be joined back to the generate_* call —
    while the reserved key is stripped from the surfaced payload."""
    db = await _db_with_task(
        "succeeded", "wan-2-0", result={"urls": ["https://cdn/v.mp4"]}, request_id="r-42"
    )
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["request_id"] == "r-42"
    assert _REQUEST_ID_KEY not in result["payload"]
    await db.close()


@pytest.mark.asyncio
async def test_get_status_echoes_request_id_on_pending_envelope():
    db = await _db_with_task("pending", "wan-2-0", request_id="r-99")
    client = MagicMock()
    # keep it pending: upstream still running, so the pending envelope is returned
    client.get = AsyncMock(return_value={"id": "task-1", "status": "running"})
    adapter = _adapter(is_async=True)
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["status"] in ("pending", "running")
    assert result["request_id"] == "r-99"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_failed_task_echoes_request_id():
    """A failed task carries request_id onto the CFGPUError so the error result
    can be joined back to the originating request."""
    from cfgpu_mcp.errors import CFGPUError
    db = await _db_with_task("failed", "wan-2-0", error="content blocked", request_id="r-7")
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc_info:
            await task_service.get_status("task-1")
    assert exc_info.value.request_id == "r-7"
    assert exc_info.value.to_tool_result_dict()["request_id"] == "r-7"
    await db.close()


# ── caption echo across the async hop ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_status_echoes_caption_on_success():
    """The label supplied on generate_* arrives with the artifact one tool call later.

    This is the case the whole stash exists for: a caller keeping its own asset ledger
    can file the artifact under the user's name for it without holding state of its own
    across the generate → task_status/task_wait hop.
    """
    db = await _db_with_task(
        "succeeded", "wan-2-0", result={"urls": ["https://cdn/v.mp4"]}, caption="开场镜头 v2"
    )
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["caption"] == "开场镜头 v2"
    assert _CAPTION_KEY not in result["payload"]  # never surfaced as part of the API request
    await db.close()


@pytest.mark.asyncio
async def test_get_status_echoes_caption_on_pending_envelope():
    db = await _db_with_task("pending", "wan-2-0", caption="开场镜头 v2")
    client = MagicMock()
    client.get = AsyncMock(return_value={"id": "task-1", "status": "running"})
    adapter = _adapter(is_async=True)
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["caption"] == "开场镜头 v2"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_echoes_label_on_success():
    """The name supplied on generate_* arrives with the artifact one tool call later —
    without it a host's asset panel shows the artifact under an opaque generated key."""
    db = await _db_with_task(
        "succeeded", "wan-2-0", result={"urls": ["https://cdn/v.mp4"]}, label="开场镜头.mp4"
    )
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["label"] == "开场镜头.mp4"
    assert _LABEL_KEY not in result["payload"]
    await db.close()


@pytest.mark.asyncio
async def test_get_status_echoes_label_on_pending_envelope():
    db = await _db_with_task("pending", "wan-2-0", label="开场镜头.mp4")
    client = MagicMock()
    client.get = AsyncMock(return_value={"id": "task-1", "status": "running"})
    adapter = _adapter(is_async=True)
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")
    assert result["label"] == "开场镜头.mp4"
    await db.close()


@pytest.mark.asyncio
async def test_wait_echoes_both_caption_and_label():
    """task_wait is the other half of the async hop and must echo the same pair."""
    db = await _db_with_task(
        "succeeded", "wan-2-0", result={"urls": ["https://cdn/v.mp4"]},
        caption="开场镜头 v2", label="开场镜头.mp4",
    )
    client = MagicMock()
    client.get = AsyncMock()
    adapter = _adapter(is_async=True)
    adapter.estimate_poll_timeout.return_value = 5  # a MagicMock cannot be min()'d
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.wait_for_task("task-1")
    assert result["caption"] == "开场镜头 v2"
    assert result["label"] == "开场镜头.mp4"
    await db.close()


@pytest.mark.asyncio
async def test_failed_task_carries_no_label_either():
    """Same asymmetry as the caption: a failed call produced no artifact to name."""
    from cfgpu_mcp.errors import CFGPUError
    db = await _db_with_task(
        "failed", "wan-2-0", error="content blocked", request_id="r-7", label="开场镜头.mp4"
    )
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc_info:
            await task_service.get_status("task-1")
    err = exc_info.value.to_tool_result_dict()
    assert err["request_id"] == "r-7"
    assert "label" not in err
    await db.close()


@pytest.mark.asyncio
async def test_failed_task_carries_request_id_but_not_caption():
    """The documented asymmetry: joining a failure back to its request is what a
    correlation handle is for, but a failed call produced no artifact to label."""
    from cfgpu_mcp.errors import CFGPUError
    db = await _db_with_task(
        "failed", "wan-2-0", error="content blocked", request_id="r-7", caption="开场镜头 v2"
    )
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc_info:
            await task_service.get_status("task-1")
    err = exc_info.value.to_tool_result_dict()
    assert err["request_id"] == "r-7"
    assert "caption" not in err
    await db.close()


@pytest.mark.asyncio
async def test_get_status_converges_when_the_repoll_body_carries_an_upstream_error():
    """task_status must resolve the task, not report it running forever.

    Its re-poll swallows transient poll errors on purpose (a stale running row is the
    right answer when the network hiccuped). A copyright/moderation rejection arriving
    as an HTTP-200 body error used to land in that same branch — classified `unknown`,
    logged as transient — so every subsequent task_status returned the stale running
    row and the task could never resolve through this tool at all.
    """
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("pending", "wan-2-0", request_id="r-7")
    client = MagicMock()
    client.get = AsyncMock(return_value={
        "id": "task-1",
        "error": {"message": "The request failed because the output video may be "
                             "related to copyright restrictions."},
    })
    adapter = _adapter(is_async=True)
    adapter.extract_status.side_effect = lambda r: r.get("status", "running")
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("task-1")

    err = exc.value
    assert err.error_type == "task_failed"
    assert err.retryable is False
    assert "copyright restrictions" in err.user_message
    assert err.request_id == "r-7"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_marks_a_non_retryable_repoll_failure_on_the_envelope():
    """A re-poll error that will not heal must not present as a plain running task.

    task_status swallows transient poll failures on purpose — the stale record is the
    right answer when the network hiccuped. But a non-retryable one (a retired endpoint,
    a token the upstream will keep rejecting) makes every future call fail the same way,
    so a bare `status: "running"` sends the caller into a loop that cannot terminate.
    The task really is still alive upstream, so this rides `last_error` rather than the
    error channel — the same shape `task_wait` produces for the same error.
    """
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("pending", "wan-2-0", request_id="r-1")
    client = MagicMock()
    client.get = AsyncMock(side_effect=CFGPUError(
        error_type="model_unavailable",
        user_message="所选模型暂不可用，请尝试其他模型。",
        original={},
        retryable=False,
    ))
    adapter = _adapter(is_async=True)
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")

    assert result["status"] in ("pending", "running")
    assert "error" not in result                      # the task is alive; not the error channel
    assert result["last_error"]["error_type"] == "model_unavailable"
    assert result["last_error"]["retryable"] is False
    # elapsed / consecutive_failures are properties of a *wait*; one re-poll has neither.
    assert "elapsed" not in result["last_error"]
    assert result["request_id"] == "r-1"
    await db.close()


@pytest.mark.asyncio
async def test_get_status_stays_silent_about_a_retryable_repoll_failure():
    """The ordinary case is unchanged: a blip returns the stale record with no noise."""
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("pending", "wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(side_effect=CFGPUError(
        error_type="timeout", user_message="请求超时", original={}, retryable=True,
    ))
    p_db, p_client, p_reg = _patch_config(db, client, _adapter(is_async=True))
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")

    assert result["status"] == "pending"
    assert "last_error" not in result
    await db.close()


@pytest.mark.asyncio
async def test_get_status_flags_a_repoll_that_blew_up_outside_cfgpu_error():
    """An adapter that crashes parsing a terminal body recurs on every call.

    It carries no classification we can trust, so `retryable` stays True (never tell a
    caller to stop polling a live task) — the *presence* of last_error is the news:
    this "running" was never observed.
    """
    db = await _db_with_task("pending", "wan-2-0")
    client = MagicMock()
    client.get = AsyncMock(return_value={"id": "task-1", "status": "succeeded"})
    adapter = _adapter(is_async=True)
    adapter.extract_status.return_value = "succeeded"
    adapter.parse_response.side_effect = KeyError("videoUrl")
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("task-1")

    assert result["status"] == "pending"
    assert result["last_error"]["error_type"] == "unknown"
    assert "videoUrl" in result["last_error"]["message"]
    await db.close()
