"""request_id-anchored durable submission — see request-id-durability.md.

The invariant every test here circles is I1: **the task row is written before the
upstream POST**. Everything else in the design is a corollary of it — a row that only
appears after the POST cannot be found by anyone whose connection died during the POST,
which is precisely the interruption that costs money (the billing happens inside those
10–60 seconds).

These are deliberately written against the persisted row rather than the returned Task:
the returned value is exactly what a cancelled or crashed caller never receives, so
asserting on it would test the one path that is known to work.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.repository import SqliteTaskRepository
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import TaskManager, single_client
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput, NormalizedResult


async def _make_tm() -> tuple[TaskManager, aiosqlite.Connection, SqliteTaskRepository]:
    """A TaskManager over a real (in-memory) SQLite, opened through ``open_db`` so the
    schema — including the migration path — is the production one, not a hand-rolled DDL."""
    db = await db_ops.open_db(":memory:")
    repo = SqliteTaskRepository(db)
    return TaskManager(single_client(AsyncMock()), repo), db, repo


def _sync_adapter():
    adapter = MagicMock()
    adapter.adapter_id = "doubao-seedream-5-0-lite"
    adapter.model_name = "doubao-seedream-5-0-lite"
    adapter.task_type = "image"
    adapter.is_async = False
    adapter.endpoint = "/v1/images/generations"
    adapter.build_payload.return_value = {"model": "test", "prompt": "x"}
    adapter.parse_response.return_value = NormalizedResult(
        urls=["https://cdn/img.jpg"],
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        task_id=None, model_used="test", seed=None, usage=None,
    )
    return adapter


def _async_adapter(upstream_id: str = "cfgpu-task-1"):
    adapter = MagicMock()
    adapter.adapter_id = "wan-2-0"
    adapter.model_name = "wan-video"
    adapter.task_type = "video"
    adapter.is_async = True
    adapter.endpoint = "/v1/video/tasks"
    adapter.poll_endpoint = "/v1/video/tasks/{task_id}"
    adapter.build_payload.return_value = {"model": "wan-video"}
    adapter.poll_config = MagicMock(base_interval=0.01, max_interval=0.05, backoff_factor=1.0)
    adapter.estimate_poll_timeout.return_value = 5
    adapter.extract_task_id.return_value = upstream_id
    adapter.extract_eta.return_value = None
    adapter.extract_status.side_effect = lambda r: r.get("status", "running")
    return adapter


# ── I1 / D4: the row exists, and says what it can prove, before the POST ──────────


async def test_row_exists_before_upstream_post():
    """I1 + D4. Asserted *inside* the POST, because that is where the interruption lands.

    The status matters as much as the row: `dispatching` is the design's word for "I
    cannot prove this was never sent", and it has to be already written when the bytes
    go out — a row that says `submitting` while the POST is in flight would tell a
    recovering caller that resending is safe when it is not.
    """
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    seen = {}

    async def _post(endpoint, payload):
        seen["row"] = await repo.get_task("req-1")
        return {"data": [{"url": "https://cdn/img.jpg"}]}

    tm._client_for(None).post = _post
    await tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-1"))

    assert seen["row"] is not None, "任务行必须在 POST 之前落库"
    assert seen["row"]["status"] == "dispatching"
    await db.close()


async def test_task_id_is_request_id_when_supplied():
    """D1: the caller's handle *is* the primary key — no second id to remember."""
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "u"}]})

    task = await tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-abc"))

    assert task.id == "req-abc"
    assert (await repo.get_task("req-abc"))["status"] == "succeeded"
    await db.close()


async def test_task_id_falls_back_to_uuid_without_request_id():
    """D1's fallback: CLI / dispatcher callers don't send request_id and must still work.

    This is also what lets the MCP side ship independently of the host-side pin (§9.1).
    """
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(return_value={"data": [{"url": "u"}]})

    task = await tm.create(adapter, GenerateImageInput(prompt="x"))

    assert task.id and task.status == "succeeded"
    assert await repo.get_task(task.id) is not None
    await db.close()


# ── I5 / D5: the already-billed write-back is outside the caller's cancel scope ───


