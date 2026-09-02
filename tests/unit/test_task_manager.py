import asyncio
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.task_manager import TaskManager, Task, single_client
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateAudioInput, GenerateVideoInput, GenerateImageInput


async def _make_tm() -> tuple[TaskManager, aiosqlite.Connection]:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    from cfgpu_mcp.client.db import _CREATE_TABLE
    await db.execute(_CREATE_TABLE)
    await db.commit()
    client = AsyncMock()
    from cfgpu_mcp.client.repository import SqliteTaskRepository
    return TaskManager(single_client(client), SqliteTaskRepository(db)), db


def _sync_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "doubao-seedream-5-0-lite"
    adapter.cfgpu_model_id = "doubao-seedream-5-0-lite-api"
    adapter.model_name = "doubao-seedream-5-0-lite"
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
        usage={"total_tokens": 10},
    )
    return adapter


def _async_adapter(task_id: str = "cfgpu-task-1"):
    adapter = MagicMock()
    adapter.adapter_id = "wan-2-0"
    adapter.cfgpu_model_id = "wan-video"
    adapter.model_name = "wan-video"
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
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)
    assert task.status == "succeeded"
    assert task.result is not None
    await db.close()


@pytest.mark.asyncio
async def test_sync_media_create_without_artifact_raises_error():
    """HTTP success is not generation success when a sync media API returns no media."""
    from cfgpu_mcp.tool_registry import NormalizedResult

    tm, db = await _make_tm()
    adapter = _sync_adapter()
    adapter.task_type = "audio"
    adapter.parse_response.return_value = NormalizedResult(
        urls=[],
        inline_media=None,
        expires_at=None,
        task_id=None,
        model_used="MiniMax/speech-2.8-hd",
        seed=None,
        usage=None,
    )
    upstream = {
        "output": {
            "base_resp": {"status_code": 2054, "status_msg": "voice id not exist"},
        }
    }
    tm._client_for(None).post = AsyncMock(return_value=upstream)

    with pytest.raises(CFGPUError) as exc_info:
        await tm.create(adapter, GenerateAudioInput(text="x"))

    error = exc_info.value
    assert error.error_type == "task_failed"
    assert error.retryable is False
    assert "没有返回任何产物 URL 或内联媒体" in error.user_message
    assert error.original["response"] == upstream
    await db.close()


@pytest.mark.asyncio
async def test_sync_create_stamps_model_name():
    """model_used always mirrors the adapter's public model_name, regardless of
    what (if anything) the upstream response echoes back in "model" — that field
    is the internal cfgpu_model_id and must never reach the caller."""
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/img.jpg"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None,
        model_used="doubao-seedream-5-0-lite-api",  # upstream echo of cfgpu_model_id
        seed=None,
        usage={"total_tokens": 10},
    )
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)
    assert task.result["model_used"] == "doubao-seedream-5-0-lite"
    await db.close()


@pytest.mark.asyncio
async def test_poll_stamps_model_name():
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _async_adapter()
    adapter.model_name = "cf-wan-video"  # distinct from cfgpu_model_id, to prove the override
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc",
        model_used="wan-video",  # upstream echo of cfgpu_model_id
        seed=None,
        usage=None,
    )
    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed",
        "content": {"videoUrl": "https://cdn/v.mp4"},
    })
    task = await tm.poll(task, adapter)
    assert task.result["model_used"] == "cf-wan-video"
    await db.close()


@pytest.mark.asyncio
async def test_sync_create_echoes_aspect_ratio():
    """The requested aspect_ratio is echoed back in the result metadata."""
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x", aspect_ratio="16:9")
    task = await tm.create(adapter, req)
    assert task.result["aspect_ratio"] == "16:9"
    await db.close()


@pytest.mark.asyncio
async def test_poll_echoes_requested_aspect_ratio():
    """Async models finalize in poll() without the request in scope; the
    requested aspect_ratio is recovered from the stashed payload and echoed."""
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x", aspect_ratio="9:16")
    task = await tm.create(adapter, req)

    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc",
        model_used="wan-video",
        seed=None,
        usage=None,
    )
    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed",
        "content": {"videoUrl": "https://cdn/v.mp4"},
    })
    task = await tm.poll(task, adapter)
    assert task.result["aspect_ratio"] == "9:16"
    await db.close()


