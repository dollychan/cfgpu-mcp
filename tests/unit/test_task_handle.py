"""One handle per task, and it is the one the caller already has.

The production trace this file exists for (2026-09-10): a single generation showed the
model two different values under the same key. The submission receipt carried
``task_id: "c286f37d…"`` — the row key, i.e. the caller's own ``request_id`` — and the
``task_wait`` that collected the artifact carried ``task_id: "47c466e3…"``, the
*upstream's* id, parsed out of the provider response by ``adapter.parse_response`` and
stored verbatim in the task row's ``result``.

Two separate defects, one symptom:

1. ``request-id-durability.md`` I6 says the upstream's handle "does not appear in any
   return to the agent". ``Task.to_dict`` and the pending envelope honour that; the
   *success* envelope never did, because ``_present`` returns ``task.result`` (the
   stored ``NormalizedResult``) rather than anything built from the row.
2. Where a ``request_id`` was supplied it *is* the row key, so the pending envelope was
   handing back the same value twice under two names. Harmless on its own, but it is
   what taught the model that ``task_id`` is a thing worth carrying — and then defect 1
   changed what that thing meant halfway through the job.

So: the caller-facing envelope carries exactly **one** handle. With a ``request_id``
that is ``request_id`` and ``task_id`` is gone; without one it is ``task_id``, and that
is the **row** id — never the upstream's.
"""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.db import _CREATE_TABLE
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.task_manager import _REQUEST_ID_KEY

#: What ``adapter.parse_response`` puts in ``NormalizedResult.task_id`` — the provider's
#: own id for the generation. It is in the stored row on purpose (diagnostics, and the
#: only record of it for sync models, which have no ``upstream_task_id`` column value);
#: it must simply never leave through a tool result.
_UPSTREAM_ID = "47c466e3-d1cf-4ff0-879f-ab2250674ff4"

_STORED_SUCCESS = {
    "urls": ["https://cdn/img.png"],
    "expires_at": None,
    "task_id": _UPSTREAM_ID,
    "model_used": "cf-image-2",
    "aspect_ratio": "3:4",
    "seed": None,
    "usage": None,
}


async def _db_with_task(status: str, *, result=None, error=None, request_id: str | None = None):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE)
    await db.commit()
    payload = {"prompt": "x"}
    if request_id:
        payload[_REQUEST_ID_KEY] = request_id
    await db_ops.insert_task(db, "row-1", "wan-2-0", "pending", payload)
    if status != "pending":
        await db_ops.update_task(db, "row-1", status, result=result, error=error)
    return db


def _adapter():
    adapter = MagicMock()
    adapter.adapter_id = "m"
    adapter.model_name = "m-model"
    adapter.is_async = True
    adapter.poll_endpoint = "/v1/tasks/{task_id}"
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


def _still_running_client():
    client = MagicMock()
    client.get = AsyncMock(return_value={"id": "row-1", "status": "running"})
    return client


# ── The success envelope ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_succeeded_envelope_never_carries_the_upstream_id():
    """The 2026-09-10 trace, pinned: the artifact hop must not rename the job."""
    db = await _db_with_task("succeeded", result=dict(_STORED_SUCCESS), request_id="row-1")
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter())
    with p_db, p_client, p_reg:
        result = await task_service.get_status("row-1")
    assert _UPSTREAM_ID not in str(result)
    assert "task_id" not in result
    assert result["request_id"] == "row-1"
    await db.close()


@pytest.mark.asyncio
async def test_succeeded_envelope_without_a_request_id_falls_back_to_the_row_id():
    """A caller that supplied no request_id still needs a handle — and the only one
    worth handing back is the one it can query with. The upstream id is not that."""
    db = await _db_with_task("succeeded", result=dict(_STORED_SUCCESS))
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter())
    with p_db, p_client, p_reg:
        result = await task_service.get_status("row-1")
    assert result["task_id"] == "row-1"
    assert "request_id" not in result
    await db.close()


@pytest.mark.asyncio
async def test_wait_for_task_success_agrees_with_get_status():
    """Both tools present the same row, so they must present the same handle."""
    db = await _db_with_task("succeeded", result=dict(_STORED_SUCCESS), request_id="row-1")
    client = MagicMock()
    client.get = AsyncMock()
    adapter = _adapter()
    adapter.is_async = False  # terminal already: wait() returns without polling
    p_db, p_client, p_reg = _patch_config(db, client, adapter)
    with p_db, p_client, p_reg:
        result = await task_service.wait_for_task("row-1")
    assert "task_id" not in result
    assert result["request_id"] == "row-1"
    await db.close()


# ── The pending envelope ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_envelope_drops_the_duplicate_handle():
    """`task_id` and `request_id` were the same value under two names (D1)."""
    db = await _db_with_task("pending", request_id="row-1")
    adapter = _adapter()
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, _still_running_client(), adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("row-1")
    assert result["status"] in ("pending", "running")
    assert "task_id" not in result
    assert result["request_id"] == "row-1"
    await db.close()


