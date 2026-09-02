from __future__ import annotations

import logging
from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import _CAPTION_KEY, _LABEL_KEY, _REQUEST_ID_KEY
from cfgpu_mcp.tool_registry import pending_result, stamp_echo

logger = logging.getLogger(__name__)


def _present(task: Any, last_error: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shape a Task into a tool result consistent with ``generate_*``.

    On success the flat ``NormalizedResult`` dict (urls/expires_at/metadata at
    the top level), plus the real per-model API ``payload``, is returned —
    identical to what ``generate_image`` / ``generate_video`` return — so callers
    see one structure regardless of which tool produced the artifact. Non-terminal
    tasks fall back to the ``{task_id, status}`` envelope (mirrors generate's
    ``wait=False``). The caller's echo fields — ``request_id``, ``caption`` and
    ``label``, all stashed in the stored payload at create time — are echoed on both
    shapes, so an async artifact can be joined back to the originating generate_* request
    and carries the description and name that request gave it. This is the whole point of
    stashing them: they are supplied on ``generate_*`` but the artifact only exists here,
    one tool call later.
    Failed tasks are surfaced by raising ``CFGPUError`` — see ``_raise_if_failed`` — so
    the error shape matches ``task_wait`` and the ``generate_*`` tools exactly. Note the
    asymmetry is deliberate and only looks lopsided: *failed* is terminal and carries a
    remedy (error_type / retryable / card_hint), while *still running* is not a failure
    at all, so only the first belongs in the error channel.

    ``last_error`` is passed only by ``wait_for_task``, and only when the wait stopped
    early for a reason (see ``TaskManager.wait``). ``get_status`` never sets it: a single
    re-poll that fails there already falls back to the stale record on purpose.

    "Has an artifact" is ``urls`` **or** ``inline_media``, matching
    ``annotate_artifact`` and ``TaskManager.poll``'s success guard: a synchronous model
    that returns media inline instead of a URL (MiniMax speech) is still reachable here
    via ``generate_audio(wait=False) → task_status``, and testing ``urls`` alone would
    demote that finished task to the bare ``{task_id, status}`` envelope, dropping the
    audio the caller came for.
    """
    request_id = task.payload.get(_REQUEST_ID_KEY)
    caption = task.payload.get(_CAPTION_KEY)
    label = task.payload.get(_LABEL_KEY)
    result = task.result or {}
    if task.status == "succeeded" and (result.get("urls") or result.get("inline_media")):
        return stamp_echo({**task.result, "payload": task.public_payload()}, request_id=request_id, caption=caption, label=label)
    return stamp_echo(
        pending_result(task.id, task.status, last_error),
        request_id=request_id, caption=caption, label=label,
    )


class _AsFailed:
    """A read-only view of a task, presented as failed. Nothing is written back — the
    stored row is left exactly as upstream reported it, so the discrepancy stays
    diagnosable instead of being papered over by the read path."""

    def __init__(self, task: Any, error: str) -> None:
        self._task = task
        self.status = "failed"
        self.error = error

    def __getattr__(self, name: str) -> Any:
        return getattr(self._task, name)


def _raise_if_failed(task: Any) -> None:
    """Raise a standard ``task_failed`` CFGPUError for a task that cannot deliver.

    Keeps the failure contract identical across ``task_status`` / ``task_wait`` /
    ``generate_*`` (all produce ``{error: True, error_type, message, retryable,
    model_id}`` via ``tool_error_dict``), instead of ``task_status`` alone
    emitting a divergent ``{status: "failed", error: "<string>"}`` envelope.

    "Succeeded with no artifact" counts as failed here for the same reason it does in
    ``TaskManager.poll``: it is terminal, and it produced nothing. Without this it fell
    through to the non-terminal envelope carrying ``status: "succeeded"`` — a shape the
    result contract reads as "not done yet, poll again", which for a terminal row means
    polling forever. ``get_status`` deliberately does not re-poll a terminal row (the
    row is malformed data, not work in flight), so the presentation layer is where this
    has to converge. Unreachable for rows written by the current code — both
    ``create()`` and ``poll()`` already convert it at write time — but a row predating
    those guards is exactly the kind of thing that turns into an infinite poll loop.
    """
    if task.status == "succeeded" and not (
        (task.result or {}).get("urls") or (task.result or {}).get("inline_media")
    ):
        task = _AsFailed(task, "Task reported success but returned no artifact URLs or inline media")
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
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    repo = await get_task_repository()
    tm = TaskManager(client_for, repo)
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
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    repo = await get_task_repository()
    registry = get_registry()
    tm = TaskManager(client_for, repo)

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
        task, last_error = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = task.payload.get(_REQUEST_ID_KEY)
        raise
    _raise_if_failed(task)
    return _present(task, last_error)
