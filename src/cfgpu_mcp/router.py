from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import (
    GenerateAudioInput,
    GenerateImageInput,
    GenerateVideoInput,
    UnderstandVisionInput,
)

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

    def resolve(
        self, req: GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput
    ) -> "ModelAdapter":
        """Resolve req.model (single id, candidate list, or 'auto') to one adapter."""
        model = req.model
        if isinstance(model, list):
            return self.select_model(req, allowed=model)
        if model == "auto":
            return self.select_model(req)
        return self.get_adapter(model)

    def select_model(
        self,
        req: GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput,
        allowed: list[str] | None = None,
    ) -> "ModelAdapter":
        if isinstance(req, GenerateImageInput):
            task_type = "image"
        elif isinstance(req, GenerateVideoInput):
            task_type = "video"
        elif isinstance(req, UnderstandVisionInput):
            task_type = "understand"
        else:
            task_type = "audio"
        candidates: list["ModelAdapter"] = self._registry.list_all(task_type=task_type)

        if allowed:
            allowed_set = set(allowed)
            known = {a.adapter_id for a in candidates} | {
                a.cfgpu_model_id for a in candidates
            }
            unknown = allowed_set - known
            if unknown:
                raise CFGPUError(
                    error_type="invalid_params",
                    user_message=(
                        f"未知或不支持当前任务类型({task_type})的 model: "
                        f"{sorted(unknown)}。请使用 list_models 查看可用 adapter_id。"
                    ),
                    original={"model": allowed},
                )
            candidates = [
                a
                for a in candidates
                if a.adapter_id in allowed_set or a.cfgpu_model_id in allowed_set
            ]

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
        # Highest score wins; break ties deterministically by adapter_id so
        # selection never depends on registry/filesystem iteration order.
        scored.sort(key=lambda x: (-x[0], x[1].adapter_id))
        return scored[0][1]

    def _score(
        self,
        adapter: "ModelAdapter",
        req: GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput,
    ) -> int:
        score = 0

        # understand requests carry no quality_tier; treat them as "balanced".
        quality_tier = getattr(req, "quality_tier", "balanced")
        if quality_tier == "fast":
            score += adapter.speed_tier * 2 - adapter.cost_tier
        elif quality_tier == "best":
            # No model declares an explicit quality flag, so use cost_tier as a
            # proxy: pricier models tend to be the higher-quality flagships.
            score += adapter.cost_tier * 2
            score += adapter.speed_tier - adapter.cost_tier
        else:  # balanced (and understand)
            score += adapter.speed_tier - adapter.cost_tier

        # Reference media capability bonus
        if isinstance(req, GenerateImageInput):
            if req.reference_images and (
                "multi_image_fusion" in adapter.capabilities
                or "multi_image_group" in adapter.capabilities
            ):
                score += 3
        elif isinstance(req, GenerateVideoInput):
            if req.reference_images and "multi_modal_reference" in adapter.capabilities:
                score += 3
            if (req.reference_videos or req.reference_audios) and "multi_modal_reference" in adapter.capabilities:
                score += 3

        # Chinese prompt preference (image only — seedream is an image family)
        if (
            isinstance(req, GenerateImageInput)
            and _is_chinese(req.prompt)
            and adapter.adapter_id.startswith("doubao-seedream")
        ):
            score += 2

        return score
