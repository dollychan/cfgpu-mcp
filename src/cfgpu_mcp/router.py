from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


#: Weight of one ``quality_rank`` step in the "best" tier. cost_tier + speed_tier
#: can reach 10 (both capped at 5), so a step of 11 guarantees that any declared
#: rank outranks every undeclared model and that the rank order can never be
#: overturned by a price difference between two ranked models.
_QUALITY_RANK_STEP = 11


def selection_key(
    score: int, adapter: "ModelAdapter", quality_tier: str
) -> tuple[int, int, int, str]:
    """Ordering for ``model="auto"``: declared default, then score, preference, name.

    **``default_for`` outranks the score; ``auto_priority`` does not.** The two look
    similar and are deliberately different strengths:

    - ``auto_priority`` is a **tie-break, not a bonus**. It must separate models the
      score left level without ever outweighing a real scoring difference. Folded
      into the score it would do exactly that: a video default carrying priority 2
      would outrank the flagship proxy by a point in the "best" tier, handing a best
      request to a model picked for being *fast*. As a later key it can only speak
      when the earlier ones are silent.
    - ``default_for`` is an **operator decision for one named quality tier**, so it
      is allowed to overrule the heuristic — that is the whole point of writing it
      down. The alternative for expressing "this is our default" was bending
      ``speed_tier`` / ``cost_tier`` until the score agreed, which corrupts the
      inputs: both feed *every* tier's score, so a number bent to win one tier
      silently moves the others, and the YAML ends up asserting a latency or a price
      that is not true. Scoping it to a tier is what keeps it from leaking: a model
      declared the balanced default says nothing about "fast" or "best".

    It is not a pin. ``select_model`` has already dropped every candidate whose
    ``supports()`` refused the request, so a declared default that cannot serve this
    particular call is simply not here, and the next-best candidate wins normally.

    The trailing ``adapter_id`` keeps selection deterministic and independent of
    registry/filesystem iteration order.

    ``quality_tier`` is required rather than defaulted: it now changes the *first*
    key, and a caller that omitted it would get a silently wrong ordering that
    still looks like a valid ranking.
    """
    return (
        0 if quality_tier in adapter.default_for else 1,
        -score,
        -adapter.auto_priority,
        adapter.adapter_id,
    )


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
        self,
        req: GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput,
        *,
        for_validation: bool = False,
    ) -> "ModelAdapter":
        """Resolve req.model (single id, candidate list, or 'auto') to one adapter."""
        model = req.model
        if isinstance(model, list):
            if not model:
                raise CFGPUError(
                    error_type="invalid_params",
                    user_message="model candidate list must not be empty; use 'auto' explicitly",
                    original={"model": model},
                )
            return self.select_model(req, allowed=model, for_validation=for_validation)
        if model == "auto":
            return self.select_model(req, for_validation=for_validation)
        # Explicit single model: the auto/list paths filter on supports(), but a
        # directly named model would otherwise bypass it and surface a task-type /
        # capability mismatch as a raw AssertionError from build_payload(). Validate
        # here so the caller gets the friendly supports() reason (and a model-card hint).
        try:
            adapter = self.get_adapter(model)
        except CFGPUError:
            # An unknown model_id here is almost always a hallucinated / mistyped id.
            # Vision-understanding models are a small, homogeneous, *synchronous* set
            # (the call is cheap and re-runnable), so a hard failure would needlessly
            # abort the whole analysis. Fall back to auto-selection instead. The
            # generate_* paths deliberately keep the hard error: a wrong media model
            # would waste an async, billed generation job and must surface loudly.
            if isinstance(req, UnderstandVisionInput):
                logger.warning(
                    "understand_vision: unknown model %r, falling back to auto-selection",
                    model,
                )
                return self.select_model(req)
            raise
        # validate_only may safely normalize a model-specific enum (for example
        # 4k -> this model's highest supported tier).  Let validate_request apply
        # that correction before supports(); the billed path still rejects the raw
        # invalid request unless the caller merges the returned corrected_args.
        if for_validation:
            return adapter
        ok, reason = adapter.supports(req)
        if not ok:
            raise CFGPUError(
                error_type="invalid_params",
                user_message=reason,
                original={"model": model},
                model_id=adapter.model_name,
            )
        return adapter

    def select_model(
        self,
        req: GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput,
        allowed: list[str] | None = None,
        *,
        for_validation: bool = False,
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
            known = (
                {a.model_name for a in candidates}
                | {a.adapter_id for a in candidates}
                | {a.cfgpu_model_id for a in candidates}
            )
            unknown = allowed_set - known
            if unknown:
                raise CFGPUError(
                    error_type="invalid_params",
                    user_message=(
                        f"未知或不支持当前任务类型({task_type})的 model: "
                        f"{sorted(unknown)}。请使用 list_models 查看可用 model_id。"
                    ),
                    original={"model": allowed},
                )
            candidates = [
                a
                for a in candidates
                if a.model_name in allowed_set
                or a.adapter_id in allowed_set
                or a.cfgpu_model_id in allowed_set
            ]

        scored: list[tuple[int, "ModelAdapter"]] = []
        for adapter in candidates:
            candidate_req = req
            if for_validation:
                corrections = adapter.validation_corrections(req)
                if corrections:
                    candidate_req = req.model_copy(update=corrections)
            ok, _ = adapter.supports(candidate_req)
            if not ok:
                continue
            score = self._score(adapter, candidate_req)
            scored.append((score, adapter))

        if not scored:
            raise CFGPUError(
                error_type="model_unavailable",
                user_message="没有可用的模型支持当前请求，请检查参数或手动指定 model。",
                original={},
            )
        # A model declared the default for this quality tier wins outright; failing
        # that, highest score. See selection_key for the full ordering.
        quality_tier = getattr(req, "quality_tier", "balanced")
        scored.sort(key=lambda x: selection_key(x[0], x[1], quality_tier))
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
            # quality_rank is the explicit flagship declaration. cost_tier stays as
            # the proxy for models that declare none (every video and audio model
            # today): pricier tends to mean flagship — but only tends to, which is
            # why a declared rank always wins over it.
            score += adapter.quality_rank * _QUALITY_RANK_STEP
            score += adapter.cost_tier * 2
            score += adapter.speed_tier - adapter.cost_tier
        else:  # balanced (and understand)
            score += adapter.speed_tier - adapter.cost_tier

        # NOTE: auto_priority is deliberately *not* added here — it is a tie-break
        # applied after the score, see selection_key.

        # Reference media capability bonus
        if isinstance(req, GenerateImageInput):
            if req.reference_images and (
                "multi_image_fusion" in adapter.capabilities
                or "multi_image_group" in adapter.capabilities
            ):
                score += 3
            # n > 1 asks for a 组图. A model without the capability does not fail — n is
            # a compatibility hint it silently ignores and it returns a single image —
            # so nothing downstream can catch a mismatch here. That makes this a real
            # preference rather than a tie-break, at the same magnitude as the fusion
            # bonus. (It matters now that auto_priority pins 5.0 Pro, the one Seedream
            # *without* the capability, as the default pick.)
            if req.n > 1 and "multi_image_group" in adapter.capabilities:
                score += 3
            # Region editing does not need to be *filtered* for here — supports() already
            # rejects every model without the capability, so a regions request cannot
            # reach a model that would ignore it. This is only a tie-break preference,
            # same magnitude as the fusion bonus above.
            if req.regions and "region_edit" in adapter.capabilities:
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
