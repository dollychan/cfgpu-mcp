from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


# Kling's create API takes a flat pixel `size` string instead of a resolution
# tier + ratio pair. Map (resolution, aspect_ratio) → "WxH".
_SIZE_MAP: dict[tuple[str, str], str] = {
    ("480p", "16:9"): "854x480",
    ("480p", "9:16"): "480x854",
    ("480p", "1:1"): "480x480",
    ("480p", "4:3"): "640x480",
    ("480p", "3:4"): "480x640",
    ("480p", "21:9"): "1024x440",
    ("720p", "16:9"): "1280x720",
    ("720p", "9:16"): "720x1280",
    ("720p", "1:1"): "720x720",
    ("720p", "4:3"): "960x720",
    ("720p", "3:4"): "720x960",
    ("720p", "21:9"): "1680x720",
    ("1080p", "16:9"): "1920x1080",
    ("1080p", "9:16"): "1080x1920",
    ("1080p", "1:1"): "1080x1080",
    ("1080p", "4:3"): "1440x1080",
    ("1080p", "3:4"): "1080x1440",
    ("1080p", "21:9"): "2560x1080",
}

# Unified quality_tier → Kling generation mode.
_MODE_MAP = {"fast": "std", "balanced": "std", "best": "pro"}


@register_python_adapter
class KlingVideoAdapter(ModelAdapter):
    """Python Adapter for Kling Video O1 (可灵 O1).

    Kling's create API uses a flat payload (``prompt`` / ``size`` / ``mode`` /
    ``seconds``) rather than WAN's multimodal ``content`` array, so it needs its
    own adapter: ``resolution`` + ``aspect_ratio`` are mapped to a pixel ``size``
    string and ``quality_tier`` maps to Kling's ``std`` / ``pro`` mode. The poll
    response follows the standard ``/video/tasks/{task_id}`` shape, so the base
    ``extract_task_id`` / ``extract_status`` are reused.
    """

    adapter_id = "kling-video-o1"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        ratio = req.aspect_ratio if req.aspect_ratio != "adaptive" else "16:9"
        size = _SIZE_MAP.get((req.resolution, ratio), _SIZE_MAP[("720p", "16:9")])
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "prompt": req.prompt,
            "size": size,
            "mode": _MODE_MAP.get(req.quality_tier, "std"),
            "seconds": str(req.duration_seconds),
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
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        # The documented create API only covers text-to-video; the field names
        # for image/video/audio references aren't published yet, so reject them
        # cleanly rather than guess (and let model="auto" route elsewhere).
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} currently supports text-to-video only (no first/last frame)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, f"{self.adapter_id} currently supports text-to-video only (no reference media)"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""
