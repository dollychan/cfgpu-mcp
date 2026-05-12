from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter


def _is_chinese(text: str) -> bool:
    return any(unicodedata.category(ch).startswith("Lo") for ch in text[:100])


class ModelRouter:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    def get_adapter(self, name: str) -> "ModelAdapter":
        try:
            return self._registry.get(name)
        except KeyError as e:
            raise CFGPUError(
                error_type="invalid_params",
                user_message=str(e),
                original={"model": name},
            ) from e

    def select_model(
        self, req: GenerateImageInput | GenerateVideoInput
    ) -> "ModelAdapter":
        task_type = "image" if isinstance(req, GenerateImageInput) else "video"
        candidates: list["ModelAdapter"] = self._registry.list_all(task_type=task_type)

        scored: list[tuple[int, "ModelAdapter"]] = []
        for adapter in candidates:
            ok, _ = adapter.supports(req)
            if not ok:
                continue
            score = self._score(adapter, req)
            scored.append((score, adapter))

        if not scored:
            raise CFGPUError(
                error_type="model_unavailable",
                user_message="没有可用的模型支持当前请求，请检查参数或手动指定 model。",
                original={},
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score(
        self,
        adapter: "ModelAdapter",
        req: GenerateImageInput | GenerateVideoInput,
    ) -> int:
        score = 0

        if req.quality_tier == "fast":
            score += adapter.speed_tier * 2 - adapter.cost_tier
        elif req.quality_tier == "best":
            score += 5 if "best_quality" in adapter.capabilities else 0
            score += adapter.speed_tier - adapter.cost_tier
        else:  # balanced
            score += adapter.speed_tier - adapter.cost_tier

        # Reference media capability bonus
        if isinstance(req, GenerateImageInput):
            if req.reference_images and "multi_ref" in adapter.capabilities:
                score += 3
        elif isinstance(req, GenerateVideoInput):
            if req.reference_images and "multi_modal_reference" in adapter.capabilities:
                score += 3
            if (req.reference_videos or req.reference_audios) and "multi_modal_reference" in adapter.capabilities:
                score += 3

        # Chinese prompt preference
        if _is_chinese(req.prompt) and adapter.adapter_id.startswith("doubao-seedream"):
            score += 2

        return score
