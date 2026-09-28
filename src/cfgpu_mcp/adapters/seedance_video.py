from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput

# Seedance 2.5 classifies a multimodal-reference request into one of three
# sub-tasks, and two of them constrain ratio / duration. See
# ``SeedanceVideoAdapter._omni_reference_task_type`` for why only ``reference``
# is ever derived and ``edit`` / ``extend`` must be declared.
_OMNI_REFERENCE_TASK_KEY = "omni_reference_task_type"
_OMNI_REFERENCE_TASK_TYPES = ("auto", "reference", "edit", "extend")

# Seedance 2.5's two asynchronous task-type failures. Both are caused by the request,
# so a retry of the same call can never succeed — yet as ``task_failed`` they read as
# "generation failed" and invite exactly that. Each remedy names the parameter change,
# and they differ because the causes do: Constraint is the model's own classification
# meeting incompatible parameters (under ``auto``); Mismatch is a declared sub-task the
# prompt contradicts. The card documents the constraint table, so its hint is kept.
_TASK_TYPE_IDENTIFIED = "identified your task as"

_TASK_TYPE_REMEDIES = {
    "InvalidParameter.TaskTypeConstraint": (
        "模型根据提示词把本次任务判定为视频编辑或视频延长，而当前参数与该任务类型不兼容。"
        "要编辑：aspect_ratio 设为 \"adaptive\"，duration_seconds 设为 -1 或不传，且被编辑的参考视频时长须在 4–30 秒；"
        "要延长：aspect_ratio 设为 \"adaptive\"。"
        "若本意只是把视频当作参考：指定一个具体的 aspect_ratio（如 \"16:9\"），任务会被声明为参考生视频，"
        "或去掉提示词里“删除/替换/改成/延长/续写”这类编辑、延长措辞。"
    ),
    "InvalidParameter.TaskTypeMismatch": (
        "声明的任务类型（omni_reference_task_type）与模型从提示词判定的类型不一致。"
        "指定了具体 aspect_ratio 时任务会被声明为参考生视频：若本意是编辑/延长，"
        "把 aspect_ratio 改为 \"adaptive\"（编辑另需 duration_seconds=-1），并在提示词中写明意图；"
        "若本意是参考生视频，去掉提示词里“删除/替换/改成/延长/续写”这类编辑、延长措辞。"
        "若在 model_specific 中显式声明了 edit/extend，请确认提示词包含对应关键词，或移除该声明。"
    ),
}


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
        # A declared edit / extend fixes the output geometry to the source video, so
        # the same fallback applies — only when the declaration is otherwise valid,
        # since correcting toward a request supports() rejects anyway helps nobody.
        declared = self._declared_omni_task_type(req)
        if (
            self.adapter_id == "doubao-seedance-2-5"
            and declared in ("edit", "extend")
            and self._is_omni_reference(req)
            and req.reference_videos
        ):
            if req.aspect_ratio != "adaptive":
                corrected["aspect_ratio"] = "adaptive"
            if declared == "edit" and req.duration_seconds not in (None, -1):
                corrected["duration_seconds"] = -1
        is_t2v = not (
            req.first_frame
            or req.last_frame
            or req.reference_images
            or req.reference_videos
            or req.reference_audios
        )
        if (
            self.adapter_id == "wan-2-0-fast"
            and is_t2v
            and self.resolve_resolution(req) in {"1080p", "4k"}
        ):
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
            "resolution": self.resolve_resolution(req),
            "generate_audio": req.with_audio,
            "watermark": req.watermark,
        }
        task_type = self._omni_reference_task_type(req)
        if task_type is not None:
            payload[_OMNI_REFERENCE_TASK_KEY] = task_type
        # An explicit model_specific value overrides the derived one; supports()
        # has already checked it against these same final arguments.
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def translate_task_failure(self, error: str) -> tuple[str, str, bool | None] | None:
        for code, remedy in _TASK_TYPE_REMEDIES.items():
            if code in error:
                return "invalid_params", f"{error} {remedy}", None
        # The CFGPU relay has been seen to drop the code and pass only Ark's prose
        # ("... Seedance identified your task as video editing based on your prompt.
        # ... Issues: [0] duration must be -1."), so the constraint is also recognised
        # by that sentence. Codes are checked first: a Mismatch may well use the same
        # phrase, and its code is the more specific signal.
        if _TASK_TYPE_IDENTIFIED in error:
            return (
                "invalid_params",
                f"{error} {_TASK_TYPE_REMEDIES['InvalidParameter.TaskTypeConstraint']}",
                None,
            )
        return None

    def _is_omni_reference(self, req: GenerateVideoInput) -> bool:
        return not (req.first_frame or req.last_frame) and bool(
            req.reference_images or req.reference_videos or req.reference_audios
        )

    @staticmethod
    def _declared_omni_task_type(req: GenerateVideoInput) -> Any:
        return (req.model_specific or {}).get(_OMNI_REFERENCE_TASK_KEY)

    def _omni_reference_task_type(self, req: GenerateVideoInput) -> str | None:
        """Seedance 2.5's sub-task declaration, derived from the final arguments.

        Derived here, in ``build_payload``, so it is recomputed from whatever is
        actually submitted: a preflight's value is only what the arguments implied
        *then*, and a human editing ratio or duration on the approval card changes it.

        ratio / duration can prove ``reference`` but never ``edit`` or ``extend``.
        Both of those need a reference video and ``ratio=adaptive`` (edit also
        ``duration=-1``) — so a request without a video, or with a concrete ratio,
        is compatible with ``reference`` alone and declaring it costs nothing. The
        converse fails: ``adaptive`` / ``-1`` are also this model's defaults and are
        just as valid for ``reference``, so mapping them to ``edit`` would declare
        every ordinary reference call an edit and fail it with TaskTypeMismatch.
        Edit / extend intent lives in the prompt; those cases get ``auto`` and the
        model classifies, unless the caller declares via ``model_specific``.

        Text-to-video and first/last-frame are not omni-reference tasks, so the
        field — defined only for that task — is not sent at all.
        """
        if self.adapter_id != "doubao-seedance-2-5" or not self._is_omni_reference(req):
            return None
        if not req.reference_videos or req.aspect_ratio != "adaptive":
            return "reference"
        return "auto"

    def _check_declared_omni_task_type(self, req: GenerateVideoInput) -> str | None:
        """Reject a ``model_specific`` declaration the final arguments contradict.

        Checked against the arguments actually submitted, so a value frozen from an
        earlier preflight (whose ratio a human has since changed) fails locally with
        a reason rather than asynchronously upstream.
        """
        declared = self._declared_omni_task_type(req)
        if declared is None or self.adapter_id != "doubao-seedance-2-5":
            return None
        if declared not in _OMNI_REFERENCE_TASK_TYPES:
            return (
                f"{_OMNI_REFERENCE_TASK_KEY}={declared!r} is not valid; use one of "
                f"{', '.join(_OMNI_REFERENCE_TASK_TYPES)}"
            )
        if not self._is_omni_reference(req):
            return (
                f"{_OMNI_REFERENCE_TASK_KEY} only applies to multimodal reference "
                "tasks (reference_images / reference_videos / reference_audios); "
                "remove it for text-to-video and first/last-frame generation"
            )
        if declared in ("edit", "extend"):
            if not req.reference_videos:
                return f"{_OMNI_REFERENCE_TASK_KEY}={declared} requires at least one reference_video"
            if req.aspect_ratio != "adaptive":
                return (
                    f"{_OMNI_REFERENCE_TASK_KEY}={declared} requires aspect_ratio=adaptive; "
                    "the output ratio follows the source video"
                )
        if declared == "edit" and self.resolve_duration_seconds(req) != -1:
            return (
                f"{_OMNI_REFERENCE_TASK_KEY}=edit requires duration_seconds=-1; "
                "the output duration follows the source video"
            )
        return None

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
        declared_reason = self._check_declared_omni_task_type(req)
        if declared_reason:
            return False, declared_reason
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
            and self.resolve_resolution(req) == "1080p"
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
