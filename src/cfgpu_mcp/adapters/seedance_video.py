from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


@register_python_adapter
class SeedanceVideoAdapter(ModelAdapter):
    """Python Adapter for the Seedance video family and variants.

    Handles the multimodal content array construction required by the Seedance API.
    WAN 2.0 / WAN 2.0 Fast / Seedance 2.0 / Seedance 2.0 Fast / Doubao Seedance 1.5 Pro
    all reuse this class via Registry extends-chain resolution — no per-variant
    Python module needed. The class is registered under ``wan-2-0`` (the base model
    every variant ``extends:``).
    """

    adapter_id = "wan-2-0"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)

        content: list[dict] = [{"type": "text", "text": req.prompt}]

        # Scene: first frame only
        if req.first_frame and not req.last_frame and not req.reference_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": req.first_frame},
                "role": "first_frame",
            })

        # Scene: first + last frame
        elif req.first_frame and req.last_frame:
            content.append({
                "type": "image_url",
                "image_url": {"url": req.first_frame},
                "role": "first_frame",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": req.last_frame},
                "role": "last_frame",
            })

        # Scene: multimodal reference (reference_images, reference_videos, reference_audios)
        else:
            for url in (req.reference_images or []):
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": "reference_image",
                })

        for url in (req.reference_videos or []):
            content.append({
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            })
        for url in (req.reference_audios or []):
            content.append({
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            })

        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "content": content,
            "ratio": req.aspect_ratio,
            "duration": req.duration_seconds,
            "resolution": req.resolution,
            "generate_audio": req.with_audio,
        }
        if req.watermark is not None:
            payload["watermark"] = req.watermark
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        content = resp.get("content") or {}
        video_url = content.get("videoUrl")
        return NormalizedResult(
            urls=[video_url] if video_url else [],
            expires_at=_default_expires_at(),
            task_id=resp.get("id"),
            model_used=resp.get("model"),
            seed=resp.get("seed"),
            usage=resp.get("usage"),
            aspect_ratio=resp.get("ratio"),  # resolved output ratio (e.g. "adaptive" → "9:16")
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        # Validate mutual exclusivity of scene types
        has_first_last = bool(req.first_frame or req.last_frame)
        has_refs = bool(req.reference_images)
        if has_first_last and has_refs:
            return False, "first/last_frame and reference_images are mutually exclusive"
        if req.last_frame and not req.first_frame:
            return False, "last_frame requires first_frame"
        # WAN 2.0 Fast (doubao-seedance-2-0-fast) does not support 1080p in
        # text-to-video; the API rejects it post-submit. Catch it here so
        # model="auto" routing can fall back to the full wan-2-0 instead.
        is_t2v = not (
            req.first_frame
            or req.last_frame
            or req.reference_images
            or req.reference_videos
        )
        if (
            self.adapter_id == "wan-2-0-fast"
            and is_t2v
            and req.resolution == "1080p"
        ):
            return False, (
                "wan-2-0-fast does not support 1080p for text-to-video "
                "(use 480p or 720p, or switch to wan-2-0 for 1080p)"
            )
        # Doubao Seedance 1.5 Pro caps explicit durations at 12s (WAN 2.0 allows up to 15).
        if (
            self.adapter_id == "doubao-seedance-1-5-pro"
            and req.duration_seconds != -1
            and req.duration_seconds > 12
        ):
            return False, (
                f"{self.adapter_id} supports explicit durations of 4–12 seconds "
                f"(or -1 for a model-chosen smart duration)"
            )
        return True, ""

    def estimate_poll_timeout(self, req: "GenerateImageInput | GenerateVideoInput") -> int:
        assert isinstance(req, GenerateVideoInput)
        base = 300
        if req.first_frame or req.last_frame:
            base = 400
        if req.reference_videos or req.reference_images:
            base = 500
        duration_extra = max(0, req.duration_seconds - 5) * 20
        if self.poll_config:
            return self.poll_config.default_timeout + duration_extra
        return base + duration_extra