@pytest.mark.asyncio
async def test_poll_response_ratio_overrides_requested():
    """When the upstream response reports the resolved ratio (e.g. the request
    asked for "adaptive"), it overrides the requested aspect_ratio echo."""
    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x", aspect_ratio="adaptive")
    task = await tm.create(adapter, req)

    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc",
        model_used="wan-video",
        seed=None,
        usage=None,
        aspect_ratio="9:16",  # API resolved "adaptive" → "9:16"
    )
    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed", "ratio": "9:16",
        "content": {"videoUrl": "https://cdn/v.mp4"},
    })
    task = await tm.poll(task, adapter)
    assert task.result["aspect_ratio"] == "9:16"
    await db.close()


@pytest.mark.asyncio
async def test_async_model_create_returns_pending():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
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
    tm._client_for(None).post = AsyncMock(return_value={"unexpected": "shape"})
    req = GenerateVideoInput(prompt="x")
    with pytest.raises(CFGPUError) as exc_info:
        await tm.create(adapter, req)
    assert exc_info.value.error_type == "unknown"
    # The raw response rides the message: `original` is not surfaced by the tool
    # layer, so without it the caller cannot tell WHICH shape came back.
    assert '{"unexpected": "shape"}' in exc_info.value.user_message
    # No bogus pending row should have been written.
    assert await tm.list_running() == []
    await db.close()


@pytest.mark.asyncio
async def test_no_task_id_error_truncates_a_huge_response():
    from cfgpu_mcp.errors import CFGPUError
    tm, db = await _make_tm()
    adapter = _async_adapter()
    adapter.extract_task_id.side_effect = lambda r: None
    tm._client_for(None).post = AsyncMock(return_value={"blob": "x" * 5000})
    with pytest.raises(CFGPUError) as exc_info:
        await tm.create(adapter, GenerateVideoInput(prompt="x"))
    assert len(exc_info.value.user_message) < 600
    assert exc_info.value.user_message.endswith("…")
    # The full body is still available to the server-side caller.
    assert exc_info.value.original["response"] == {"blob": "x" * 5000}
    await db.close()


@pytest.mark.asyncio
async def test_poll_updates_status():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
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
        usage={"total_tokens": 100},
    )
    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc", "status": "completed", "model": "wan-video",
        "output": {"video_url": "https://cdn/v.mp4"}
    })
    task = await tm.poll(task, adapter)
    assert task.status == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_poll_success_without_urls_converges_to_failed():
    """Upstream reports success but returns no artifact URL: poll() writes a
    terminal 'failed' (not 'succeeded') so the task converges instead of being
    re-polled forever on every get_status."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    adapter.parse_response.return_value = NormalizedResult(
        urls=[],  # success status but no URLs
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc", model_used="wan-video", seed=None, usage=None,
    )
    tm._client_for(None).get = AsyncMock(return_value={"id": "task-abc", "status": "completed"})
    task = await tm.poll(task, adapter)
    assert task.status == "failed"
    assert task.result is None
    assert task.error
    await db.close()


@pytest.mark.asyncio
async def test_poll_success_with_inline_media_only_stays_succeeded():
    """Inline media (base64 blob, no downloadable URL) is a real artifact: the
    urls-less guard must not force such a task to 'failed'. Keeps poll() in step
    with annotate_artifact(), which counts urls OR inline_media as an artifact."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    from cfgpu_mcp.tool_registry import NormalizedResult
    from datetime import datetime, UTC, timedelta
    adapter.parse_response.return_value = NormalizedResult(
        urls=[],  # no URL — the artifact came back inline instead
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id="task-abc", model_used="wan-video", seed=None, usage=None,
        inline_media=[{"data": "AAA=", "mime_type": "audio/mpeg"}],
    )
    tm._client_for(None).get = AsyncMock(return_value={"id": "task-abc", "status": "completed"})
    task = await tm.poll(task, adapter)
    assert task.status == "succeeded"
    assert task.result["inline_media"] == [{"data": "AAA=", "mime_type": "audio/mpeg"}]
    assert task.error is None
    await db.close()


@pytest.mark.asyncio
async def test_wait_sync_model_returns_immediately():
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)

    poll_called = False
    original_poll = tm.poll
    async def patched_poll(t, a):
        nonlocal poll_called
        poll_called = True
        return await original_poll(t, a)
    tm.poll = patched_poll  # type: ignore

    outcome = await tm.wait(task, adapter, req)
    assert not poll_called
    assert outcome.task.status == "succeeded"
    assert outcome.last_error is None
    await db.close()