async def test_cancelled_request_still_writes_result():
    """I5. The agent disconnects mid-POST; the money is already spent either way.

    Without the shield the write-back is cancelled along with the caller's request and
    the artifact is lost with it. With it, the caller comes back later with the
    request_id it knew *before* it ever called, and finds the result.
    """
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    released = asyncio.Event()

    async def _post(endpoint, payload):
        await released.wait()
        return {"data": [{"url": "https://cdn/img.jpg"}]}

    tm._client_for(None).post = _post

    call = asyncio.create_task(
        tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-cancel"))
    )
    for _ in range(200):  # let create() reach the shielded POST
        if await repo.get_task("req-cancel"):
            break
        await asyncio.sleep(0.01)

    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    released.set()
    for _ in range(200):  # the shielded half is still running; give it the loop
        row = await repo.get_task("req-cancel")
        if row and row["status"] == "succeeded":
            break
        await asyncio.sleep(0.01)

    assert row["status"] == "succeeded"
    assert row["result"]["urls"] == ["https://cdn/img.jpg"]
    await db.close()


# ── D2: a primary-key conflict is a dedup signal, not an error ───────────────────


async def test_duplicate_request_id_returns_existing_live_task():
    """D2, non-terminal half: the first submission is still running — do not send again."""
    tm, db, repo = await _make_tm()
    adapter = _async_adapter()
    post = AsyncMock(return_value={"id": "cfgpu-task-1"})
    tm._client_for(None).post = post

    req = GenerateVideoInput(prompt="x", request_id="req-dup")
    first = await tm.create(adapter, req)
    second = await tm.create(adapter, req)

    assert post.await_count == 1, "同一个 request_id 第二次到达不得再发一次 POST"
    assert second.id == first.id == "req-dup"
    assert second.status == "pending"
    await db.close()


async def test_duplicate_request_id_returns_cached_terminal_result():
    """D2, terminal half: the artifact already exists — returning it beats paying twice.

    This is the half that closes the checkpoint-replay double charge (BUG-021's
    same-AIMessage path), and the half that depends on the host baking the request_id
    into the message rather than recomputing it (D10).
    """
    tm, db, _ = await _make_tm()
    adapter = _sync_adapter()
    post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    tm._client_for(None).post = post

    req = GenerateImageInput(prompt="x", request_id="req-replay")
    first = await tm.create(adapter, req)
    replay = await tm.create(adapter, req)

    assert post.await_count == 1, "重放同一个 request_id 不得二次计费"
    assert replay.status == "succeeded"
    assert replay.result == first.result
    await db.close()


# ── D6 / I6: upstream_task_id is internal ────────────────────────────────────────


async def test_upstream_task_id_never_surfaces_to_caller():
    """I6. The agent's whole vocabulary is request_id; a second id it cannot obtain
    during recovery (it only exists once the response came back) is worse than none."""
    from cfgpu_mcp.service.task import _present
    from cfgpu_mcp.task_manager import Task

    tm, db, repo = await _make_tm()
    adapter = _async_adapter("cfgpu-upstream-9")
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-upstream-9"})

    task = await tm.create(adapter, GenerateVideoInput(prompt="x", request_id="req-hidden"))

    assert task.upstream_task_id == "cfgpu-upstream-9"      # internally present
    assert "cfgpu-upstream-9" not in str(task.to_dict())     # …and nowhere in the output
    assert "cfgpu-upstream-9" not in str(_present(task))
    assert "cfgpu-upstream-9" not in str(Task(await repo.get_task("req-hidden")).to_dict())

    # The half this test used to miss: everything above holds while the task has no
    # `result`, and _present's *success* branch does not go through `Task.to_dict()` at
    # all — it returns the stored NormalizedResult, whose own `task_id` is the upstream's
    # (adapter.parse_response put it there). That is the path that leaked, in production,
    # on 2026-09-10. See D13; the rule itself is pinned in test_task_handle.py.
    await repo.update_task(
        "req-hidden", "succeeded",
        result={"urls": ["https://cdn/v.mp4"], "task_id": "cfgpu-upstream-9"},
    )
    done = Task(await repo.get_task("req-hidden"))
    assert "cfgpu-upstream-9" not in str(_present(done))
    await db.close()


