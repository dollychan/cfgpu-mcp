from __future__ import annotations

import logging
from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import _CAPTION_KEY, _REQUEST_ID_KEY
from cfgpu_mcp.tool_registry import stamp_echo

logger = logging.getLogger(__name__)


def _present(task: Any) -> dict[str, Any]:
    """Shape a Task into a tool result consistent with ``generate_*``.

    On success the flat ``NormalizedResult`` dict (urls/expires_at/metadata at
    the top level), plus the real per-model API ``payload``, is returned —
    identical to what ``generate_image`` / ``generate_video`` return — so callers
    see one structure regardless of which tool produced the artifact. Non-terminal
    tasks fall back to the ``{task_id, status}`` envelope (mirrors generate's
    ``wait=False``). The caller's echo fields — ``request_id`` and ``caption``, both
    stashed in the stored payload at create time — are echoed on both shapes, so an
    async artifact can be joined back to the originating generate_* request and carries
    the label that request gave it. This is the whole point of stashing the caption: it
    is supplied on ``generate_*`` but the artifact only exists here, one tool call later.
    Failed tasks are surfaced by raising ``CFGPUError`` — see ``_raise_if_failed`` — so
    the error shape matches ``task_wait`` and the ``generate_*`` tools exactly.
    """
    request_id = task.payload.get(_REQUEST_ID_KEY)
    caption = task.payload.get(_CAPTION_KEY)
    if task.status == "succeeded" and (task.result or {}).get("urls"):
        return stamp_echo({**task.result, "payload": task.public_payload()}, request_id=request_id, caption=caption)
    return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption)


def _raise_if_failed(task: Any) -> None:
    """Raise a standard ``task_failed`` CFGPUError for a failed task.

    Keeps the failure contract identical across ``task_status`` / ``task_wait`` /
    ``generate_*`` (all produce ``{error: True, error_type, message, retryable,
    model_id}`` via ``tool_error_dict``), instead of ``task_status`` alone
    emitting a divergent ``{status: "failed", error: "<string>"}`` envelope.
    """
    if task.status == "failed":
        from cfgpu_mcp.config import get_registry
        # Expose the agent-facing model_id (model_name), not the internal
        # adapter_id stored on the task row.
        model_id: str | None = None
        try:
            model_id = get_registry().get(task.adapter_id).model_name
        except KeyError:
            pass
        raise CFGPUError(
            error_type="task_failed",
            user_message=task.error or "Task failed without error message",
            original={"task_id": task.id},
            model_id=model_id,
            request_id=task.payload.get(_REQUEST_ID_KEY),
        )


async def get_status(task_id: str) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    repo = await get_task_repository()
    client = get_client()
    tm = TaskManager(client, repo)
    try:
        task = await tm.status(task_id)
    except KeyError as e:
        raise CFGPUError(
            error_type="invalid_params",
            user_message=f"Task {task_id!r} not found.",
            original={"task_id": task_id},
        ) from e

    # Client-driven polling: each task_status call carries the caller's token,
    # so we do ONE live upstream poll to advance the task — this is what lets a
    # wait=False submitter (or a reconnecting client) drive an async task to
    # completion without the server holding a connection open.
    #
    # Re-poll only while the task is still in flight. succeeded / failed are
    # terminal: a succeeded-without-urls row is malformed data that poll()
    # converges to "failed" at write time, not something to retry on every read.
    needs_repoll = task.status in ("pending", "running")
    if needs_repoll:
        registry = get_registry()
        adapter = registry.get(task.adapter_id)  # missing adapter is a program error, not stale-tolerable
        if adapter.is_async:
            try:
                task = await tm.poll(task, adapter)
            except CFGPUError as e:
                # Auth / bad params are caller-fixable — surface them instead of
                # masquerading as "still running". Transient network/timeout
                # errors are tolerated: return the stale record so polling retries.
                if e.error_type in ("auth", "invalid_params"):
                    e.request_id = task.payload.get(_REQUEST_ID_KEY)
                    raise
                logger.warning("Re-poll transient failure for task %s (%s): %s", task_id, task.adapter_id, e)
            except Exception as e:
                logger.warning("Re-poll failed for task %s (%s), using stale DB result: %s", task_id, task.adapter_id, e)

    _raise_if_failed(task)
    return _present(task)


async def wait_for_task(
    task_id: str,
    timeout: int | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    repo = await get_task_repository()
    client = get_client()
    registry = get_registry()
    tm = TaskManager(client, repo)

    try:
        task = await tm.status(task_id)
    except KeyError as e:
        raise CFGPUError(
            error_type="invalid_params",
            user_message=f"Task {task_id!r} not found.",
            original={"task_id": task_id},
        ) from e

    adapter = registry.get(task.adapter_id)

    # Re-construct a minimal req for timeout estimation (use defaults). Match the
    # adapter's task_type exactly so a per-type estimate_poll_timeout() override
    # (e.g. the video adapters' `assert isinstance(req, GenerateVideoInput)`)
    # never receives the wrong Input type. Note GenerateAudioInput's required
    # field is `text`, not `prompt`.
    from cfgpu_mcp.tool_registry import (
        GenerateAudioInput,
        GenerateImageInput,
        GenerateVideoInput,
        UnderstandVisionInput,
    )
    if adapter.task_type == "image":
        req = GenerateImageInput(prompt="")
    elif adapter.task_type == "audio":
        req = GenerateAudioInput(text="")
    elif adapter.task_type == "understand":
        req = UnderstandVisionInput(prompt="")
    else:
        req = GenerateVideoInput(prompt="")

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = task.payload.get(_REQUEST_ID_KEY)
        raise
    return _present(task)
