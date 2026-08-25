from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateImageInput, RegionSpec, lean_result, stamp_echo


async def generate_image(
    prompt: str,
    model: str | list[str] = "auto",
    aspect_ratio: str = "1:1",
    resolution: str = "2K",
    reference_images: list[str] | None = None,
    regions: list[RegionSpec] | list[dict] | None = None,
    image_refs: list[str] | None = None,
    n: int = 1,
    quality_tier: str = "balanced",
    watermark: bool = False,
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
    request_id: str | None = None,
    caption: str | None = None,
    label: str | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager, validate_request

    req = GenerateImageInput(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=reference_images,
        regions=regions,
        image_refs=image_refs,
        n=n,
        quality_tier=quality_tier,
        watermark=watermark,
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
        request_id=request_id,
        caption=caption,
        label=label,
        validate_only=validate_only,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req, for_validation=validate_only)

    if validate_only:
        # Before the repository is acquired: a request that is never submitted must not
        # leave a task row behind. Errors are stamped exactly as on the billed path, so
        # a preflight failure is indistinguishable from the real one.
        try:
            preflight = validate_request(adapter, req)
        except CFGPUError as e:
            e.model_id = adapter.model_name
            e.request_id = request_id
            raise
        # request_id only: it joins this preflight to the caller's request. `caption`
        # and `label` describe and name an artifact, and a preflight produced none — the
        # same asymmetry CFGPUError already encodes.
        return stamp_echo(preflight, request_id=request_id)

    repo = await get_task_repository()
    tm = TaskManager(client_for, repo)

    try:
        task = await tm.create(adapter, req)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = request_id
        raise

    if not wait:
        return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption, label=label)

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = request_id
        raise

    if task.result is None:
        return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption, label=label)

    result = task.result
    # The real per-model API request is always surfaced, regardless of return_metadata.
    payload = task.public_payload()
    if not return_metadata:
        return stamp_echo(lean_result(result, payload), request_id=request_id, caption=caption, label=label)
    return stamp_echo({**result, "payload": payload}, request_id=request_id, caption=caption, label=label)
