from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput


# Unified quality_tier → GPT Image 2's generation quality. The three tiers line
# up one-to-one, so the tier the caller already sets steers the model instead of
# a second near-synonymous parameter. Same shape as Kling's _MODE_MAP.
# The image-only extra tiers (xhigh / max / auto) are the API's own words and go
# up verbatim; base.supports() admits them only on models declaring quality_tiers.
_QUALITY_MAP = {"fast": "low", "balanced": "medium", "best": "high", "xhigh": "xhigh", "max": "max", "auto": "auto"}


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
        if not req.prompt.strip():
            return False, f"{self.model_name} requires a non-empty prompt"
        return True, ""

    def validation_corrections(
        self, req: "GenerateImageInput | GenerateVideoInput"
    ) -> dict[str, Any]:
        assert isinstance(req, GenerateImageInput)
        corrected: dict[str, Any] = {}
        # This API family accepts the discrete 1K / 2K / 4K tiers.  Keep the
        # correction at or below the caller's requested tier: validation must
        # never turn an unsupported request into a more expensive generation.
        if req.resolution == "1.5K":
            corrected["resolution"] = "1K"
        elif req.resolution == "3K":
            corrected["resolution"] = "2K"
        # Ratio: the base corrects to the nearest of adapter.yaml's aspect_ratios.
        corrected.update(super().validation_corrections(req))
        return corrected

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        raise NotImplementedError


@register_python_adapter
class ImageGPTAdapter(_AsyncImageBase):
    """Adapter for CFGPU's GPT Image family.

    Payload: model + prompt + aspect_ratio + resolution + quality + n +
    reference_images (optional). ``quality_tier`` maps to the API's
    ``low`` / ``medium`` / ``high`` quality. Output dimensions always come from
    aspect_ratio + resolution; the 2.5 variants' custom ``image_size`` is not
    exposed, so the upstream maps the pair itself.

    adapter.yaml keys for the 2.5 variants: ``sends_n: false`` (no output-count
    parameter upstream) and ``default_background`` (sent as ``background``;
    ``model_specific.background`` overrides it).
    """

    adapter_id = "gpt-image-2"

    @classmethod
    def from_config(cls, config: dict) -> "ImageGPTAdapter":
        instance: ImageGPTAdapter = super().from_config(config)  # type: ignore[assignment]
        instance._sends_n = bool(config.get("sends_n", True))
        instance._default_background = config.get("default_background")
        return instance

    def parse_response(self, resp: dict) -> NormalizedResult:
        result = super().parse_response(resp)
        # The GPT Image task record reports no top-level `usage`: the token count
        # rides data.result.tokenUsage ({textInputTokens, imageInputTokens,
        # imageOutputTokens, totalTokens}), passed through as-is like every other
        # adapter's usage. Nano Banana's record carries no usage at all.
        if result.usage is None:
            result.usage = ((resp.get("data") or {}).get("result") or {}).get("tokenUsage")
        return result

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)
        payload = {
            "model": self.cfgpu_model_id,
            "prompt": req.prompt,
            "aspect_ratio": req.aspect_ratio,
            # The API names all three tiers literally (1K / 2K / 4K) and
            # rejects anything else, empty string included, so the tier goes
            # up verbatim. 3K has no counterpart and is rejected upstream.
            "resolution": req.resolution,
            "quality": _QUALITY_MAP.get(req.quality_tier, "medium"),
        }
        if self._sends_n:
            # GPT Image 2 accepts an output-count ceiling (1–10); the API may
            # return fewer images.
            payload["n"] = req.n
        if self._default_background is not None:
            payload["background"] = self._default_background
        return self._finalize_payload(payload, req)


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


__all__ = ["ImageGPTAdapter", "NanoBananaAdapter"]