@pytest.mark.asyncio
async def test_wait_that_runs_out_of_budget_is_not_an_error():
    """★ Running out of patience is a fact about the wait, not about the task.

    The task was created and is running; the caller's next move is the same
    ``task_status(task_id)`` it would make after ``wait=False``, so the result is the
    same non-terminal envelope. Reporting ``error: True`` over a healthy job is what
    made "is this over?" un-answerable from any single field.

    ``last_error`` is absent here specifically: polling never failed, so "running"
    means we watched it run. Its absence is the difference from the give-up paths.
    """
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-timeout"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    tm._client_for(None).get = AsyncMock(return_value={"id": "task-timeout", "status": "running"})
    adapter.parse_response.return_value = None

    outcome = await tm.wait(task, adapter, req, timeout=0)

    assert outcome.task.id == "task-timeout"
    assert outcome.task.status in ("pending", "running")
    assert outcome.last_error is None
    await db.close()


@pytest.mark.asyncio
async def test_wait_is_clamped_to_the_hard_ceiling():
    """★ A wait longer than MAX_WAIT_SECONDS is never honoured, however it was asked for.

    The binding constraint isn't ours — it's the MCP client's own request timeout,
    which is far shorter. Waiting past it doesn't buy patience, it destroys the call:
    the client disconnects, the session is torn down, and the timeout error we would
    have raised (carrying the task_id, the caller's only way back to a job that is
    running fine) is never delivered. So an over-large budget must be cut, not obeyed.
    """

    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-clamp"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)
    tm._client_for(None).get = AsyncMock(return_value={"id": "task-clamp", "status": "running"})

    # Ceiling pulled down to 0 so the clamp is observable without touching the
    # clock (time.monotonic is the event loop's own, patching it globally breaks
    # asyncio). Unclamped, min() would keep 99999 and this would poll forever.
    with patch("cfgpu_mcp.task_manager.MAX_WAIT_SECONDS", 0):
        outcome = await tm.wait(task, adapter, req, timeout=99999)

    assert outcome.task.id == "task-clamp"   # the handle always survives
    assert outcome.task.status != "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_adapter_default_timeout_is_clamped_too():
    """Same ceiling when the number comes from poll_config rather than the caller —
    a misconfigured adapter must not be able to reintroduce an unbounded wait."""

    tm, db = await _make_tm()
    adapter = _async_adapter()
    adapter.estimate_poll_timeout.return_value = 99999
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-clamp-2"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)
    tm._client_for(None).get = AsyncMock(return_value={"id": "task-clamp-2", "status": "running"})

    with patch("cfgpu_mcp.task_manager.MAX_WAIT_SECONDS", 0):
        outcome = await tm.wait(task, adapter, req)

    assert outcome.task.id == "task-clamp-2"
    await db.close()


def _transient() -> CFGPUError:
    """What CFGPUClient._request() raises when one HTTP call hangs past http_timeout."""
    return CFGPUError(
        error_type="timeout",
        user_message="请求超时（60.0s），请稍后重试或在 config.yaml 增大 cfgpu_api.http_timeout。",
        original={"url": "http://gw/v1/video/tasks/task-1", "timeout": 60.0},
        retryable=True,
    )


