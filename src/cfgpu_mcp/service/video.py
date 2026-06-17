from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateVideoInput


async def generate_video(
    prompt: str,
    model: str | list[str] = "auto",
    first_frame: str | None = None,
    last_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration_seconds: int = 5,
    aspect_ratio: str = "adaptive",
    resolution: str = "720p",
    with_audio: bool = True,
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

    req = GenerateVideoInput(
        prompt=prompt,
        model=model,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        with_audio=with_audio,
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
    # The real per-model API request is always surfaced, regardless of return_metadata.
    payload = task.public_payload()
    if not return_metadata:
        return {
            "urls": result.get("urls", []),
            "expires_at": result.get("expires_at"),
            "payload": payload,
        }
    return {**result, "payload": payload}
