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
      ``{"model", "input": {...}, "parameters": {"resolution", "ratio",
      "prompt_extend", "watermark", "duration"}}`` —
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

    This base class is 万相 2.7 图生视频 (``wan2.7-i2v``). It accepts a first
    frame (optionally with a final frame and driving audio), or a source clip for
    continuation (optionally with a final frame). Siblings override
    ``_build_input`` (or the ``_build_media`` helper it uses) and ``supports``.
    """

    adapter_id = "wan-2-7-i2v"

    _ALLOWED_RATIOS = frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"})
    _RATIOLESS_ADAPTERS = frozenset({"wan-2-6-i2v", "wan-2-7-i2v"})

    def _uses_ratio(self) -> bool:
        return self.adapter_id not in self._RATIOLESS_ADAPTERS

    def validation_corrections(self, req: "GenerateVideoInput") -> dict:
        corrected = super().validation_corrections(req)
        if self._uses_ratio() and req.aspect_ratio not in self._ALLOWED_RATIOS:
            corrected["aspect_ratio"] = "16:9"
        return corrected

    def _supports_common(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        # ``adaptive`` is the unified schema default and maps to this API's 16:9
        # default. Other unsupported ratios are only corrected by validate_only.
        if (
            self._uses_ratio()
            and req.aspect_ratio != "adaptive"
            and req.aspect_ratio not in self._ALLOWED_RATIOS
        ):
            return False, (
                f"{self.model_name} does not support aspect_ratio {req.aspect_ratio} "
                f"(supported: {', '.join(sorted(self._ALLOWED_RATIOS))})"
            )
        return True, ""

    def _output(self, resp: dict) -> dict:
        return resp.get("output") or {}

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        """Build the documented Wan 2.7 i2v media combinations.

        ``reference_videos`` is intentionally the continuation-source slot for
        this adapter only: the unified schema has no separate ``first_clip``
        field. ``supports`` constrains it to exactly one clip, so it cannot be
        mistaken for Wan's multi-video reference-to-video model.
        """
        media: list[dict] = []
        if req.first_frame:
            media.append({"type": "first_frame", "url": req.first_frame})
        elif req.reference_videos:
            media.append({"type": "first_clip", "url": req.reference_videos[0]})
        if req.last_frame:
            media.append({"type": "last_frame", "url": req.last_frame})
        if req.reference_audios:
            media.append({"type": "driving_audio", "url": req.reference_audios[0]})
        return media

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        """Build the ``input`` object. 万相 2.7 default: prompt + optional media array."""
        inp: dict = {"prompt": req.prompt}
        if req.negative_prompt:
            inp["negative_prompt"] = req.negative_prompt
        media = self._build_media(req)
        if media:                                   # omit entirely for text-to-video
            inp["media"] = media
        return inp

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        parameters: dict = {
            "resolution": self.resolve_resolution(req).upper(),   # 720p → 720P
            "prompt_extend": req.prompt_extend,
            "watermark": req.watermark,
            "duration": self.resolve_duration_seconds(req),
        }
        if self._uses_ratio():
            parameters["ratio"] = (
                req.aspect_ratio if req.aspect_ratio in self._ALLOWED_RATIOS else "16:9"
            )
        payload: dict = {
            "model": self.cfgpu_model_id,           # Only place cfgpu_model_id is used
            "input": self._build_input(req),
            "parameters": parameters,
        }
        return self._merge_model_specific(payload, req)

    @staticmethod
    def _merge_model_specific(payload: dict, req: "GenerateVideoInput") -> dict:
        """Deep-merge the documented ``parameters`` escape hatch.

        Every Wan video endpoint puts its advanced controls in ``parameters``.  A
        shallow ``payload.update`` here would replace the typed controls when a
        caller sets (for example) ``seed`` or ``shot_type``.
        """
        if not req.model_specific:
            return payload
        overrides = dict(req.model_specific)
        parameter_overrides = overrides.pop("parameters", None)
        if isinstance(parameter_overrides, dict):
            payload.setdefault("parameters", {}).update(parameter_overrides)
        elif parameter_overrides is not None:
            overrides["parameters"] = parameter_overrides
        payload.update(overrides)
        return payload

    def extract_task_id(self, resp: dict) -> str | None:
        # Create response is snake_case (task_id); poll response is camelCase (taskId).
        output = self._output(resp)
        return output.get("taskId") or output.get("task_id")

    def extract_status(self, resp: dict) -> str:
        # Poll response uses camelCase taskStatus with UPPERCASE values (SUCCEEDED).
        output = self._output(resp)
        status = (output.get("taskStatus") or output.get("task_status") or "running").lower()
        # "canceled" / "unknown" are terminal here. _STATUS_MAP collapses them centrally
        # as well; this line keeps the upstream's documented vocabulary next to the card.
        if status in ("canceled", "unknown"):
            return "failed"
        return status

    def parse_response(self, resp: dict) -> NormalizedResult:
        output = self._output(resp)
        usage = resp.get("usage") or {}
        video_url = output.get("videoUrl") or output.get("video_url")
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
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        has_first_frame = bool(req.first_frame)
        has_first_clip = bool(req.reference_videos)
        if has_first_frame == has_first_clip:
            return False, (
                f"{self.model_name} requires exactly one first_frame or source video "
                "(reference_videos[0] maps to first_clip)"
            )
        if req.reference_images:
            return False, f"{self.model_name} does not support reference_images"
        if req.reference_videos and len(req.reference_videos) != 1:
            return False, f"{self.model_name} accepts exactly one source video (first_clip)"
        if req.reference_audios and len(req.reference_audios) != 1:
            return False, f"{self.model_name} accepts at most one driving audio track"
        if has_first_clip and req.reference_audios:
            return False, f"{self.model_name} supports driving audio only with first_frame"
        return True, ""

    def estimate_poll_timeout(self, req: "GenerateImageInput | GenerateVideoInput") -> int:
        assert isinstance(req, GenerateVideoInput)
        base = self.poll_config.default_timeout if self.poll_config else 400
        duration_extra = max(0, self.resolve_duration_seconds(req) - 5) * 20
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
        # The upstream attaches a voice sample to an individual reference item.
        # Preserve the unified list's order: audio N belongs to the Nth reference
        # video/image (the optional first frame is not a character reference).
        for item, voice_url in zip(media, req.reference_audios or []):
            item["reference_voice"] = voice_url
        if req.first_frame:
            media.append({"type": "first_frame", "url": req.first_frame})
        return media

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip WanVideoAdapter.supports (it requires first_frame); go to base.
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.last_frame:
            return False, f"{self.model_name} does not support last_frame"
        if not (req.reference_videos or req.reference_images):
            return False, f"{self.model_name} requires at least one reference_video or reference_image"
        if len(req.reference_audios or []) > len(req.reference_videos or []) + len(req.reference_images or []):
            return False, f"{self.model_name} accepts at most one reference_voice per reference media item"
        media_count = len(req.reference_videos or []) + len(req.reference_images or []) + bool(req.first_frame)
        if media_count > 5:
            return False, f"{self.model_name} accepts at most 5 media items (including first_frame)"
        if req.reference_videos and self.resolve_duration_seconds(req) > 10:
            return False, f"{self.model_name} accepts durations of 2–10 seconds when reference_videos are supplied"
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
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.model_name} is a text-to-video model (no first/last_frame)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, f"{self.model_name} is a text-to-video model (no reference media)"
        return True, ""


@register_python_adapter
class WanVideoEditAdapter(WanVideoAdapter):
    """万相 2.7 视频编辑 (``wan2.7-videoedit``).

    A source video (``type: "video"``) plus reference images (``reference_image``)
    that drive the edit, e.g. "将视频中女孩的衣服替换为图片中的衣服".
    """

    adapter_id = "wan-2-7-videoedit"

    def _uses_ratio(self) -> bool:
        # Omitted ratio preserves the source video's aspect ratio.  It is not the
        # Wan 2.7 T2V/R2V default of 16:9.
        return False

    def validation_corrections(self, req: "GenerateVideoInput") -> dict:
        corrected = super().validation_corrections(req)
        # Keep the source ratio for the unified default ``adaptive``.  An explicit
        # unsupported ratio, however, can use the same safe 16:9 preflight fallback
        # as the other Wan 2.7 endpoints.
        if req.aspect_ratio != "adaptive" and req.aspect_ratio not in self._ALLOWED_RATIOS:
            corrected["aspect_ratio"] = "16:9"
        return corrected

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        parameters: dict = {
            "resolution": self.resolve_resolution(req).upper(),
            "prompt_extend": req.prompt_extend,
            "watermark": req.watermark,
        }
        if req.aspect_ratio in self._ALLOWED_RATIOS:
            parameters["ratio"] = req.aspect_ratio
        # The upstream default is zero (= retain the complete source clip).  Do
        # not silently crop it by turning the unified omitted value into 5 seconds.
        if req.duration_seconds is not None:
            parameters["duration"] = req.duration_seconds
        payload = {
            "model": self.cfgpu_model_id,
            "input": self._build_input(req),
            "parameters": parameters,
        }
        return self._merge_model_specific(payload, req)

    def _build_media(self, req: "GenerateVideoInput") -> list[dict]:
        media: list[dict] = []
        for url in (req.reference_videos or []):
            media.append({"type": "video", "url": url})
        for url in (req.reference_images or []):
            media.append({"type": "reference_image", "url": url})
        return media

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip WanVideoAdapter.supports (it requires first_frame); go to base.
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.model_name} is a video-edit model (use reference_videos/reference_images, not first/last_frame)"
        if req.reference_audios:
            return False, f"{self.model_name} does not support reference_audios"
        if not req.reference_videos:
            return False, f"{self.model_name} requires a source video (reference_videos)"
        if len(req.reference_videos) > 1:
            return False, f"{self.model_name} accepts a single source video"
        if req.reference_images and len(req.reference_images) > 4:
            return False, f"{self.model_name} accepts at most 4 reference_images"
        if req.aspect_ratio != "adaptive" and req.aspect_ratio not in self._ALLOWED_RATIOS:
            return False, f"{self.model_name} does not support aspect_ratio {req.aspect_ratio}"
        return True, ""


# ── 万相 2.6 family (flat input keys, no media array) ─────────────────────────


@register_python_adapter
class Wan26VideoT2VAdapter(WanVideoAdapter):
    """万相 2.6 文生视频 (``wan2.6-t2v``). Flat input: ``{"prompt"}``."""

    adapter_id = "wan-2-6-t2v"

    _SIZES = {
        "720p": "1280*720",
        "1080p": "1920*1080",
    }

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        inp = {"prompt": req.prompt}
        if req.reference_audios:
            inp["audio_url"] = req.reference_audios[0]
        return inp

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        parameters: dict = {
            "size": self._SIZES[self.resolve_resolution(req)],
            "duration": self.resolve_duration_seconds(req),
            "prompt_extend": req.prompt_extend,
            "watermark": req.watermark,
        }
        if req.negative_prompt:
            parameters["negative_prompt"] = req.negative_prompt
        return self._merge_model_specific(
            {"model": self.cfgpu_model_id, "input": self._build_input(req), "parameters": parameters}, req
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.model_name} is a text-to-video model (no first/last_frame)"
        if req.reference_images or req.reference_videos:
            return False, f"{self.model_name} is a text-to-video model (no reference media)"
        if req.reference_audios and len(req.reference_audios) > 1:
            return False, f"{self.model_name} accepts at most one driving audio track"
        if req.aspect_ratio not in {"adaptive", "16:9"}:
            return False, f"{self.model_name} supports only 16:9 output (parameters.size)"
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
        if req.negative_prompt:
            inp["negative_prompt"] = req.negative_prompt
        if req.reference_audios:
            inp["audio_url"] = req.reference_audios[0]
        return inp

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if not req.first_frame:
            return False, f"{self.model_name} is an image-to-video model and requires first_frame"
        if req.last_frame:
            return False, f"{self.model_name} does not support last_frame (first_frame only)"
        if req.reference_images or req.reference_videos:
            return False, f"{self.model_name} accepts only a first_frame image and an optional reference_audios track"
        if req.reference_audios and len(req.reference_audios) > 1:
            return False, f"{self.model_name} accepts a single audio track (reference_audios)"
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

    _SIZES = {
        ("720p", "16:9"): "1280*720",
        ("720p", "9:16"): "720*1280",
        ("720p", "1:1"): "960*960",
        ("1080p", "16:9"): "1920*1080",
        ("1080p", "9:16"): "1080*1920",
    }

    def _build_input(self, req: "GenerateVideoInput") -> dict:
        reference_urls = list(req.reference_videos or []) + list(req.reference_images or [])
        return {"prompt": req.prompt, "reference_urls": reference_urls}

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        ratio = "16:9" if req.aspect_ratio == "adaptive" else req.aspect_ratio
        parameters = {
            "size": self._SIZES[(self.resolve_resolution(req), ratio)],
            "duration": self.resolve_duration_seconds(req),
            "watermark": req.watermark,
        }
        return self._merge_model_specific(
            {"model": self.cfgpu_model_id, "input": self._build_input(req), "parameters": parameters}, req
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = self._supports_common(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if req.first_frame or req.last_frame:
            return False, f"{self.model_name} is a reference-to-video model (use reference_videos/reference_images, not first/last_frame)"
        if req.reference_audios:
            return False, f"{self.model_name} does not support reference_audios"
        if not (req.reference_videos or req.reference_images):
            return False, f"{self.model_name} requires at least one reference_video or reference_image"
        if len(req.reference_videos or []) + len(req.reference_images or []) > 5:
            return False, f"{self.model_name} accepts at most 5 reference media items"
        if len(req.reference_videos or []) > 3:
            return False, f"{self.model_name} accepts at most 3 reference videos"
        ratio = "16:9" if req.aspect_ratio == "adaptive" else req.aspect_ratio
        if (self.resolve_resolution(req), ratio) not in self._SIZES:
            return False, f"{self.model_name} does not support {self.resolve_resolution(req)} at ratio {ratio}"
        return True, ""