async def _waiting_tm(get_side_effect):
    """An async task mid-flight, with ``get`` (the poll) wired to ``get_side_effect``."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-1"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)
    tm._client_for(None).get = AsyncMock(side_effect=get_side_effect)
    return tm, db, adapter, req, task


@pytest.mark.asyncio
async def test_wait_survives_a_transient_poll_failure():
    """A poll failing is not the task failing — the job keeps running upstream.

    The co-located comfy-gateway freezes its event loop while it uploads an artifact,
    so the poll that lands in that window times out. Aborting there threw away a video
    that had already been generated, and with wait=True the caller never held the
    task_id, so it was unrecoverable.
    """
    from cfgpu_mcp.tool_registry import NormalizedResult

    tm, db, adapter, req, task = await _waiting_tm(
        [_transient(), {"id": "task-1", "status": "succeeded"}]
    )
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"], expires_at=None, task_id="task-1",
        model_used="wan-video", seed=None, usage=None,
    )

    outcome = await tm.wait(task, adapter, req)

    assert outcome.task.status == "succeeded"
    assert outcome.task.result["urls"] == ["https://cdn/v.mp4"]
    # Absorbed, not merely survived: a transient blip that the poll recovered from is
    # not something the caller has to hear about on a successful result.
    assert outcome.last_error is None
    await db.close()


@pytest.mark.asyncio
async def test_wait_gives_up_after_repeated_failures_but_hands_back_the_task_id():
    """A dead upstream must not hold the caller for the full poll timeout — and the
    task_id has to survive, or the artifact it is still producing is unreachable.

    Giving up on *watching* is not the task failing, so this returns rather than
    raising. ``last_error`` is what distinguishes this "running" from an observed one:
    it says we lost sight of the job, and why.
    """
    from cfgpu_mcp.task_manager import MAX_CONSECUTIVE_POLL_FAILURES

    tm, db, adapter, req, task = await _waiting_tm(
        [_transient() for _ in range(MAX_CONSECUTIVE_POLL_FAILURES + 3)]
    )

    outcome = await tm.wait(task, adapter, req)

    assert tm._client_for(None).get.await_count == MAX_CONSECUTIVE_POLL_FAILURES
    assert outcome.task.id == "task-1"
    assert outcome.last_error["error_type"] == "timeout"
    assert outcome.last_error["consecutive_failures"] == MAX_CONSECUTIVE_POLL_FAILURES
    # Carries the type, not just prose: `auth` and `timeout` need opposite next moves,
    # and deciding between them must not require parsing a sentence.
    assert outcome.last_error["retryable"] is True
    await db.close()


@pytest.mark.asyncio
async def test_a_run_of_failures_resets_once_a_poll_gets_through():
    """The ceiling counts *consecutive* failures. One flaky poll every other round is
    an upstream that is merely slow, not one that is gone — that must not accumulate
    into a give-up over a long video."""
    from cfgpu_mcp.tool_registry import NormalizedResult
    from cfgpu_mcp.task_manager import MAX_CONSECUTIVE_POLL_FAILURES

    running = {"id": "task-1", "status": "running"}
    flaky: list = []
    for _ in range(MAX_CONSECUTIVE_POLL_FAILURES + 2):
        flaky += [_transient(), running]
    flaky.append({"id": "task-1", "status": "succeeded"})

    tm, db, adapter, req, task = await _waiting_tm(flaky)
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"], expires_at=None, task_id="task-1",
        model_used="wan-video", seed=None, usage=None,
    )

    outcome = await tm.wait(task, adapter, req)

    assert outcome.task.status == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_wait_stops_at_once_on_a_non_retryable_poll_error_but_keeps_the_task():
    """A bad token will not fix itself by asking again — but it also did not stop the
    job, which keeps running upstream on someone else's GPU.

    So this stops watching without claiming the task failed. The credential problem
    rides ``last_error`` with its ``error_type`` intact, because that is what tells the
    caller to fix the token *before* polling rather than simply polling again.
    """
    tm, db, adapter, req, task = await _waiting_tm(
        CFGPUError(error_type="auth", user_message="token 无效", retryable=False)
    )

    outcome = await tm.wait(task, adapter, req)

    assert tm._client_for(None).get.await_count == 1
    # Still resumable: the caller needs the id even on the paths that stop early.
    assert outcome.task.id == "task-1"
    assert outcome.last_error["error_type"] == "auth"
    assert outcome.last_error["retryable"] is False
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
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-run"})
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


# ── request_id correlation stashing ───────────────────────────────────────────

from cfgpu_mcp.task_manager import _REQUEST_ID_KEY


@pytest.mark.asyncio
async def test_sync_create_stashes_request_id_stripped_from_payload():
    """A caller-supplied request_id rides the stored payload (so task_status can
    echo it) but is stripped from public_payload — it is never part of the real
    upstream API request."""
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x", request_id="r-sync-1")
    task = await tm.create(adapter, req)
    assert task.payload[_REQUEST_ID_KEY] == "r-sync-1"      # stashed for later echo
    assert _REQUEST_ID_KEY not in task.public_payload()     # never sent upstream
    await db.close()


@pytest.mark.asyncio
async def test_async_create_stashes_request_id():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    req = GenerateVideoInput(prompt="x", request_id="r-async-1")
    task = await tm.create(adapter, req)
    assert task.payload[_REQUEST_ID_KEY] == "r-async-1"
    assert _REQUEST_ID_KEY not in task.public_payload()
    await db.close()


@pytest.mark.asyncio
async def test_create_without_request_id_leaves_payload_clean():
    """No request_id supplied → the reserved key is absent, result shape unchanged."""
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x")
    task = await tm.create(adapter, req)
    assert _REQUEST_ID_KEY not in task.payload
    await db.close()


# ── caption stashing ──────────────────────────────────────────────────────────

from cfgpu_mcp.task_manager import _CAPTION_KEY


@pytest.mark.asyncio
async def test_sync_create_stashes_caption_stripped_from_payload():
    """The label rides the stored payload but is never part of the upstream request."""
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x", caption="角色阿雅 第一版")
    task = await tm.create(adapter, req)
    assert task.payload[_CAPTION_KEY] == "角色阿雅 第一版"
    assert _CAPTION_KEY not in task.public_payload()
    await db.close()


@pytest.mark.asyncio
async def test_async_create_stashes_caption():
    """The async path is the one that needs the stash: the label is supplied here but
    the artifact only appears at task_wait, one tool call later."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    req = GenerateVideoInput(prompt="x", caption="开场镜头 v2")
    task = await tm.create(adapter, req)
    assert task.payload[_CAPTION_KEY] == "开场镜头 v2"
    assert _CAPTION_KEY not in task.public_payload()
    await db.close()


