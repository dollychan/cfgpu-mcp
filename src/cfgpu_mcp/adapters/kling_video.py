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

# `video_list[].refer_type`: "feature" borrows motion/camera/style from the video,
# "base" makes it the footage being edited. The unified schema has a single
# `reference_videos` slot with no edit/reference distinction, so reference is the
# default and an edit is requested by overriding the whole array via
# `model_specific={"video_list": [{"video_url": ..., "refer_type": "base"}]}`.
_DEFAULT_REFER_TYPE = "feature"
_BASE_REFER_TYPE = "base"


@register_python_adapter
class KlingVideoAdapter(ModelAdapter):
    """Python Adapter for Kling Video O1 (可灵 O1).

    Kling's create API uses a flat payload (``prompt`` / ``size`` / ``mode`` /
    ``seconds``) rather than WAN's multimodal ``content`` array, so it needs its
    own adapter: ``resolution`` + ``aspect_ratio`` are mapped to a pixel ``size``
    string and ``quality_tier`` maps to Kling's ``std`` / ``pro`` mode.

    Media rides two sibling arrays rather than one interleaved list. ``image_list``
    entries are ``{"image": url}`` plus an optional ``type``
    (``first_frame`` / ``end_frame``); an entry with no ``type`` is a plain
    reference image, and typed and untyped entries may be mixed. ``video_list``
    entries are ``{"video_url": url, "refer_type": ...}``. ``with_audio`` maps to
    the string flag ``sound`` (``on`` / ``off``).

    Create returns a flat ``{"id", "status": "queued", ...}`` envelope, so the
    base ``extract_task_id`` / ``extract_status`` are reused. The poll response
    nests the result under ``taskResult.videos[]`` (``[{"id", "url", "duration"}]``)
    with the outcome in a top-level ``status`` (``completed``), so
    ``parse_response`` reads that nested array.
    """

    adapter_id = "kling-video-o1"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        ratio = req.aspect_ratio if req.aspect_ratio != "adaptive" else "16:9"
        size = _SIZE_MAP.get((req.resolution, ratio), _SIZE_MAP[("720p", "16:9")])
        # An untyped entry is a plain reference image; typed and untyped entries
        # may be mixed (e.g. a style reference alongside a first frame).
        image_list: list[dict] = []
        if req.first_frame:
            image_list.append({"image": req.first_frame, "type": "first_frame"})
        if req.last_frame:
            image_list.append({"image": req.last_frame, "type": "end_frame"})
        for url in (req.reference_images or []):
            image_list.append({"image": url})

        video_list = [
            {"video_url": url, "refer_type": _DEFAULT_REFER_TYPE}
            for url in (req.reference_videos or [])
        ]

        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "prompt": req.prompt,
            "size": size,
            "mode": _MODE_MAP.get(req.quality_tier, "std"),
            "seconds": str(req.duration_seconds),
            "sound": "on" if req.with_audio else "off",
        }
        if image_list:
            payload["image_list"] = image_list
        if video_list:
            payload["video_list"] = video_list
        if req.model_specific:
            payload.update(req.model_specific)
        # Editing a `base` video takes its length from the source footage, so Kling
        # sends no `seconds` there. Decided after the merge so an override-supplied
        # video_list counts too — but never drop a `seconds` the caller set explicitly.
        if self._has_base_video(payload) and "seconds" not in (req.model_specific or {}):
            payload.pop("seconds", None)
        return payload

    @staticmethod
    def _has_base_video(payload: dict) -> bool:
        return any(
            isinstance(v, dict) and v.get("refer_type") == _BASE_REFER_TYPE
            for v in (payload.get("video_list") or [])
        )

    def parse_response(self, resp: dict) -> NormalizedResult:
        task_result = resp.get("taskResult") or {}
        videos = task_result.get("videos") or []
        urls = [v["url"] for v in videos if isinstance(v, dict) and v.get("url")]
        return NormalizedResult(
            urls=urls,
            expires_at=_default_expires_at(),
            task_id=resp.get("id"),
            model_used=resp.get("model"),
            seed=resp.get("seed"),
            usage=resp.get("usage"),
            aspect_ratio=resp.get("ratio"),  # resolved output ratio when the API reports it
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        # The create payload has an image_list and a video_list but no audio slot.
        if req.reference_audios:
            return False, f"{self.adapter_id} does not support reference_audios"
        if req.last_frame and not req.first_frame:
            return False, "last_frame requires first_frame"
        if req.duration_seconds == -1:
            return False, f"{self.adapter_id} requires an explicit duration (no -1 smart mode)"
        # Validate the requested scene against the model's declared capabilities so
        # model="auto" skips incapable models instead of failing post-submit.
        if req.first_frame and req.last_frame:
            needed = "first_last_frame"
        elif req.reference_images or req.reference_videos:
            needed = "multi_modal_reference"
        elif req.first_frame:
            needed = "image_to_video"
        else:
            needed = "text_to_video"
        if needed not in self.capabilities:
            return False, (
                f"{self.adapter_id} does not support {needed} "
                f"(capabilities: {', '.join(sorted(self.capabilities))})"
            )
        return True, ""
