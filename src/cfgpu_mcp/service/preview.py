from __future__ import annotations

from typing import Any

from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput


def _build_preview(adapter: Any, req: Any) -> dict[str, Any]:
    payload = adapter.build_payload(req)
    return {
        "dry_run": True,
        "model": adapter.adapter_id,
        "display_name": adapter.display_name,
        "cost_tier": adapter.cost_tier,
        "speed_tier": adapter.speed_tier,
        "is_async": adapter.is_async,
        "estimated_seconds": adapter.estimate_poll_timeout(req),
        "payload": payload,
    }


def _resolve_adapter(req: GenerateImageInput | GenerateVideoInput, model: str) -> Any:
    from cfgpu_mcp.config import get_registry
    from cfgpu_mcp.router import ModelRouter

    registry = get_registry()
    router = ModelRouter(registry)
    return router.get_adapter(model) if model != "auto" else router.select_model(req)


async def preview_generate_image(
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
    adapter = _resolve_adapter(req, model)
    return _build_preview(adapter, req)


async def preview_generate_video(
    prompt: str,
    model: str = "auto",
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
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = False,
    model_specific: dict | None = None,
) -> dict[str, Any]:
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
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
    )
    adapter = _resolve_adapter(req, model)
    return _build_preview(adapter, req)
