from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


@register_python_adapter
class SeedanceVideoAdapter(ModelAdapter):
    """Python Adapter for the Seedance video family and variants.

    Handles the multimodal content array construction required by the Seedance API.
    WAN 2.0 / WAN 2.0 Fast / Seedance 2.0 / Seedance 2.0 Fast / Seedance 2.0 mini /
    Seedance 2.5 / Doubao Seedance 1.5 Pro
    all reuse this class via Registry extends-chain resolution — no per-variant
    Python module needed. The class is registered under ``wan-2-0`` (the base model
    every variant ``extends:``).
    """

    adapter_id = "wan-2-0"

    def validation_corrections(
        self, req: "GenerateImageInput | GenerateVideoInput"
    ) -> dict[str, Any]:
        corrected = super().validation_corrections(req)
        assert isinstance(req, GenerateVideoInput)
        # Seedance 2.5 derives first-frame / first+last-frame output geometry from
        # the first image. Unlike text-to-video and ordinary reference-to-video,
        # those scenes do not accept an independently selected ratio. ``adaptive``
        # is therefore the safe validate_only fallback.
        if (
            self.adapter_id == "doubao-seedance-2-5"
            and req.first_frame
            and req.aspect_ratio != "adaptive"
        ):
            corrected["aspect_ratio"] = "adaptive"
        is_t2v = not (
            req.first_frame
            or req.last_frame
            or req.reference_images
            or req.reference_videos
            or req.reference_audios
        )
        if self.adapter_id == "wan-2-0-fast" and is_t2v and req.resolution in {"1080p", "4k"}:
            corrected["resolution"] = "720p"
        return corrected

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
            "duration": self.resolve_duration_seconds(req),
            "resolution": req.resolution,
            "generate_audio": req.with_audio,
            "watermark": req.watermark,
        }
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
        has_refs = bool(req.reference_images or req.reference_videos or req.reference_audios)
        if has_first_last and has_refs:
            return False, "first/last_frame and reference media are mutually exclusive"
        if req.last_frame and not req.first_frame:
            return False, "last_frame requires first_frame"
        if (
            self.adapter_id == "doubao-seedance-2-5"
            and req.first_frame
            and req.aspect_ratio != "adaptive"
        ):
            return False, (
                "doubao-seedance-2-5 only supports aspect_ratio=adaptive for "
                "first-frame and first/last-frame video generation; the output "
                "ratio follows the first frame"
            )
        # Validate the requested scene type against the model's declared
        # capabilities. The CFGPU API derives task_type server-side from the
        # content array shape (e.g. a reference_video → r2v); a model that lacks
        # the capability is rejected post-submit. Catch it here so the failure is
        # local and clear, and so model="auto" routing skips incapable models.
        if req.first_frame and req.last_frame:
            needed = "first_last_frame"
        elif req.first_frame:
            needed = "image_to_video"
        elif req.reference_images or req.reference_videos or req.reference_audios:
            needed = "multi_modal_reference"
        else:
            needed = "text_to_video"
        if needed not in self.capabilities:
            return False, (
                f"{self.adapter_id} does not support {needed} "
                f"(capabilities: {', '.join(sorted(self.capabilities))})"
            )
        for field, values, limit in (
            ("reference_images", req.reference_images, self.max_reference_images),
            ("reference_videos", req.reference_videos, self.max_reference_videos),
            ("reference_audios", req.reference_audios, self.max_reference_audios),
        ):
            if values and limit is not None and len(values) > limit:
                return False, f"{self.adapter_id} accepts at most {limit} {field}"
        if (
            req.reference_audios
            and not self.allow_audio_only_reference
            and not (req.reference_images or req.reference_videos)
        ):
            return False, (
                f"{self.adapter_id} does not allow audio-only reference input; "
                "include at least one reference image or video"
            )
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
        # Per-model duration ceilings (1.5 Pro 12s, 2.5 30s, the rest 15s) come from
        # each adapter.yaml's max_duration_seconds and are enforced by super().supports().
        return True, ""

    def estimate_poll_timeout(self, req: "GenerateImageInput | GenerateVideoInput") -> int:
        assert isinstance(req, GenerateVideoInput)
        base = 300
        if req.first_frame or req.last_frame:
            base = 400
        if req.reference_videos or req.reference_images:
            base = 500
        duration_extra = max(0, self.resolve_duration_seconds(req) - 5) * 20
        if self.poll_config:
            return self.poll_config.default_timeout + duration_extra
        return base + duration_extra
