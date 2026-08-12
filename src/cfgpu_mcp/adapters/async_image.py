from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput


# Unified quality_tier → GPT Image 2's generation quality. The three tiers line
# up one-to-one, so the tier the caller already sets steers the model instead of
# a second near-synonymous parameter. Same shape as Kling's _MODE_MAP.
_QUALITY_MAP = {"fast": "low", "balanced": "medium", "best": "high"}


class _AsyncImageBase(ModelAdapter):
    """Shared response handling for image models that wrap responses under a 'data' key.

    POST /images/generations → {"code":200,"data":{"task_id":"...","status":"pending"}}
    GET  /images/tasks/{id}  → {"code":200,"data":{"status":"completed","result":{"images":[...]}}}
    """

    def extract_task_id(self, resp: dict) -> str | None:
        return (resp.get("data") or {}).get("task_id")

    def extract_status(self, resp: dict) -> str:
        return (resp.get("data") or {}).get("status", "running")

    def parse_response(self, resp: dict) -> NormalizedResult:
        data = resp.get("data") or {}
        images: list[str] = (data.get("result") or {}).get("images") or []
        return NormalizedResult(
            urls=images,
            expires_at=_default_expires_at(),
            task_id=data.get("task_id"),
            model_used=None,
            seed=None,
            usage=resp.get("usage"),
        )

    def _finalize_payload(self, payload: dict, req: GenerateImageInput) -> dict:
        if req.reference_images:
            payload["reference_images"] = req.reference_images
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateImageInput)
        if req.n and req.n > 1:
            return False, (
                f"{self.adapter_id} generates a single image; n>1 (组图 / group image "
                f"generation) is only supported by doubao-seedream-* models"
            )
        return True, ""

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        raise NotImplementedError


@register_python_adapter
class GptImage2Adapter(_AsyncImageBase):
    """Adapter for GPT Image 2.

    Payload: model + prompt + aspect_ratio + resolution + quality +
    reference_images (optional). ``quality_tier`` maps to the API's
    ``low`` / ``medium`` / ``high`` quality.
    Supported aspect ratios: 1:1, 3:2, 2:3, 4:3, 3:4, 16:9, 9:16 — the unified
    schema also offers 21:9, which goes up verbatim and is rejected upstream.
    """

    adapter_id = "gpt-image-2"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)
        return self._finalize_payload(
            {
                "model": self.cfgpu_model_id,
                "prompt": req.prompt,
                "aspect_ratio": req.aspect_ratio,
                # The API spells its default tier as "" and names only 2K / 4K
                # explicitly, so 1K has to be translated. 3K has no counterpart
                # and goes up verbatim, to be rejected upstream.
                "resolution": "" if req.resolution == "1K" else req.resolution,
                "quality": _QUALITY_MAP.get(req.quality_tier, "medium"),
            },
            req,
        )


@register_python_adapter
class NanoBananaAdapter(_AsyncImageBase):
    """Adapter for Nano Banana 2 and Nano Banana Pro.

    Payload: model + prompt + image_size + aspect_ratio + reference_images (optional).
    image_size maps from resolution (1K/2K/4K).
    Nano Pro reuses this class via YAML extends: nano-banana-2.
    """

    adapter_id = "nano-banana-2"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)
        return self._finalize_payload(
            {"model": self.cfgpu_model_id, "prompt": req.prompt, "image_size": req.resolution, "aspect_ratio": req.aspect_ratio},
            req,
        )