async def test_async_row_polls_by_upstream_task_id():
    """D6. The poll URL is built from the upstream id, never from the row's own key."""
    tm, db, _ = await _make_tm()
    adapter = _async_adapter("cfgpu-upstream-9")
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-upstream-9"})
    get = AsyncMock(return_value={"status": "running"})
    tm._client_for(None).get = get

    task = await tm.create(adapter, GenerateVideoInput(prompt="x", request_id="req-poll"))
    await tm.poll(task, adapter)

    assert get.await_args.args[0] == "/v1/video/tasks/cfgpu-upstream-9"
    await db.close()


async def test_legacy_row_polls_by_id():
    """I7. Rows written before this change have `upstream_task_id IS NULL` and an `id`
    that *is* the upstream id. `or id` is what keeps them pollable; they are not
    backfilled, so this fallback is permanent, not transitional."""
    tm, db, repo = await _make_tm()
    adapter = _async_adapter()
    await repo.insert_task("cfgpu-legacy-7", "wan-2-0", "pending", {"prompt": "x"})
    get = AsyncMock(return_value={"status": "running"})
    tm._client_for(None).get = get

    await tm.poll(await tm.status("cfgpu-legacy-7"), adapter)

    assert get.await_args.args[0] == "/v1/video/tasks/cfgpu-legacy-7"
    await db.close()


async def test_lookup_falls_back_to_upstream_task_id():
    """D6's second hop. Only reached on a primary-key miss, so it costs a query only
    for callers holding an upstream id — which is what a caller that submitted before
    this change holds."""
    tm, db, repo = await _make_tm()
    adapter = _async_adapter("cfgpu-upstream-2")
    tm._client_for(None).post = AsyncMock(return_value={"id": "cfgpu-upstream-2"})
    await tm.create(adapter, GenerateVideoInput(prompt="x", request_id="req-two-hop"))

    row = await repo.get_task("cfgpu-upstream-2")

    assert row is not None and row["id"] == "req-two-hop"
    await db.close()


# ── §7.1 / D8: what a submission that could not complete leaves behind ───────────


async def test_async_missing_upstream_id_marks_row_failed():
    """§7.1. The POST succeeded (so it was billed) but the handle is unparseable.

    Before this change the raise left nothing at all; the row is the whole point —
    without it the money is spent under an id that exists only upstream.
    """
    tm, db, repo = await _make_tm()
    adapter = _async_adapter()
    adapter.extract_task_id.return_value = None
    tm._client_for(None).post = AsyncMock(return_value={"unexpected": "shape"})

    with pytest.raises(CFGPUError) as exc:
        await tm.create(adapter, GenerateVideoInput(prompt="x", request_id="req-lost"))

    assert exc.value.error_type == "submission_lost"
    assert exc.value.retryable is False          # D8: resending risks a second charge
    row = await repo.get_task("req-lost")
    assert row["status"] == "failed" and row["error"]
    assert exc.value.to_tool_result_dict()["task_id"] == "req-lost"
    await db.close()


async def test_refused_submission_converges_to_failed():
    """A 4xx is the upstream *answering*: nothing was started, nothing billed.

    Leaving such a row in `dispatching` would tell a later query "this may have cost
    you money, do not resend" — the opposite of the truth, and the one direction of
    error that makes a caller abandon work it could simply retry.
    """
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(
        side_effect=CFGPUError(error_type="invalid_params", user_message="bad prompt")
    )

    with pytest.raises(CFGPUError):
        await tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-4xx"))

    assert (await repo.get_task("req-4xx"))["status"] == "failed"
    await db.close()


async def test_indeterminate_post_stays_dispatching_and_names_the_next_step():
    """D8 + D9. A POST whose answer never came back is the case that used to be
    unrecoverable: `outcome_unknown` said "may have billed, nothing to reconcile".

    There *is* something to reconcile now, so the flag comes off and the message names
    the call that reconciles it. The row stays non-terminal on purpose — `dispatching`
    is a claim about what can be proved, and nothing here proves anything.
    """
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(
        side_effect=CFGPUError(
            error_type="timeout", user_message="请求超时",
            original={"phase": "request", "method": "POST"},
            retryable=False, outcome_unknown=True,
        )
    )

    with pytest.raises(CFGPUError) as exc:
        await tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-indet"))

    assert (await repo.get_task("req-indet"))["status"] == "dispatching"
    assert exc.value.outcome_unknown is False
    assert "req-indet" in exc.value.user_message
    assert "task_status" in exc.value.user_message
    await db.close()


