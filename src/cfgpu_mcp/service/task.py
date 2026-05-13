from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_status(task_id: str) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_db, get_registry
    from cfgpu_mcp.task_manager import TaskManager

    db = await get_db()
    client = get_client()
    tm = TaskManager(client, db)
    try:
        task = await tm.status(task_id)
    except KeyError as e:
        from cfgpu_mcp.errors import CFGPUError
        raise CFGPUError(
            error_type="invalid_params",
            user_message=f"Task {task_id!r} not found.",
            original={"task_id": task_id},
        ) from e

    # Re-poll from API if task succeeded but result has no URLs (e.g. stale DB record)
    if task.status == "succeeded" and not (task.result or {}).get("urls"):
        registry = get_registry()
        try:
            adapter = registry.get(task.adapter_id)
            task = await tm.poll(task, adapter)
        except Exception as e:
            logger.debug("Re-poll failed for task %s (%s), using stale DB result: %s", task_id, task.adapter_id, e)

    return task.to_dict()


async def wait_for_task(
    task_id: str,
    timeout: int | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_db, get_registry
    from cfgpu_mcp.errors import CFGPUError
    from cfgpu_mcp.task_manager import TaskManager

    db = await get_db()
    client = get_client()
    registry = get_registry()
    tm = TaskManager(client, db)

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
    return task.to_dict()
