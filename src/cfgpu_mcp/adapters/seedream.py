from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    ("2K", "3:2"):  "2496x1664",
    ("2K", "2:3"):  "1664x2496",
    ("2K", "21:9"): "3136x1344",
    ("3K", "1:1"):  "3072x3072",
    ("3K", "16:9"): "4096x2304",
    ("3K", "9:16"): "2304x4096",
    ("4K", "1:1"):  "4096x4096",
    ("4K", "16:9"): "5504x3040",
    ("4K", "9:16"): "3040x5504",
}


@register_python_adapter
class SeedreamAdapter(ModelAdapter):
    """Python Adapter for Doubao Seedream (synchronous image models).

    Base for doubao-seedream-5-0-lite; also reused (via the `extends` chain) by
    the 4.0/4.5 variants and doubao-seedream-5-0-pro. Pro is single-image only
    and supports 1K/2K tiers; see build_payload for the pro-specific guards.
    """

    adapter_id = "doubao-seedream-5-0-lite"

    def validation_corrections(
        self, req: "GenerateImageInput | GenerateVideoInput"
    ) -> dict[str, Any]:
        assert isinstance(req, GenerateImageInput)
        is_pro = self.adapter_id == "doubao-seedream-5-0-pro"
        allowed_resolutions = {"1K", "2K"} if is_pro else {"2K", "3K", "4K"}
        resolution = req.resolution if req.resolution in allowed_resolutions else "2K"
        corrected: dict[str, Any] = {}
        if resolution != req.resolution:
            corrected["resolution"] = resolution

        # 1K Pro is a semantic tier: the model chooses geometry itself.  Every
        # other tier is emitted as an exact pixel pair and therefore must exist
        # in the table.  Fall back to the same tier's square size, never to a
        # different unreported geometry.
        if not (is_pro and resolution == "1K") and (resolution, req.aspect_ratio) not in _SIZE_MAP:
            corrected["aspect_ratio"] = "1:1"
        return corrected

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateImageInput)
        if not req.prompt.strip():
            return False, f"{self.adapter_id} requires a non-empty prompt"
        is_pro = self.adapter_id == "doubao-seedream-5-0-pro"
        max_refs = 10 if is_pro else 14
        reference_count = len(req.reference_images or [])
        if reference_count > max_refs:
            return False, f"{self.adapter_id} accepts at most {max_refs} reference_images"
        if is_pro and req.n > 1:
            return False, (
                "doubao-seedream-5-0-pro does not support n>1 (group images); "
                "use doubao-seedream-5-0-lite for group generation"
            )
        if not is_pro and reference_count + req.n > 15:
            return False, (
                f"{self.adapter_id} requires reference_images count + n <= 15 "
                f"(got {reference_count} + {req.n})"
            )
        return True, ""

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)

        is_pro = self.adapter_id == "doubao-seedream-5-0-pro"

        # Pro is single-image only (no 组图 / multi_image_group).
        if is_pro and req.n and req.n > 1:
            raise ValueError(
                "doubao-seedream-5-0-pro does not support n>1 (group images); "
                "use doubao-seedream-5-0-lite for 组图 generation."
            )

        if is_pro and req.resolution == "1K":
            # Pro's 1K tier has no fixed pixel table (model judges aspect), so
            # pass the tier through to the API (方式 2) instead of mapping.
            size = "1K"
        else:
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
            usage=resp.get("usage"),
        )
