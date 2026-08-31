from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.adapters.regions import build_bbox_list, regions_missing_size, render_prompt
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput


# ── size ────────────────────────────────────────────────────────────────────
#
# 万相 2.7 takes `size` two ways and they cannot be mixed: a tier name ("2K"), or an
# exact `width*height` pixel pair — note the separator is `*`, not Seedream's `x`, and
# the response echoes the same spelling back in `usage.size`.
#
# Unlike Seedream, this family publishes no per-ratio pixel table. What it publishes is
# a *total pixel budget* per tier (1K = 1024x1024, 2K = 2048x2048) plus a hard range of
# [768x768, 2048x2048] for every scenario, so the table below is computed rather than
# copied: for aspect ratio aw:ah, take the largest integer m with (2*aw*m) * (2*ah*m)
# inside the tier's budget. That makes w:h come out *exactly* aw:ah — which is the whole
# reason for sending pixels instead of the tier name — with both dimensions even, and
# every cell lands at 96.9%+ of its tier's budget.
#
# Sending pixels always, image input included, is the same choice every other image
# adapter here makes, and it has one visible consequence worth knowing: with a tier name
# this model would scale the output to the *last input image's* aspect ratio, whereas
# pixels mean `aspect_ratio` (default 1:1) governs even on an edit. Callers who want the
# input's shape back hand framing back to the model with
# `model_specific={"parameters": {"size": "2K"}}` — the tier name overrides the computed
# pair, and the model then scales to the last input image's ratio.

_RATIOS = ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")

#: Documented total-pixel meaning of each tier name. 4K exists only on wan2.7-image-pro.
_TIER_PIXELS = {"1K": 1024 * 1024, "2K": 2048 * 2048}

#: Every scenario on wan2.7-image (non-pro) is capped here; the floor applies too and no
#: computed cell comes near it.
_MIN_TOTAL = 768 * 768
_MAX_TOTAL = 2048 * 2048

#: Ordering for picking a fallback tier — the highest supported tier at or below what was
#: asked for, so a correction never silently upgrades a caller into a bigger, pricier
#: image than they requested.
_TIER_RANK = {"1K": 0, "1.5K": 1, "2K": 2, "3K": 3, "4K": 4}


