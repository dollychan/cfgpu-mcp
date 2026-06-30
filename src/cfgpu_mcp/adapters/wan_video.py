from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput


@register_python_adapter
class WanVideoAdapter(ModelAdapter):
    """Python Adapter for the 万相 2.6 / 2.7 video family.

    This is a *hybrid* of the two existing video API shapes, so it cannot reuse
    ``SeedanceVideoAdapter`` (WAN 2.0 / Seedance) nor ``HappyHorseVideoAdapter``:

    - **Request** uses the DashScope-style nested envelope like HappyHorse —
      ``{"model", "input": {...}, "parameters": {"resolution", "duration"}}`` —
      *not* Seedance's flat ``content[]`` array. The ``input`` shape differs per
      member and is built by the ``_build_input`` hook:
        * 万相 2.7 (i2v/r2v/t2v/videoedit): ``{"prompt", "media": [{"type","url"}]}``
        * 万相 2.6 (i2v/r2v/t2v): flat keys ``{"prompt", "img_url", "audio_url",
          "reference_urls": [...]}`` — no ``media`` array.
    - **Poll** uses the DashScope ``output``-nested envelope like HappyHorse —
      create returns ``{"output": {"task_status", "task_id"}}`` (snake_case),
      poll returns ``{"output": {"taskId", "taskStatus", "videoUrl"}, "usage"}``
      (camelCase) — *not* Seedance's flat ``{"id", "status", "content"}``. So
      ``extract_task_id`` / ``extract_status`` / ``parse_response`` read the
      ``output`` envelope, tolerating both key casings.

    This base class is 万相 2.7 图生视频 (``wan2.7-i2v``): image-to-video only, a
    first-frame image is required. Siblings override ``_build_input`` (or the
    ``_build_media`` helper it uses) and ``supports``.
    """

    adapter_id = "wan-2-7-i2v"

    def _output(self, resp: dict) -> dict:
        return resp.get("output") or {}

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        """万相 2.7 image-to-video: a single first-frame image."""
        return [{"type": "first_frame", "url": req.first_frame}]

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        """Build the ``input`` object. 万相 2.7 default: prompt + optional media array."""
        inp: dict = {"prompt": req.prompt}
        media = self._build_media(req)
        if media:                                   # omit entirely for text-to-video
            inp["media"] = media
        return inp

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        payload: dict = {
            "model": self.cfgpu_model_id,           # Only place cfgpu_model_id is used
            "input": self._build_input(req),
            "parameters": {
                "resolution": req.resolution.upper(),   # 720p → 720P
                "duration": req.duration_seconds,
            },
        }
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def extract_task_id(self, resp: dict) -> str | None:
        # Create response is snake_case (task_id); poll response is camelCase (taskId).
        output = self._output(resp)
        return output.get("taskId") or output.get("task_id")

    def extract_status(self, resp: dict) -> str:
        # Poll response uses camelCase taskStatus with UPPERCASE values (SUCCEEDED).
        output = self._output(resp)
        status = (output.get("taskStatus") or output.get("task_status") or "running").lower()
        # "canceled" and "unknown" aren't in task_manager's _STATUS_MAP; collapse to failed
        if status in ("canceled", "unknown"):
            return "failed"
        return status

    def parse_response(self, resp: dict) -> NormalizedResult:
        output = self._output(resp)
        usage = resp.get("usage") or {}
        video_url = output.get("videoUrl")
        return NormalizedResult(
            urls=[video_url] if video_url else [],
            expires_at=_default_expires_at(),
            task_id=output.get("taskId") or output.get("task_id"),
            model_used=resp.get("model"),
            seed=output.get("seed"),
            usage=resp.get("usage"),
            aspect_ratio=usage.get("ratio") or output.get("ratio"),  # resolved output ratio (usage.ratio)
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if not req.first_frame:
            return False, f"{self.adapter_id} is an image-to-video model and requires first_frame"
        if req.last_frame:
            return False, f"{self.adapter_id} does not support last_frame (first_frame only)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, f"{self.adapter_id} supports image-to-video only (no reference media)"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""

    def estimate_poll_timeout(self, req: "GenerateImageInput | GenerateVideoInput") -> int:
        assert isinstance(req, GenerateVideoInput)
        base = self.poll_config.default_timeout if self.poll_config else 400
        duration_extra = max(0, req.duration_seconds - 5) * 20
        return base + duration_extra


# ── 万相 2.7 siblings (media array) ──────────────────────────────────────────


@register_python_adapter
class WanVideoR2VAdapter(WanVideoAdapter):
    """万相 2.7 参考生视频 (``wan2.7-r2v``).

    ``media`` carries reference videos (``reference_video``) and reference images
    (``reference_image``) the prompt refers to as 视频1/视频2/图片3.
    """

    adapter_id = "wan-2-7-r2v"

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        media: list[dict] = []
        for url in (req.reference_videos or []):
            media.append({"type": "reference_video", "url": url})
        for url in (req.reference_images or []):
            media.append({"type": "reference_image", "url": url})
        return media

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip WanVideoAdapter.supports (it requires first_frame); go to base.
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} is a reference-to-video model (use reference_videos/reference_images, not first/last_frame)"
        if req.reference_audios:
            return False, f"{self.adapter_id} does not support reference_audios"
        if not (req.reference_videos or req.reference_images):
            return False, f"{self.adapter_id} requires at least one reference_video or reference_image"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""


