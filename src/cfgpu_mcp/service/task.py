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

    # Re-poll from API if an async task succeeded but its result has no URLs
    # (e.g. stale DB record). Sync models have no poll_endpoint, so skip them.
    if task.status == "succeeded" and not (task.result or {}).get("urls"):
        registry = get_registry()
        try:
            adapter = registry.get(task.adapter_id)
            if adapter.is_async:
                task = await tm.poll(task, adapter)
        except Exception as e:
            logger.debug("Re-poll failed for task %s (%s), using stale DB result: %s", task_id, task.adapter_id, e)

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