@pytest.mark.asyncio
async def test_pending_envelope_keeps_task_id_when_there_is_no_request_id():
    """The fallback id is a real handle for CLI / dispatcher callers — not cruft."""
    db = await _db_with_task("pending")
    adapter = _adapter()
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, _still_running_client(), adapter)
    with p_db, p_client, p_reg:
        result = await task_service.get_status("row-1")
    assert result["task_id"] == "row-1"
    await db.close()


# ── The error shape ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_result_carries_one_handle_too():
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("failed", error="content blocked", request_id="row-1")
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter())
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("row-1")
    out = exc.value.to_tool_result_dict()
    assert out["request_id"] == "row-1"
    assert "task_id" not in out
    await db.close()


@pytest.mark.asyncio
async def test_failed_result_keeps_task_id_without_a_request_id():
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("failed", error="content blocked")
    client = MagicMock()
    client.get = AsyncMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter())
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("row-1")
    assert exc.value.to_tool_result_dict()["task_id"] == "row-1"
    await db.close()


@pytest.mark.asyncio
async def test_not_found_error_still_names_the_id_that_was_asked_for():
    """No row means no request_id to echo, and "Task 'x' not found" is only useful
    if it says which x."""
    from cfgpu_mcp.errors import CFGPUError

    db = await _db_with_task("pending")
    client = MagicMock()
    p_db, p_client, p_reg = _patch_config(db, client, _adapter())
    with p_db, p_client, p_reg:
        with pytest.raises(CFGPUError) as exc:
            await task_service.get_status("no-such-row")
    assert exc.value.to_tool_result_dict()["task_id"] == "no-such-row"
    await db.close()


# ── stamp_echo owns the rule ─────────────────────────────────────────────────

def test_stamp_echo_drops_task_id_when_a_request_id_is_present():
    from cfgpu_mcp.tool_registry import stamp_echo
    out = stamp_echo({"urls": ["x"], "task_id": _UPSTREAM_ID}, request_id="r-1", row_id="r-1")
    assert out == {"urls": ["x"], "request_id": "r-1"}


def test_stamp_echo_rewrites_a_stale_task_id_to_the_row_id():
    from cfgpu_mcp.tool_registry import stamp_echo
    out = stamp_echo({"urls": ["x"], "task_id": _UPSTREAM_ID}, row_id="row-1")
    assert out["task_id"] == "row-1"


def test_stamp_echo_never_adds_a_handle_that_was_not_there():
    """`return_metadata=False` asks for the artifact without the metadata — row_id must
    not smuggle a handle back into the lean shape."""
    from cfgpu_mcp.tool_registry import stamp_echo
    assert "task_id" not in stamp_echo({"urls": ["x"]}, row_id="row-1")


def test_stamp_echo_without_row_id_is_unchanged_for_callers_that_pass_none():
    from cfgpu_mcp.tool_registry import stamp_echo
    assert stamp_echo({"task_id": "t"}, caption="c") == {"task_id": "t", "caption": "c"}


# ── The tool schema ──────────────────────────────────────────────────────────

def _mcp_input_schema(tool_name: str) -> dict:
    from cfgpu_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == tool_name).inputSchema


@pytest.mark.parametrize("tool_name", ["task_status", "task_wait"])
def test_task_tools_ask_for_the_request_id(tool_name):
    """The parameter must name the handle the caller actually holds. It used to say
    `task_id`, pointing at a field the success envelope no longer carries."""
    from cfgpu_mcp.tool_registry import get_field_descriptions

    schema = _mcp_input_schema(tool_name)
    assert "request_id" in schema["properties"]
    assert "task_id" not in schema["properties"]
    assert "request_id" in schema.get("required", [])
    # The description is injected from tool_registry (_inject_param_descriptions
    # matches by name, and silently does nothing when the names drift).
    assert schema["properties"]["request_id"]["description"] == get_field_descriptions(tool_name)["request_id"]


@pytest.mark.asyncio
async def test_service_still_accepts_the_legacy_task_id_spelling():
    """Mode B (`dispatch_tool(name, inputs)`) callers pass a dict they built
    themselves, and the CLI's own `cfgpu task status <id>` predates the rename."""
    db = await _db_with_task("pending")
    adapter = _adapter()
    adapter.extract_status.return_value = "running"
    p_db, p_client, p_reg = _patch_config(db, _still_running_client(), adapter)
    with p_db, p_client, p_reg:
        from cfgpu_mcp.agent.dispatcher import dispatch_tool
        result = await dispatch_tool("task_status", {"task_id": "row-1"})
    assert result["task_id"] == "row-1"
    await db.close()


@pytest.mark.asyncio
async def test_service_rejects_a_call_with_neither_spelling():
    from cfgpu_mcp.errors import CFGPUError

    with pytest.raises(CFGPUError) as exc:
        await task_service.get_status()
    assert exc.value.error_type == "invalid_params"
