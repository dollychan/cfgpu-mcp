from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


# The request has no aspect_ratio "adaptive" — a concrete ratio is always sent.
_DEFAULT_RATIO = "16:9"


@register_python_adapter
class GrokVideoAdapter(ModelAdapter):
    """Python Adapter for Grok Imagine Video 1.5 (xAI).

    A third video API shape, sharing neither of the existing two:

    - **Request** is flat and snake_case — ``{"model", "prompt", "aspect_ratio",
      "video_length", "resolution_name", "refer_images": [...]}`` — not Seedance's
      ``content[]`` array nor 万相/HappyHorse's ``input``/``parameters`` envelope.
      ``video_length`` is a **string** (like Kling's ``seconds``) and
      ``resolution_name`` keeps the lowercase tier (``720p``, not ``720P``).
    - **Poll** wraps everything under a ``data`` object like the async image models
      (``{"code", "message", "data": {...}}``), with camelCase keys inside
      (``taskId`` / ``status`` / ``videoUrl`` / ``videoLength``), so
      ``extract_task_id`` / ``extract_status`` / ``parse_response`` all read
      through ``_data()``.

    Billing is per second with the unit price stepping on output resolution, and —
    as with Kling — the response carries no ``usage`` object, so ``_build_usage``
    synthesizes the same ``{duration, sr, ratio}`` record from ``videoLength`` /
    ``resolutionName`` / ``aspectRatio``.

    Audio is always generated (the model emits synchronized audio and the request
    has no sound switch), so ``with_audio`` is not sent.
    """

    adapter_id = "grok-imagine-video-1-5"

    @staticmethod
    def _data(resp: dict) -> dict:
        data = resp.get("data")
        return data if isinstance(data, dict) else {}

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        # One flat image slot: a first_frame and any reference_images all ride
        # `refer_images`, first_frame first (it is the frame the clip starts from).
        refer_images: list[str] = []
        if req.first_frame:
            refer_images.append(req.first_frame)
        refer_images.extend(req.reference_images or [])

        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "prompt": req.prompt,
            "aspect_ratio": req.aspect_ratio if req.aspect_ratio != "adaptive" else _DEFAULT_RATIO,
            "video_length": str(req.duration_seconds),
            "resolution_name": req.resolution,   # lowercase tier, e.g. "720p"
        }
        if refer_images:
            payload["refer_images"] = refer_images
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def extract_task_id(self, resp: dict) -> str | None:
        data = self._data(resp)
        return data.get("taskId") or data.get("task_id") or super().extract_task_id(resp)

    def extract_status(self, resp: dict) -> str:
        status = (self._data(resp).get("status") or "running").lower()
        # Not in task_manager's _STATUS_MAP; collapse to failed so polling converges.
        if status in ("canceled", "cancelled", "unknown"):
            return "failed"
        return status

    @staticmethod
    def _parse_sr(resolution_name: Any) -> int | None:
        """``"720p"`` → ``720``. The billing tier is read off the resolution's short
        side, which is exactly what the tier name states."""
        if isinstance(resolution_name, (int, float)):
            return int(resolution_name)
        if not isinstance(resolution_name, str):
            return None
        digits = resolution_name.strip().rstrip("pP")
        try:
            return int(digits)
        except ValueError:
            return None

    def _build_usage(self, data: dict) -> dict | None:
        """Synthesize the billing record Grok does not return.

        Grok is billed per second at a rate that steps with output resolution
        (≤480P / ≤720P / >720P), but the task response carries no ``usage`` object —
        the inputs sit in ``data.videoLength`` / ``data.resolutionName``. Assemble
        them into the same ``{duration, sr, ratio}`` shape Kling / 万相 / HappyHorse
        report, so a consumer reads per-second billing the same way across the family.
        """
        duration: Any = data.get("videoLength")
        if isinstance(duration, str):   # tolerate the string form the request uses
            try:
                duration = int(duration)
            except ValueError:
                pass
        usage = {
            "duration": duration,
            "sr": self._parse_sr(data.get("resolutionName")),
            "ratio": data.get("aspectRatio"),
        }
        # Nothing extractable (e.g. a queued response) — report no usage rather than
        # a record of three nulls.
        if all(v is None for v in usage.values()):
            return None
        return usage

    def parse_response(self, resp: dict) -> NormalizedResult:
        data = self._data(resp)
        video_url = data.get("videoUrl") or data.get("proxyUrl")
        usage = self._build_usage(data)
        return NormalizedResult(
            urls=[video_url] if video_url else [],
            expires_at=_default_expires_at(),
            task_id=data.get("taskId"),
            model_used=resp.get("model"),
            seed=None,
            usage=usage,
            # `aspectRatio` is null on most responses; task_manager then falls back
            # to the requested ratio.
            aspect_ratio=data.get("aspectRatio"),
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        # The request body has exactly one media slot, `refer_images`.
        if req.last_frame:
            return False, f"{self.adapter_id} does not support last_frame (refer_images only)"
        if req.reference_videos:
            return False, f"{self.adapter_id} does not support reference_videos"
        if req.reference_audios:
            return False, f"{self.adapter_id} does not support reference_audios"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""