async def test_connect_timeout_is_safe_to_resend():
    """The other half of the same fork: DNS/TCP/TLS never completed, so the upstream
    received nothing. Saying "may have billed" here would be a false alarm."""
    tm, db, repo = await _make_tm()
    adapter = _sync_adapter()
    tm._client_for(None).post = AsyncMock(
        side_effect=CFGPUError(
            error_type="timeout", user_message="连不上",
            original={"phase": "connect", "method": "POST"}, retryable=True,
        )
    )

    with pytest.raises(CFGPUError):
        await tm.create(adapter, GenerateImageInput(prompt="x", request_id="req-connect"))

    assert (await repo.get_task("req-connect"))["status"] == "failed"
    await db.close()


# ── §6 judgement points ──────────────────────────────────────────────────────────


async def test_pre_upstream_rows_are_listed_as_running():
    """`list_running_tasks` is the sweeper's only window (Phase 2). A status it does not
    select is a row nothing will ever converge."""
    tm, db, repo = await _make_tm()
    await repo.insert_task("a", "m", "submitting", {})
    await repo.insert_task("b", "m", "dispatching", {})
    await repo.insert_task("c", "m", "pending", {})
    await repo.insert_task("d", "m", "succeeded", {})

    assert {r["id"] for r in await repo.list_running_tasks()} == {"a", "b", "c"}
    await db.close()


async def test_dispatching_is_not_repolled():
    """§6. There is no upstream id yet, so a poll would ask about a task_id the upstream
    has never seen — a 404 reported as though the job had failed."""
    from cfgpu_mcp.service import task as task_service

    tm, db, repo = await _make_tm()
    await repo.insert_task("req-disp", "m", "dispatching", {})
    client = MagicMock()
    client.get = AsyncMock()
    registry = MagicMock()
    registry.get.return_value = _async_adapter()

    with (
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock(return_value=repo)),
        patch("cfgpu_mcp.config.client_for", MagicMock(return_value=client)),
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=registry)),
    ):
        out = await task_service.get_status("req-disp")

    client.get.assert_not_called()
    assert out["status"] == "dispatching"
    assert out["task_id"] == "req-disp"
    await db.close()


async def test_wait_on_a_pre_upstream_row_refreshes_instead_of_polling():
    """`task_wait(request_id)` can land on a row whose POST is still in flight in some
    other worker. The row is the only thing that can advance it, so re-read the row."""
    tm, db, repo = await _make_tm()
    adapter = _async_adapter()
    client = MagicMock()
    client.get = AsyncMock()
    tm._client_for = lambda _a: client

    await repo.insert_task("req-wait", "wan-2-0", "dispatching", {})
    task = await tm.status("req-wait")

    async def _land_the_row():
        await asyncio.sleep(0.02)
        await repo.update_task("req-wait", "succeeded", result={"urls": ["u"]})

    lander = asyncio.create_task(_land_the_row())
    outcome = await tm.wait(task, adapter, GenerateVideoInput(prompt="x"), timeout=5)
    await lander

    client.get.assert_not_called()
    assert outcome.task.status == "succeeded"
    await db.close()


# ── schema ───────────────────────────────────────────────────────────────────────


async def test_open_db_migrates_a_legacy_table():
    """The column is added to an existing table, not only to a freshly created one —
    every deployment that matters already has the table."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, adapter_id TEXT NOT NULL,"
        " status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, error TEXT,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    await db.commit()

    await db_ops.migrate_schema(db)

    cols = {r["name"] async for r in await db.execute("PRAGMA table_info(tasks)")}
    assert "upstream_task_id" in cols
    await db.close()


async def test_insert_reports_whether_it_inserted():
    """The repository, not the caller, owns the conflict semantics — the caller only
    needs to know which of the two happened."""
    db = await db_ops.open_db(":memory:")
    repo = SqliteTaskRepository(db)

    assert await repo.insert_task("x", "m", "submitting", {"p": 1}) is True
    assert await repo.insert_task("x", "m", "submitting", {"p": 2}) is False
    assert (await repo.get_task("x"))["payload"] == {"p": 1}, "冲突不得覆盖既有行"
    await db.close()