@pytest.mark.asyncio
async def test_caption_does_not_reach_the_posted_body():
    """create() POSTs build_payload()'s clean output; only the stored copy is augmented."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    await tm.create(adapter, GenerateVideoInput(prompt="x", caption="开场镜头 v2", request_id="r-1"))
    posted_body = tm._client_for(None).post.await_args.args[1]
    assert _CAPTION_KEY not in posted_body
    assert _REQUEST_ID_KEY not in posted_body
    await db.close()


@pytest.mark.asyncio
async def test_async_create_stashes_label():
    """Same reason as the caption stash: the name is supplied on generate but the
    artifact it names only appears at task_wait, one tool call later."""
    from cfgpu_mcp.task_manager import _LABEL_KEY
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    req = GenerateVideoInput(prompt="x", label="开场镜头.mp4")
    task = await tm.create(adapter, req)
    assert task.payload[_LABEL_KEY] == "开场镜头.mp4"
    assert _LABEL_KEY not in task.public_payload()
    await db.close()


@pytest.mark.asyncio
async def test_label_does_not_reach_the_posted_body():
    from cfgpu_mcp.task_manager import _LABEL_KEY
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    await tm.create(adapter, GenerateVideoInput(prompt="x", label="开场镜头.mp4"))
    posted_body = tm._client_for(None).post.await_args.args[1]
    assert _LABEL_KEY not in posted_body
    assert "label" not in posted_body
    await db.close()


@pytest.mark.asyncio
async def test_create_without_label_leaves_payload_clean():
    from cfgpu_mcp.task_manager import _LABEL_KEY
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    task = await tm.create(adapter, GenerateImageInput(prompt="x"))
    assert _LABEL_KEY not in task.payload
    await db.close()


@pytest.mark.asyncio
async def test_public_payload_strips_every_echo_key():
    """The strip list is hand-maintained; a fourth echo field added without updating it
    would leak an internal key into the payload every caller sees, silently."""
    from cfgpu_mcp.task_manager import _ECHO_PAYLOAD_KEYS
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    req = GenerateImageInput(prompt="x", request_id="r-1", caption="描述", label="名字.png")
    task = await tm.create(adapter, req)
    assert all(k in task.payload for k in _ECHO_PAYLOAD_KEYS)
    assert not any(k in task.public_payload() for k in _ECHO_PAYLOAD_KEYS)
    await db.close()


@pytest.mark.asyncio
async def test_create_without_caption_leaves_payload_clean():
    tm, db = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    task = await tm.create(adapter, GenerateImageInput(prompt="x"))
    assert _CAPTION_KEY not in task.payload
    await db.close()


# ── _extract_error_message ────────────────────────────────────────────────────

from cfgpu_mcp.task_manager import _extract_error_message


def test_extract_error_nano_failed_has_no_detail():
    # Real CFGPU nano image failure: status under data, no error field at all.
    # Top-level "message" is "success" (query succeeded) and must NOT be used.
    resp = {
        "code": 200,
        "message": "success",
        "data": {"task_id": "t1", "task_type": "nano_generation", "status": "failed", "result": None},
    }
    assert _extract_error_message(resp) is None


def test_extract_error_wan_null_error_does_not_crash():
    # WAN video failure carries error: null — must not raise AttributeError.
    resp = {"id": "t1", "status": "failed", "error": None}
    assert _extract_error_message(resp) is None


def test_extract_error_dict_message():
    resp = {"id": "t1", "status": "failed", "error": {"message": "content_blocked"}}
    assert _extract_error_message(resp) == "content_blocked"


def test_extract_error_string():
    resp = {"id": "t1", "status": "failed", "error": "quota exceeded"}
    assert _extract_error_message(resp) == "quota exceeded"


def test_extract_error_nested_fail_reason():
    resp = {"data": {"status": "failed", "fail_reason": "nsfw detected"}}
    assert _extract_error_message(resp) == "nsfw detected"


def test_extract_error_dashscope_output_code_and_message():
    # HappyHorse / 万相 video: the task record is nested under output, and the
    # reason rides output.code + output.message (both null on success).
    resp = {
        "requestId": "r1",
        "model": "happyhorse-1.0-t2v",
        "output": {
            "taskId": "t1",
            "taskStatus": "FAILED",
            "code": "InvalidParameter.DataInspection",
            "message": "Input data may contain inappropriate content.",
        },
    }
    assert _extract_error_message(resp) == (
        "InvalidParameter.DataInspection: Input data may contain inappropriate content."
    )


def test_extract_error_dashscope_output_code_only():
    resp = {"output": {"taskStatus": "FAILED", "code": "InternalError", "message": None}}
    assert _extract_error_message(resp) == "InternalError"


def test_extract_error_dashscope_output_null_on_success_shape():
    # Success poll carries code/message as null — must yield no reason.
    resp = {
        "output": {
            "taskId": "t1",
            "taskStatus": "SUCCEEDED",
            "videoUrl": "https://cdn.example.com/v.mp4",
            "message": None,
            "code": None,
        }
    }
    assert _extract_error_message(resp) is None


@pytest.mark.asyncio
async def test_poll_wan_null_error_failure_converges():
    """Regression: a WAN-style failed poll with error: null used to crash
    (None.get('message')). It should converge to 'failed' with a fallback msg."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    tm._client_for(None).get = AsyncMock(return_value={"id": "task-abc", "status": "failed", "error": None})
    task = await tm.poll(task, adapter)
    assert task.status == "failed"
    assert task.error and "no error detail" in task.error
    await db.close()


