from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateImageInput


async def generate_image(
    prompt: str,
    model: str | list[str] = "auto",
    aspect_ratio: str = "1:1",
    resolution: str = "2K",
    reference_images: list[str] | None = None,
    n: int = 1,
    quality_tier: str = "balanced",
    watermark: bool | None = None,
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager

    req = GenerateImageInput(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=reference_images,
        n=n,
        quality_tier=quality_tier,
        watermark=watermark,
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req)

    client = get_client()
    repo = await get_task_repository()
    tm = TaskManager(client, repo)

    try:
        task = await tm.create(adapter, req)
    except CFGPUError as e:
        e.adapter_id = adapter.adapter_id
        raise

    if not wait:
        return {"task_id": task.id, "status": task.status}

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.adapter_id = adapter.adapter_id
        raise

    if task.result is None:
        return {"task_id": task.id, "status": task.status}

    result = task.result
    if not return_metadata:
        return {
            "urls": result.get("urls", []),
            "expires_at": result.get("expires_at"),
        }
    return result
