from __future__ import annotations

from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput

# Resolution × aspect_ratio → exact pixel size string
_SIZE_MAP: dict[tuple[str, str], str] = {
    ("2K", "1:1"):  "2048x2048",
    ("2K", "4:3"):  "2304x1728",
    ("2K", "3:4"):  "1728x2304",
    ("2K", "16:9"): "2848x1600",
    ("2K", "9:16"): "1600x2848",
    ("3K", "1:1"):  "3072x3072",
    ("3K", "16:9"): "4096x2304",
    ("3K", "9:16"): "2304x4096",
    ("4K", "1:1"):  "4096x4096",
    ("4K", "16:9"): "5504x3040",
    ("4K", "9:16"): "3040x5504",
}


@register_python_adapter
class SeedreamAdapter(ModelAdapter):
    """Python Adapter for Doubao Seedream 5.0 lite (synchronous image model)."""

    adapter_id = "doubao-seedream-5-0-lite"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)

        size = _SIZE_MAP.get(
            (req.resolution, req.aspect_ratio),
            _SIZE_MAP.get((req.resolution, "1:1"), "2048x2048"),  # fallback: 2K 1:1
        )
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "prompt": req.prompt,
            "size": size,
            "response_format": "url",
        }
        if req.reference_images:
            # Single ref → string; multiple → array
            payload["image"] = (
                req.reference_images[0]
                if len(req.reference_images) == 1
                else req.reference_images
            )
        if req.n and req.n > 1:
            # Group images (组图): 输入参考图数量 + 生成数量 ≤ 15
            payload["sequential_image_generation"] = "auto"
            payload["sequential_image_generation_options"] = {"max_images": req.n}
        if req.watermark is not None:
            payload["watermark"] = req.watermark
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        # Synchronous: response contains results directly
        urls = [item["url"] for item in resp.get("data", []) if "url" in item]
        return NormalizedResult(
            urls=urls,
            expires_at=_default_expires_at(),
            task_id=None,          # Synchronous model has no task_id
            model_used=resp.get("model"),
            seed=None,
            cost_tokens=(resp.get("usage") or {}).get("total_tokens"),
        )
