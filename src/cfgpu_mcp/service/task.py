from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _present(task: Any) -> dict[str, Any]:
    """Shape a Task into a tool result consistent with ``generate_*``.

    On success the flat ``NormalizedResult`` dict (urls/expires_at/metadata at
    the top level) is returned verbatim — identical to what ``generate_image`` /
    ``generate_video`` return — so callers see one structure regardless of which
    tool produced the artifact. Non-terminal / failed tasks fall back to the
    ``{task_id, status[, error]}`` envelope (mirrors generate's ``wait=False``).
    """
    if task.status == "succeeded" and (task.result or {}).get("urls"):
        return task.result
    out: dict[str, Any] = {"task_id": task.id, "status": task.status}
    if task.error:
        out["error"] = task.error
    return out


async def get_status(task_id: str) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    repo = await get_task_repository()
    client = get_client()
    tm = TaskManager(client, repo)
    try:
        task = await tm.status(task_id)
    except KeyError as e:
        from cfgpu_mcp.errors import CFGPUError
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
        from cfgpu_mcp.errors import CFGPUError

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
                    raise
                logger.warning("Re-poll transient failure for task %s (%s): %s", task_id, task.adapter_id, e)
            except Exception as e:
                logger.warning("Re-poll failed for task %s (%s), using stale DB result: %s", task_id, task.adapter_id, e)

    return _present(task)


async def wait_for_task(
    task_id: str,
    timeout: int | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.errors import CFGPUError
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

    # Re-construct a minimal req for timeout estimation (use defaults)
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput
    if adapter.task_type == "image":
        req = GenerateImageInput(prompt="")
    else:
        req = GenerateVideoInput(prompt="")

    task = await tm.wait(task, adapter, req, timeout=timeout)
    return _present(task)
