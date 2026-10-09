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
    score: int, adapter: "ModelAdapter", routing_tier: str, *, declared_defaults: frozenset[str] | None = None
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

    ``routing_tier`` is either a generation ``quality_tier`` or a vision
    ``analysis_depth``. ``declared_defaults`` lets the latter use its separate
    adapter declaration; absent means the generation ``default_for`` declaration.
    """
    return (
        0 if routing_tier in (adapter.default_for if declared_defaults is None else declared_defaults) else 1,
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
            # Resolved through the registry, so a candidate list accepts exactly the
            # spellings a single `model=` does (including case/space variants).
            chosen: set[int] = set()
            unknown: set[str] = set()
            for name in allowed:
                try:
                    adapter = self._registry.get(name)
                except KeyError:
                    adapter = None
                if adapter is None or adapter not in candidates:
                    unknown.add(name)
                else:
                    chosen.add(id(adapter))
            if unknown:
                raise CFGPUError(
                    error_type="invalid_params",
                    user_message=(
                        f"未知或不支持当前任务类型({task_type})的 model: "
                        f"{sorted(unknown)}。请改用当前可用、task_type 为 {task_type} 的 model_id。"
                    ),
                    original={"model": allowed},
                )
            candidates = [a for a in candidates if id(a) in chosen]

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
        # A declared default wins outright among compatible candidates. Vision uses
        # its own analysis-depth declarations; generation keeps quality-tier defaults.
        if isinstance(req, UnderstandVisionInput):
            analysis_depth = req.analysis_depth
            scored.sort(
                key=lambda x: selection_key(
                    x[0], x[1], analysis_depth,
                    declared_defaults=x[1].analysis_depth_default_for,
                )
            )
        else:
            quality_tier = req.quality_tier
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

        # Preferences between models that all passed supports(). They read the declared
        # tasks and inputs — never a separate capability vocabulary — and only order
        # candidates: anything a model cannot serve was already filtered out.
        if isinstance(req, GenerateImageInput):
            if req.reference_images and (
                "multi_image_fusion" in adapter.tasks or "multi_image_group" in adapter.tasks
            ):
                score += 3
            # n > 1 asks for more than one image. A model that does not honour n does not
            # fail — n is a compatibility hint it silently ignores and it returns a single
            # image — so nothing downstream can catch a mismatch here. That makes this a
            # real preference rather than a tie-break, at the same magnitude as the fusion
            # bonus. "Honours n" is the 组图 task (Seedream / 万相: a coherent series) or a
            # declared max_images_per_request above 1 (CF Image 2: independent images,
            # which is not the multi_image_group task).
            if req.n > 1 and (
                "multi_image_group" in adapter.tasks or (adapter.max_images_per_request or 1) > 1
            ):
                score += 3
            # No region preference: supports() already rejects every model without
            # region_edit, so every surviving candidate has it and a bonus orders nothing.
        elif isinstance(req, GenerateVideoInput):
            if req.reference_images and "reference_to_video" in adapter.tasks:
                score += 3
            # A reference video is ambiguous between "material to draw on" and "the video
            # to edit or continue" (wan2.7-i2v accepts one as its first_clip and extends
            # it). Without an edit/extend signal the plain reading is reference, so prefer
            # models whose declared video role is `reference`.
            videos = (adapter.inputs or {}).get("reference_videos")
            if req.reference_videos and videos is not None and videos.role == "reference":
                score += 3
            # Audio intent (soundtrack vs. material) cannot be read from the request, so this
            # keeps the reference-family preference the old capability encoded; an agent
            # that needs the track kept selects an audio_driven_video model explicitly.
            elif req.reference_audios and "reference_to_video" in adapter.tasks:
                score += 3

        # Chinese prompt preference (image only — seedream is an image family)
        if (
            isinstance(req, GenerateImageInput)
            and _is_chinese(req.prompt)
            and adapter.adapter_id.startswith("doubao-seedream")
        ):
            score += 2

        return score
