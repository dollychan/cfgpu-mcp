from __future__ import annotations

from typing import Any

from cfgpu_mcp.tool_registry import GenerateImageInput


async def generate_image(
    prompt: str,
    model: str = "auto",
    aspect_ratio: str = "1:1",
    resolution: str = "1K",
    reference_images: list[str] | None = None,
    quality_tier: str = "balanced",
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = False,
    model_specific: dict | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_db, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager

    req = GenerateImageInput(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=reference_images,
        quality_tier=quality_tier,
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.get_adapter(model) if model != "auto" else router.select_model(req)

    client = get_client()
    db = await get_db()
    tm = TaskManager(client, db)

    task = await tm.create(adapter, req)

    if not wait:
        return {"task_id": task.id, "status": task.status}

    task = await tm.wait(task, adapter, req, timeout=timeout)

    if task.result is None:
        return {"task_id": task.id, "status": task.status}

    result = task.result
    if not return_metadata:
        return {
            "urls": result.get("urls", []),
            "expires_at": result.get("expires_at"),
        }
    return result