@pytest.mark.asyncio
async def test_poll_treats_an_http_200_body_error_as_terminal():
    """A poll body carrying an upstream error is the task's verdict, not a poll failure.

    The transport worked — HTTP 200, the upstream answered — so the ``error`` object in
    the body describes the *task*. Real case (2026-09-02): a copyright rejection came
    back this way, the client raised on it, ``wait()`` absorbed it as a transient poll
    failure, and the caller was handed ``status: "running"`` plus a ``last_error``
    telling it to keep polling a task nothing upstream would ever advance.

    Note the body carries no ``status`` at all: relying on the status field would leave
    this on ``_STATUS_MAP``'s "running" default, which is exactly the bug.
    """
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc",
        "error": {
            "message": (
                "The request failed because the output video may be related to "
                "copyright restrictions."
            )
        },
    })
    task = await tm.poll(task, adapter)

    assert task.status == "failed"
    assert "copyright restrictions" in task.error
    await db.close()


@pytest.mark.asyncio
async def test_poll_body_error_without_a_parseable_reason_still_fails_with_an_excerpt():
    """The verdict must not depend on this parser recognising the reason field.

    An error object holding only a code would otherwise pass as a running task —
    the silent version of the very failure this path exists to end. The excerpt is
    what makes the unmapped shape diagnosable instead of a dead-end message.
    """
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(
        return_value={"id": "task-abc", "status": "running", "error": {"code": "ContentRisk"}}
    )
    task = await tm.poll(task, adapter)

    assert task.status == "failed"
    assert "ContentRisk" in task.error
    await db.close()


@pytest.mark.asyncio
async def test_wait_raises_task_failed_for_a_body_error_instead_of_reporting_running():
    """The end-to-end shape the caller sees: a terminal error, not a live task.

    Before this, five of these in a row exhausted MAX_CONSECUTIVE_POLL_FAILURES and
    returned ``WaitOutcome(task, last_error)`` — an ``error_type: "unknown"``,
    ``retryable: true`` diagnostic riding a ``status: "running"`` envelope, on a task
    that was already dead.
    """
    tm, db, adapter, req, task = await _waiting_tm(
        [{"id": "task-1", "error": {"message": "copyright restrictions"}}]
    )

    with pytest.raises(CFGPUError) as exc_info:
        await tm.wait(task, adapter, req)

    err = exc_info.value
    assert err.error_type == "task_failed"
    assert err.retryable is False
    assert "copyright restrictions" in err.user_message
    assert err.original["task_id"] == "task-1"
    await db.close()


