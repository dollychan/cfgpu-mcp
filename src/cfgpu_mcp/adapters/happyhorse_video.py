from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


@register_python_adapter
class HappyHorseVideoAdapter(ModelAdapter):
    """Python Adapter for HappyHorse video models.

    Uses DashScope-style payload (input.prompt + input.media[]) and response
    (output.task_id, output.task_status with uppercase status values).
    """

    adapter_id = "happyhorse-1-0-t2v"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)

        media: list[dict] = []
        if req.first_frame:
            media.append({"type": "first_frame", "url": req.first_frame})
        for url in (req.reference_images or []):
            media.append({"type": "reference_image", "url": url})

        inp: dict = {"prompt": req.prompt}
        if media:
            inp["media"] = media

        parameters: dict = {}
        if req.resolution and req.resolution != "adaptive":
            parameters["resolution"] = req.resolution.upper()  # 720p → 720P
        if req.aspect_ratio and req.aspect_ratio != "adaptive":
            parameters["ratio"] = req.aspect_ratio
        if req.duration_seconds:
            parameters["duration"] = req.duration_seconds

        payload: dict = {
            "model": self.cfgpu_model_id,
            "input": inp,
        }
        if parameters:
            payload["parameters"] = parameters
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def extract_task_id(self, resp: dict) -> str | None:
        return (resp.get("output") or {}).get("task_id")

    def extract_status(self, resp: dict) -> str:
        # Status values are uppercase (PENDING, RUNNING, SUCCEEDED, FAILED, CANCELED, UNKNOWN)
        status = (resp.get("output") or {}).get("task_status", "running").lower()
        # Map terminal non-success statuses not in task_manager._STATUS_MAP
        if status in ("canceled", "unknown"):
            return "failed"
        return status

    def parse_response(self, resp: dict) -> NormalizedResult:
        output = resp.get("output") or {}
        video_url = output.get("video_url")
        usage = resp.get("usage") or {}
        return NormalizedResult(
            urls=[video_url] if video_url else [],
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            task_id=output.get("task_id"),
            model_used=resp.get("model"),
            seed=output.get("seed"),
            cost_tokens=usage.get("total_tokens"),
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.last_frame:
            return False, "happyhorse-1-0-t2v does not support last_frame"
        if req.reference_videos:
            return False, "happyhorse-1-0-t2v does not support reference_videos"
        if req.reference_audios:
            return False, "happyhorse-1-0-t2v does not support reference_audios"
        if req.resolution == "480p":
            return False, "happyhorse-1-0-t2v minimum resolution is 720p"
        if req.first_frame and req.reference_images:
            return False, "first_frame and reference_images are mutually exclusive"
        return True, ""
