"""MiniMax H3 through the Submodel video-generation API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


_RESOLUTION_MAP = {
    "720p": "768P",
    "1080p": "2K",
}

_REFERENCE_LIMITS = (
    ("reference_images", 9),
    ("reference_videos", 3),
    ("reference_audios", 3),
)


@register_python_adapter
class SubmodelH3Adapter(ModelAdapter):
    """Adapter for ``submodel/minimax-h3`` / upstream ``MiniMax-H3``."""

    adapter_id = "submodel-minimax-h3"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)

        content: list[dict] = [{"type": "text", "text": req.prompt}]
        for role, url in (("first_frame", req.first_frame), ("last_frame", req.last_frame)):
            if url:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": role,
                })
        for url in req.reference_images or []:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            })
        for url in req.reference_videos or []:
            content.append({
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            })
        for url in req.reference_audios or []:
            content.append({
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            })

        payload: dict = {
            "model": self.cfgpu_model_id,
            "content": content,
            "resolution": _RESOLUTION_MAP[req.resolution],
            "duration": req.duration_seconds,
        }
        # Submodel infers image-to-video geometry from the first frame. Reference
        # inputs may use adaptive; text-to-video was checked for an explicit ratio
        # in supports().
        if not (req.first_frame or req.last_frame):
            payload["ratio"] = req.aspect_ratio
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def extract_status(self, resp: dict) -> str:
        task = resp.get("task")
        if not isinstance(task, dict):
            return "running"
        status = str(task.get("status", "running")).lower()
        return {
            "queued": "pending",
            "success": "succeeded",
            "completed": "succeeded",
        }.get(status, status)

    def parse_response(self, resp: dict) -> NormalizedResult:
        task = resp.get("task") or {}
        content = task.get("content") or {}
        url = content.get("url")
        return NormalizedResult(
            urls=[url] if isinstance(url, str) and url else [],
            expires_at=_default_expires_at(),
            task_id=str(task["id"]) if task.get("id") is not None else None,
            model_used=task.get("model"),
            seed=None,
            usage=task.get("usage"),
            aspect_ratio=task.get("ratio"),
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)

        if not req.prompt.strip():
            return False, f"{self.model_name} requires at least one non-empty text prompt"
        if req.duration_seconds == -1:
            return False, f"{self.model_name} requires an explicit duration from 4 to 15 seconds"

        has_frames = bool(req.first_frame or req.last_frame)
        has_references = bool(
            req.reference_images or req.reference_videos or req.reference_audios
        )
        if req.last_frame and not req.first_frame:
            return False, "last_frame requires first_frame"
        if has_frames and has_references:
            return False, (
                "first_frame / last_frame are mutually exclusive with "
                "reference_images / reference_videos / reference_audios"
            )
        for name, limit in _REFERENCE_LIMITS:
            values = getattr(req, name)
            if values and len(values) > limit:
                return False, f"{self.model_name} accepts at most {limit} {name} (got {len(values)})"

        is_text_to_video = not has_frames and not has_references
        if is_text_to_video and req.aspect_ratio == "adaptive":
            return False, (
                f"{self.model_name} requires an explicit aspect_ratio for text-to-video; "
                "choose 21:9, 16:9, 4:3, 1:1, 3:4, or 9:16"
            )
        return True, ""