@pytest.mark.asyncio
async def test_poll_ignores_a_stray_error_field_on_a_succeeded_task():
    """A delivered artifact outranks an error field: the work is done and billed."""
    from cfgpu_mcp.tool_registry import NormalizedResult

    tm, db = await _make_tm()
    adapter = _async_adapter()
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/v.mp4"], expires_at=None, task_id="task-abc",
        model_used="wan-video", seed=None, usage=None,
    )
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(return_value={
        "id": "task-abc", "status": "succeeded", "error": {"message": "partial warning"},
        "content": {"videoUrl": "https://cdn/v.mp4"},
    })
    task = await tm.poll(task, adapter)

    assert task.status == "succeeded"
    assert task.result["urls"] == ["https://cdn/v.mp4"]
    await db.close()


@pytest.mark.parametrize("raw,expected", [
    # Case: the base extract_status hands back whatever the upstream wrote, and only
    # three adapters lower-cased it themselves. "SUCCEEDED" reading as "running" means
    # polling a finished video forever.
    ("SUCCEEDED", "succeeded"),
    ("Failed", "failed"),
    ("success", "succeeded"),
    # Terminal-but-unhappy. Three adapters hand-rolled this collapse (in lists that had
    # already drifted — only grok knew "cancelled"); everyone else, the whole Seedance
    # family included, had nothing.
    ("canceled", "failed"),
    ("cancelled", "failed"),
    ("expired", "failed"),
    ("rejected", "failed"),
    ("timeout", "failed"),
    ("unknown", "failed"),
    # In flight, spelled the ways upstreams actually spell it.
    ("queued", "pending"),
    ("waiting", "pending"),
    ("in_progress", "running"),
    (" Running ", "running"),
])
def test_status_vocabulary_covers_the_terminal_spellings(raw, expected):
    from cfgpu_mcp.task_manager import _normalize_status
    assert _normalize_status(raw) == expected


def test_an_unrecognised_status_is_not_silently_running():
    """None, not "running" — the distinction is what poll() acts on.

    Collapsing the two is what made an unmapped *terminal* status indistinguishable
    from a healthy in-flight one.
    """
    from cfgpu_mcp.task_manager import _normalize_status
    assert _normalize_status("brand_new_state") is None
    assert _normalize_status(None) is None


@pytest.mark.asyncio
async def test_poll_converges_on_a_terminal_status_the_map_did_not_use_to_know():
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(return_value={"id": "task-abc", "status": "CANCELED"})
    task = await tm.poll(task, adapter)

    assert task.status == "failed"
    await db.close()


@pytest.mark.asyncio
async def test_poll_fails_on_an_unknown_status_carrying_a_failure_reason():
    """The DashScope shape reached from the other side: no status this table knows,
    but ``output.code`` says the content was rejected. The reason is not the top-level
    ``error`` the client used to raise on, so nothing else would have caught it."""
    tm, db = await _make_tm()
    adapter = _async_adapter()
    adapter.extract_status.side_effect = lambda r: (r.get("output") or {}).get("taskStatus", "")
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(return_value={
        "output": {"code": "InvalidParameter.DataInspection", "message": "content rejected"},
    })
    task = await tm.poll(task, adapter)

    assert task.status == "failed"
    assert "InvalidParameter.DataInspection" in task.error
    await db.close()


@pytest.mark.asyncio
async def test_an_explicit_running_status_is_believed_even_beside_a_reason_field():
    """The guard on the rule above: only an *unrecognised* status may be overridden.

    Killing a live, billed job over a stale reason field is worse than polling a dead
    one, so an upstream that says "running" is taken at its word.
    """
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-abc"})
    task = await tm.create(adapter, GenerateVideoInput(prompt="x"))

    tm._client_for(None).get = AsyncMock(
        return_value={"id": "task-abc", "status": "running", "reason": "queued behind 3 jobs"}
    )
    task = await tm.poll(task, adapter)

    assert task.status == "running"
    await db.close()
