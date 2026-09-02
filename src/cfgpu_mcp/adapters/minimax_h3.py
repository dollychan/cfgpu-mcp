"""MiniMax H3 through the CFGPU video-generation API."""

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

#: What the unified schema default ``adaptive`` becomes on text-to-video.
#:
#: Upstream requires an explicit ratio in that one scenario and rejects
#: ``adaptive`` outright — but ``adaptive`` is *our* schema's default, i.e. the
#: caller saying "you pick", not the caller asking for a value this API refuses.
#: Reading it as a hard error made the plainest possible call —
#: ``generate_video(prompt=...)`` with no other arguments — unroutable to this
#: model, so ``model="auto"`` could never land here for ordinary text-to-video.
#: Same resolution the 万相 family already applies (WanVideoAdapter maps an
#: unsupported ratio to its API's 16:9 default); ``validation_corrections``
#: reports the substitution so a preflight shows what will actually be sent.
_T2V_DEFAULT_RATIO = "16:9"

_REFERENCE_LIMITS = (
    ("reference_images", 9),
    ("reference_videos", 3),
    ("reference_audios", 3),
)


@register_python_adapter
class MinimaxH3Adapter(ModelAdapter):
    """Adapter for ``MiniMax-H3``, served by CFGPU (daily during the test phase).

    The wire contract is MiniMax's own video-generation V2 API — a flat
    ``content[]`` array of typed items, each optionally tagged with a ``role`` —
    which CFGPU passes through rather than re-shaping. So this is neither the
    Seedance flat form nor the DashScope ``input``/``parameters`` envelope, and
    it cannot reuse either of those adapters.

    Create and poll answer in *different* shapes: create is flat
    (``{"task_id": ...}``), poll wraps everything under ``task``. Both readers
    below therefore accept either form — CFGPU's task layer is shared across
    upstreams and may or may not keep MiniMax's envelope on the way out, and the
    cost of tolerating both is nil next to the cost of getting it wrong: a task
    that submits, bills, and can never be read back.
    """

    adapter_id = "cfgpu-minimax-h3"

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
            "duration": self.resolve_duration_seconds(req),
            # Documented top-level key on this API (default false). Sent
            # explicitly, like the 万相 family does with its own `watermark`, so
            # the unified flag is visibly wired rather than silently honoured
            # only in the true case.
            "aigc_watermark": req.watermark,
        }
        # MiniMax infers image-to-video geometry from the first frame, so that
        # scenario sends no ratio at all. Reference-to-video may use `adaptive`
        # (the upstream default); text-to-video may not, so the schema default
        # is substituted there — see _T2V_DEFAULT_RATIO.
        if not (req.first_frame or req.last_frame):
            payload["ratio"] = (
                _T2V_DEFAULT_RATIO
                if self._is_text_to_video(req) and req.aspect_ratio == "adaptive"
                else req.aspect_ratio
            )
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    @staticmethod
    def _is_text_to_video(req: "GenerateVideoInput") -> bool:
        return not (
            req.first_frame
            or req.last_frame
            or req.reference_images
            or req.reference_videos
            or req.reference_audios
        )

    def validation_corrections(self, req: "GenerateVideoInput") -> dict:
        corrected = super().validation_corrections(req)
        if self._is_text_to_video(req) and req.aspect_ratio == "adaptive":
            corrected["aspect_ratio"] = _T2V_DEFAULT_RATIO
        return corrected

    @staticmethod
    def _task(resp: dict) -> dict:
        """The task object, whether or not it is wrapped in a ``task`` envelope.

        MiniMax's poll response nests under ``task`` while its create response is
        flat; CFGPU proxies both. Unwrapping here rather than in each reader
        keeps the three readers below agreeing about which shape they are on.
        """
        task = resp.get("task")
        return task if isinstance(task, dict) else resp

    def extract_task_id(self, resp: dict) -> str | None:
        task = self._task(resp)
        value = task.get("task_id") or task.get("id")
        return str(value) if value is not None else None

    def extract_status(self, resp: dict) -> str:
        status = str(self._task(resp).get("status", "running")).lower()
        return {
            "queued": "pending",
            "success": "succeeded",
            "completed": "succeeded",
        }.get(status, status)

    def parse_response(self, resp: dict) -> NormalizedResult:
        task = self._task(resp)
        content = task.get("content") or {}
        url = content.get("url")
        return NormalizedResult(
            urls=[url] if isinstance(url, str) and url else [],
            expires_at=_default_expires_at(),
            task_id=self.extract_task_id(resp),
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
        if self.resolve_duration_seconds(req) == -1:
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

        # No aspect_ratio refusal here: `adaptive` on text-to-video is the schema
        # default, substituted in build_payload rather than rejected. Every other
        # value the schema admits is one this API accepts — the Literal on
        # GenerateVideoInput.aspect_ratio and MiniMax's `ratio` enum are the same
        # seven values — so there is nothing left to gate.
        return True, ""
