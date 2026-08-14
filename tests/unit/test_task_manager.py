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

    result = await tm.wait(task, adapter, req)
    assert not poll_called
    assert result.status == "succeeded"
    await db.close()


@pytest.mark.asyncio
async def test_wait_times_out_and_raises():
    from cfgpu_mcp.errors import CFGPUError
    tm, db = await _make_tm()
    adapter = _async_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"id": "task-timeout"})
    req = GenerateVideoInput(prompt="x")
    task = await tm.create(adapter, req)

    tm._client_for(None).get = AsyncMock(return_value={"id": "task-timeout", "status": "running"})
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