@register_python_adapter
class WanVideoT2VAdapter(WanVideoAdapter):
    """万相 2.7 文生视频 (``wan2.7-t2v``).

    Text-only: ``_build_media`` returns empty, so ``_build_input`` omits ``media``.
    """

    adapter_id = "wan-2-7-t2v"

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        return []

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip WanVideoAdapter.supports (it requires first_frame); go to base.
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} is a text-to-video model (no first/last_frame)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, f"{self.adapter_id} is a text-to-video model (no reference media)"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""


@register_python_adapter
class WanVideoEditAdapter(WanVideoAdapter):
    """万相 2.7 视频编辑 (``wan2.7-videoedit``).

    A source video (``type: "video"``) plus reference images (``reference_image``)
    that drive the edit, e.g. "将视频中女孩的衣服替换为图片中的衣服".
    """

    adapter_id = "wan-2-7-videoedit"

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        media: list[dict] = []
        for url in (req.reference_videos or []):
            media.append({"type": "video", "url": url})
        for url in (req.reference_images or []):
            media.append({"type": "reference_image", "url": url})
        return media

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip WanVideoAdapter.supports (it requires first_frame); go to base.
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} is a video-edit model (use reference_videos/reference_images, not first/last_frame)"
        if req.reference_audios:
            return False, f"{self.adapter_id} does not support reference_audios"
        if not req.reference_videos:
            return False, f"{self.adapter_id} requires a source video (reference_videos)"
        if len(req.reference_videos) > 1:
            return False, f"{self.adapter_id} accepts a single source video"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""


# ── 万相 2.6 family (flat input keys, no media array) ─────────────────────────


@register_python_adapter
class Wan26VideoT2VAdapter(WanVideoAdapter):
    """万相 2.6 文生视频 (``wan2.6-t2v``). Flat input: ``{"prompt"}``."""

    adapter_id = "wan-2-6-t2v"

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        return {"prompt": req.prompt}

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} is a text-to-video model (no first/last_frame)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, f"{self.adapter_id} is a text-to-video model (no reference media)"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""


@register_python_adapter
class Wan26VideoI2VAdapter(WanVideoAdapter):
    """万相 2.6 图生视频 (``wan2.6-i2v``).

    Flat input: ``{"prompt", "img_url", "audio_url"?}`` — a first-frame image
    (required) and an optional driving audio track. Maps unified ``first_frame``
    → ``img_url`` and the first ``reference_audios`` entry → ``audio_url``.
    """

    adapter_id = "wan-2-6-i2v"

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        inp: dict = {"prompt": req.prompt, "img_url": req.first_frame}
        if req.reference_audios:
            inp["audio_url"] = req.reference_audios[0]
        return inp

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if not req.first_frame:
            return False, f"{self.adapter_id} is an image-to-video model and requires first_frame"
        if req.last_frame:
            return False, f"{self.adapter_id} does not support last_frame (first_frame only)"
        if req.reference_images or req.reference_videos:
            return False, f"{self.adapter_id} accepts only a first_frame image and an optional reference_audios track"
        if req.reference_audios and len(req.reference_audios) > 1:
            return False, f"{self.adapter_id} accepts a single audio track (reference_audios)"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""


@register_python_adapter
class Wan26VideoR2VAdapter(WanVideoAdapter):
    """万相 2.6 参考生视频 (``wan2.6-r2v``).

    Flat input: ``{"prompt", "reference_urls": [...]}`` — a single flat list of
    reference media URLs (videos and/or images), no per-item type tags. The
    prompt refers to them as character1, etc. Maps unified ``reference_videos`` +
    ``reference_images`` into one ``reference_urls`` list (videos first).
    """

    adapter_id = "wan-2-6-r2v"

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        reference_urls = list(req.reference_videos or []) + list(req.reference_images or [])
        return {"prompt": req.prompt, "reference_urls": reference_urls}

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.adapter_id} is a reference-to-video model (use reference_videos/reference_images, not first/last_frame)"
        if req.reference_audios:
            return False, f"{self.adapter_id} does not support reference_audios"
        if not (req.reference_videos or req.reference_images):
            return False, f"{self.adapter_id} requires at least one reference_video or reference_image"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        return True, ""