def _fit(total: int, ratio: str) -> str:
    aw, ah = (int(v) for v in ratio.split(":"))
    m = math.isqrt(total // (aw * ah * 4))
    return f"{2 * aw * m}*{2 * ah * m}"


_SIZE_MAP: dict[tuple[str, str], str] = {
    (tier, ratio): _fit(total, ratio)
    for tier, total in _TIER_PIXELS.items()
    for ratio in _RATIOS
}

#: 图像集 (image-set) generation. Declared as a capability rather than inferred from the
#: adapter_id, for the reason SeedreamAdapter spells out: a future variant without it
#: would otherwise be sent `enable_sequential: true` and bill for images nobody asked for.
_GROUP_CAPABILITY = "multi_image_group"

#: `n` ceiling in image-set mode. Outside it the API's own range is 1–4, but this adapter
#: never sends n>1 outside image-set mode (see build_payload), so 12 is the only cap that
#: can be hit.
_MAX_SEQUENTIAL_N = 12

#: `content` may hold one text object and 0–9 images.
_MAX_IMAGES = 9


@register_python_adapter
class WanImageAdapter(ModelAdapter):
    """Python Adapter for 万相 2.7 图像生成与编辑 (``wan2.7-image``) — synchronous.

    Three things make this the odd one out among the image adapters:

    - **Request is DashScope-shaped**, like the 万相 video family and unlike Seedream:
      ``{"model", "input": {"messages": [{"role": "user", "content": [{"text"}, {"image"}…]}]},
      "parameters": {…}}``. Reference images are content parts, and their order in that
      array *is* their ordinal — 图1 is ``reference_images[0]`` — which is also the order
      ``bbox_list`` is aligned to.
    - **Regions travel in a structured field**, not in the prompt. It is the first such
      model here, so ``adapters/regions.py`` grew a ``structured=True`` rendering mode:
      the caller's ``[[标记1]]`` placeholder still resolves in place, but to neutral
      wording ("图2中框选的区域") while the numbers go to ``parameters.bbox_list`` in
      **absolute pixels of the original image**. Absolute pixels is why ``image_size`` is
      required here and never inferred.
    - **Response is DashScope-shaped too**: ``output.choices[].message.content[].image``.
    """

    adapter_id = "wan-2-7-image"

    # ── validation ──────────────────────────────────────────────────────────

    def _allowed_tiers(self) -> tuple[str, ...]:
        return tuple(_TIER_PIXELS)

    def validation_corrections(
        self, req: "GenerateImageInput | GenerateVideoInput"
    ) -> dict[str, Any]:
        assert isinstance(req, GenerateImageInput)
        allowed = self._allowed_tiers()
        if req.resolution in allowed:
            return {}
        at_or_below = [t for t in allowed if _TIER_RANK[t] <= _TIER_RANK[req.resolution]]
        fallback = (
            max(at_or_below, key=_TIER_RANK.__getitem__)
            if at_or_below
            else min(allowed, key=_TIER_RANK.__getitem__)
        )
        return {"resolution": fallback}

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateImageInput)
        if not req.prompt.strip():
            return False, f"{self.adapter_id} requires a non-empty prompt"

        allowed = self._allowed_tiers()
        if req.resolution not in allowed:
            return False, (
                f"{self.adapter_id} does not support resolution {req.resolution} "
                f"(supported: {', '.join(allowed)})"
            )

        reference_count = len(req.reference_images or [])
        if reference_count > _MAX_IMAGES:
            return False, f"{self.adapter_id} accepts at most {_MAX_IMAGES} reference_images"

        if req.n > _MAX_SEQUENTIAL_N:
            return False, (
                f"{self.adapter_id} accepts n up to {_MAX_SEQUENTIAL_N} "
                f"(图像集 mode caps the group there); got {req.n}"
            )

        if req.regions:
            # This is the one dialect wanting absolute pixels, so a missing image_size is
            # unrecoverable: guessing one does not fail, it edits a plausible, billed
            # rectangle in the wrong place. Refusing here also lets model="auto" route a
            # size-less regions request to a prompt-coordinate model, which needs none.
            missing = regions_missing_size(req.regions)
            if missing:
                return False, (
                    f"{self.adapter_id} 的 bbox_list 用原图绝对像素坐标，所以每个 region "
                    f"都必须带 image_size=[width, height]（原图尺寸，不是显示画布尺寸）；"
                    f"image_index={missing} 上的 region 没有。尺寸绝不猜测：猜错不会报错，"
                    f"只会在图上另一个位置改出一张看着合理、还要计费的图。"
                )
            # A group is several independent images; a region marks one place on the one
            # image being edited. Upstream documents no interaction between bbox_list and
            # enable_sequential, and the combination has no meaning to fall back on.
            if req.n > 1:
                return False, (
                    f"{self.adapter_id}: regions (交互式编辑) cannot be combined with n>1 "
                    f"(图像集) — a group is several independent images, a region marks one "
                    f"place on the image being edited. Call once per image."
                )
        return True, ""

    # ── request ─────────────────────────────────────────────────────────────

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)

        # supports() is the gate, but build_payload is reachable directly (tests, and any
        # future caller). A region that reached a model without the capability must stop
        # here rather than be quietly dropped into a whole-image edit that generates a
        # picture and bills for it.
        if req.regions and "region_edit" not in self.capabilities:
            raise ValueError(
                f"{self.adapter_id} does not support region editing (regions=), and "
                f"regions are never silently ignored."
            )

        reference_images = list(req.reference_images or [])
        prompt = req.prompt
        if req.regions:
            # structured=True: the placeholder resolves to a referent, not to coordinates
            # — those go to bbox_list below, in a raster the model actually knows.
            prompt = render_prompt(
                prompt, req.regions, req.image_refs, include_names=False, structured=True
            )

        content: list[dict] = [{"text": prompt}]
        content.extend({"image": url} for url in reference_images)

        parameters: dict = {
            "size": self._resolve_size(req.resolution, req.aspect_ratio),
            "watermark": req.watermark,
        }
        # 图像集. `n` is a ceiling in this mode, never a count: the model decides how many
        # images the set holds. Both keys are omitted at n=1 so the request stays in the
        # upstream default (enable_sequential=false, n=1) — and they are only ever sent
        # together, because enable_sequential=true with no `n` defaults to **12**.
        if req.n > 1 and _GROUP_CAPABILITY in self.capabilities:
            parameters["enable_sequential"] = True
            parameters["n"] = req.n
        if req.regions:
            parameters["bbox_list"] = build_bbox_list(req.regions, len(reference_images))
        # thinking_mode is upstream-default-on and is documented as taking effect only
        # for plain text-to-image, so it is sent only where it does something. Reusing
        # quality_tier for it follows kling's mode and gpt-image-2's quality: the tier the
        # caller already sets steers the model rather than a second near-synonym.
        if not reference_images and "enable_sequential" not in parameters:
            parameters["thinking_mode"] = req.quality_tier != "fast"

        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }
        if req.model_specific:
            # Merged one level deep so a caller can override a single parameter (e.g.
            # size, seed, color_palette) without having to restate the whole object.
            for key, value in req.model_specific.items():
                if key == "parameters" and isinstance(value, dict):
                    parameters.update(value)
                else:
                    payload[key] = value
        return payload

    def _resolve_size(self, resolution: str, aspect_ratio: str) -> str:
        # Same-tier square is the only safe fallback: dropping to another tier would
        # change the price band without saying so. Defensive only — every tier covers all
        # eight published ratios, since the table is computed rather than transcribed.
        return _SIZE_MAP.get((resolution, aspect_ratio)) or _SIZE_MAP[(resolution, "1:1")]

    # ── response ────────────────────────────────────────────────────────────

    def parse_response(self, resp: dict) -> NormalizedResult:
        output = resp.get("output") or {}
        urls: list[str] = []
        for choice in output.get("choices") or []:
            for part in (choice.get("message") or {}).get("content") or []:
                image = part.get("image")
                if image:
                    urls.append(image)
        if not urls:
            # Tolerated because this model shares POST /images/generations with Seedream,
            # whose sync responses are flat `data: [{url}]`. Guessing wrong here is loud,
            # not silent: an empty url list trips task_manager's "succeeded but no
            # artifact = failure" invariant.
            urls = [item["url"] for item in (resp.get("data") or []) if item.get("url")]
        return NormalizedResult(
            urls=urls,
            expires_at=_default_expires_at(),   # documented: URLs expire after 24h
            task_id=None,                       # synchronous model has no task_id
            model_used=resp.get("model"),
            seed=None,
            usage=resp.get("usage"),
        )
