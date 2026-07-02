from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateAudioInput


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
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager

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
        e.model_id = adapter.cfgpu_model_id
        raise

    if not wait:
        return {"task_id": task.id, "status": task.status}

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.cfgpu_model_id
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
