from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import audio as audio_service
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import video as video_service
from cfgpu_mcp.tool_registry import annotate_artifact, split_structured


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def generate_image(
        prompt: str,
        model: str | list[str] = "auto",
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
        reference_images: Optional[list[str]] = None,
        n: int = 1,
        quality_tier: str = "balanced",
        watermark: Optional[bool] = None,
        wait: bool = True,
        timeout: Optional[int] = None,
        return_metadata: bool = True,
        model_specific: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Generate image from text prompt using CFGPU models."""
        try:
            return split_structured(annotate_artifact(await image_service.generate_image(
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
                request_id=request_id,
            )), structured_keys=("usage", "payload"))
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def generate_video(
        prompt: str,
        model: str | list[str] = "auto",
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        reference_images: Optional[list[str]] = None,
        reference_videos: Optional[list[str]] = None,
        reference_audios: Optional[list[str]] = None,
        duration_seconds: int = 5,
        aspect_ratio: str = "adaptive",
        resolution: str = "720p",
        with_audio: bool = True,
        quality_tier: str = "balanced",
        watermark: Optional[bool] = None,
        wait: bool = True,
        timeout: Optional[int] = None,
        return_metadata: bool = True,
        model_specific: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Generate video from text prompt, image, or multimodal references using CFGPU models."""
        try:
            return split_structured(annotate_artifact(await video_service.generate_video(
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
                request_id=request_id,
            )), structured_keys=("usage", "payload"))
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def generate_audio(
        text: str,
        model: str | list[str] = "auto",
        voice: Optional[str] = None,
        audio_format: str = "mp3",
        sample_rate: Optional[int] = None,
        bitrate: Optional[int] = None,
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: int = 0,
        emotion: Optional[str] = None,
        quality_tier: str = "balanced",
        wait: bool = True,
        timeout: Optional[int] = None,
        return_metadata: bool = True,
        model_specific: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Generate speech audio from text (text-to-speech) using CFGPU voice models."""
        try:
            return split_structured(annotate_artifact(await audio_service.generate_audio(
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
            )), structured_keys=("usage", "payload", "inline_media"))
        except Exception as e:
            return tool_error_dict(e)


