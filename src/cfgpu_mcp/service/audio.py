from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateAudioInput, lean_result, stamp_echo


async def generate_audio(
    text: str,
    model: str | list[str] = "auto",
    voice: str | None = None,
    audio_format: str = "mp3",
    sample_rate: int | None = None,
    bitrate: int | None = None,
    speed: float = 1.0,
    volume: float = 1.0,
    pitch: int = 0,
    emotion: str | None = None,
    quality_tier: str = "balanced",
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
    request_id: str | None = None,
    caption: str | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager, validate_request

    req = GenerateAudioInput(
        text=text,
        model=model,
        voice=voice,
        audio_format=audio_format,
        sample_rate=sample_rate,
        bitrate=bitrate,
        speed=speed,
        volume=volume,
        pitch=pitch,
        emotion=emotion,
        quality_tier=quality_tier,
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
        request_id=request_id,
        caption=caption,
        validate_only=validate_only,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req, for_validation=validate_only)

    if validate_only:
        # Branches before the repository is acquired so an unsubmitted request leaves no
        # task row; see the same block in service/image.py for the full rationale.
        try:
            preflight = validate_request(adapter, req)
        except CFGPUError as e:
            e.model_id = adapter.model_name
            e.request_id = request_id
            raise
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
        return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption)

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = request_id
        raise

    if task.result is None:
        return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption)

    result = task.result
    # The real per-model API request is always surfaced, regardless of return_metadata.
    payload = task.public_payload()
    if not return_metadata:
        return stamp_echo(lean_result(result, payload), request_id=request_id, caption=caption)
    return stamp_echo({**result, "payload": payload}, request_id=request_id, caption=caption)
