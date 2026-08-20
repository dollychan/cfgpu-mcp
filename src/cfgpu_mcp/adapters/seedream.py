from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import (
    ModelAdapter,
    _default_expires_at,
    models_with_capability,
    register_python_adapter,
)
from cfgpu_mcp.adapters.regions import render_prompt
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput

# ── size ────────────────────────────────────────────────────────────────────
#
# The API takes `size` two ways and they cannot be mixed: an exact `WxH` pixel pair, or
# a tier name ("2K") whose geometry the model then infers from the prompt. Our unified
# schema always has an explicit `aspect_ratio`, so honouring it means emitting pixels —
# with one deliberate exception below.
#
# The tables are per-family, not shared. That is not redundancy: Seedream 5.0 Pro and
# the Lite/4.x line publish *different* pixel values for the same tier and ratio (2K
# 16:9 is 2816x1584 on Pro, 2848x1600 on Lite), and Pro's total-pixel ceiling is under a
# quarter of the others'. One shared table means one of the two families silently gets
# the other's geometry.
#
# Each family's real constraint is a total-pixel range, and the tier sets below are
# simply the presets that fall inside it:
#
#   Pro        [921600, 4624220]     → 1K / 1.5K / 2K   (3K and 4K exceed the ceiling)
#   Lite, 4.5  [3686400, 16777216]   → 2K / 3K / 4K     (1K is under the floor)
#   4.0        [921600, 16777216]    → 1K / 2K / 3K / 4K

_RATIOS = ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")


def _table(tier: str, sizes: tuple[str, ...]) -> dict[tuple[str, str], str]:
    return {(tier, ratio): size for ratio, size in zip(_RATIOS, sizes)}


# Lite / 4.5 / 4.0 share one published table for 2K, 3K and 4K.
_SIZE_MAP: dict[tuple[str, str], str] = {
    **_table("2K", ("2048x2048", "2304x1728", "1728x2304", "2848x1600",
                    "1600x2848", "2496x1664", "1664x2496", "3136x1344")),
    **_table("3K", ("3072x3072", "3456x2592", "2592x3456", "4096x2304",
                    "2304x4096", "3744x2496", "2496x3744", "4704x2016")),
    **_table("4K", ("4096x4096", "4704x3520", "3520x4704", "5504x3040",
                    "3040x5504", "4992x3328", "3328x4992", "6240x2656")),
}

# 4.0 additionally reaches down to 1K (its pixel floor is 921600, where 1K 16:9 lands
# exactly). Its 1K geometry differs from Pro's for the wide ratios.
_SIZE_MAP_4_0: dict[tuple[str, str], str] = {
    **_SIZE_MAP,
    **_table("1K", ("1024x1024", "1152x864", "864x1152", "1280x720",
                    "720x1280", "1248x832", "832x1248", "1512x648")),
}

# Pro's own 2K table. 1K and 1.5K are not here on purpose — see _PASSTHROUGH_TIERS.
_SIZE_MAP_PRO: dict[tuple[str, str], str] = _table(
    "2K", ("2048x2048", "2368x1776", "1776x2368", "2816x1584",
           "1584x2816", "2496x1664", "1664x2496", "3136x1344"),
)

#: Tiers sent as a tier name rather than a pixel pair. Pro's price steps at 1K, and its
#: documentation states 1.5K is billed at the 1K rate — which can only hold if the tier
#: name is what billing reads, since 1.5K's pixel count sits well inside the upper band.
#: Emitting exact pixels for these two would therefore double the price of the call to
#: buy an exact aspect ratio the model is expected to infer from the prompt anyway. The
#: caller's `aspect_ratio` is consequently advisory at these tiers.
_PASSTHROUGH_TIERS: dict[str, frozenset[str]] = {
    "pro": frozenset({"1K", "1.5K"}),
}

_TIER_TABLE: dict[str, dict[tuple[str, str], str]] = {
    "pro": _SIZE_MAP_PRO,
    "4-0": _SIZE_MAP_4_0,
    "lite": _SIZE_MAP,
}

_ALLOWED_TIERS: dict[str, tuple[str, ...]] = {
    "pro": ("1K", "1.5K", "2K"),
    "4-0": ("1K", "2K", "3K", "4K"),
    "lite": ("2K", "3K", "4K"),
}

#: Ordering used to pick a fallback tier — the highest supported tier at or below what
#: was asked for, so a correction never silently *upgrades* a caller into a bigger,
#: pricier image than they requested.
_TIER_RANK = {"1K": 0, "1.5K": 1, "2K": 2, "3K": 3, "4K": 4}

#: 组图 (sequential image generation) is declared, not inferred from the adapter_id. Pro
#: is the only family member without it today, but "not pro" is the wrong question to ask:
#: a future variant that also lacks it would silently be sent
#: ``sequential_image_generation: auto`` and generate — and bill for — images nobody
#: asked for.
_GROUP_CAPABILITY = "multi_image_group"

#: 输入的参考图数量 + 最终生成的图片数量 ≤ 15, for the models that do 组图 at all.
_GROUP_TOTAL_CAP = 15


@register_python_adapter
class SeedreamAdapter(ModelAdapter):
    """Python Adapter for Doubao Seedream (synchronous image models).

    Base for doubao-seedream-5-0-lite; also reused (via the `extends` chain) by the
    4.0/4.5 variants and doubao-seedream-5-0-pro. The families differ in three ways that
    matter here — supported resolution tiers, the pixel table behind each tier, and
    whether 组图 (n>1) exists at all — so nearly every method branches on ``_family``.
    """

    adapter_id = "doubao-seedream-5-0-lite"

    @property
    def _family(self) -> str:
        if self.adapter_id == "doubao-seedream-5-0-pro":
            return "pro"
        if self.adapter_id == "doubao-seedream-4-0":
            return "4-0"
        return "lite"

    def _resolve_size(self, resolution: str, aspect_ratio: str) -> str:
        family = self._family
        if resolution in _PASSTHROUGH_TIERS.get(family, frozenset()):
            return resolution
        table = _TIER_TABLE[family]
        # Same-tier square is the only safe fallback: dropping to another tier would
        # change the price band without saying so.
        return table.get((resolution, aspect_ratio)) or table.get((resolution, "1:1"), "2048x2048")

    def validation_corrections(
        self, req: "GenerateImageInput | GenerateVideoInput"
    ) -> dict[str, Any]:
        assert isinstance(req, GenerateImageInput)
        family = self._family
        allowed = _ALLOWED_TIERS[family]
        corrected: dict[str, Any] = {}

        resolution = req.resolution
        if resolution not in allowed:
            at_or_below = [t for t in allowed if _TIER_RANK[t] <= _TIER_RANK[resolution]]
            resolution = (
                max(at_or_below, key=_TIER_RANK.__getitem__)
                if at_or_below
                else min(allowed, key=_TIER_RANK.__getitem__)
            )
            corrected["resolution"] = resolution

        # Defensive only: every published table covers all eight ratios, so this cannot
        # fire today. It stays so that adding a tier with partial rows degrades to a
        # reported correction rather than to silently unreported geometry.
        if (
            resolution not in _PASSTHROUGH_TIERS.get(family, frozenset())
            and (resolution, req.aspect_ratio) not in _TIER_TABLE[family]
        ):
            corrected["aspect_ratio"] = "1:1"
        return corrected

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateImageInput)
        if not req.prompt.strip():
            return False, f"{self.adapter_id} requires a non-empty prompt"

        family = self._family
        # Checked here, not only in validation_corrections, so the *billed* path rejects
        # what the preflight would have corrected. Without it the two disagree in the
        # worst direction: validate_only reports `corrected_args: {resolution: 2K}` while
        # a caller that ignores it sends 5504x3040 to a model whose ceiling is 4624220
        # pixels and gets an opaque upstream 400. It also lets model="auto" route a 4K
        # request away from Pro instead of failing.
        allowed = _ALLOWED_TIERS[family]
        if req.resolution not in allowed:
            return False, (
                f"{self.adapter_id} does not support resolution {req.resolution} "
                f"(supported: {', '.join(allowed)})"
            )

        # The reference ceiling is a per-model fact (Pro takes 10, the rest 14), unlike
        # 组图 below, which is a declared capability.
        max_refs = 10 if family == "pro" else 14
        reference_count = len(req.reference_images or [])
        if reference_count > max_refs:
            return False, f"{self.adapter_id} accepts at most {max_refs} reference_images"

        does_groups = _GROUP_CAPABILITY in self.capabilities
        if req.n > 1 and not does_groups:
            alternatives = models_with_capability(_GROUP_CAPABILITY)
            tail = (
                f"改用支持组图的模型：{' / '.join(alternatives)}，或 model='auto'。"
                if alternatives
                else "当前没有支持组图的模型可用。"
            )
            return False, (
                f"{self.model_name} 只生成单张图片，不支持 n>1（组图 / "
                f"sequential_image_generation）。{tail}"
            )
        if does_groups and reference_count + req.n > _GROUP_TOTAL_CAP:
            return False, (
                f"{self.adapter_id} requires reference_images count + n <= "
                f"{_GROUP_TOTAL_CAP} (got {reference_count} + {req.n})"
            )
        # Region editing and 组图 are mutually exclusive in shape: a group is several
        # independent images, while a region names a place on the one image being
        # edited. Only Pro takes regions and Pro has no 组图, so this is unreachable
        # today — it exists so a future group-capable region model cannot pass silently.
        if req.regions and req.n > 1:
            return False, (
                f"{self.adapter_id}: regions (区域编辑) cannot be combined with n>1 "
                f"(组图) — a group is several independent images, a region marks one "
                f"place on the image being edited. Call once per image."
            )
        return True, ""

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateImageInput)

        # supports() is the gate; this is the direct-call backstop. Keyed on the declared
        # capability rather than on "is this Pro" so a future single-image variant cannot
        # slip through and have 组图 switched on for it.
        if req.n and req.n > 1 and _GROUP_CAPABILITY not in self.capabilities:
            raise ValueError(
                f"{self.model_name} generates a single image and does not support n>1 "
                f"(组图 / sequential_image_generation)."
            )

        # supports() is the gate, but build_payload is also reachable directly (tests,
        # and any future caller). A region that reached a model without the capability
        # must stop here rather than be quietly dropped into an ordinary whole-image
        # edit that generates a picture and bills for it.
        if req.regions and "region_edit" not in self.capabilities:
            raise ValueError(
                f"{self.adapter_id} does not support region editing (regions=), and "
                f"regions are never silently ignored."
            )

        prompt = req.prompt
        if req.regions:
            # Coordinates go into the prompt text — this family has no structured box
            # field. Names and notes are deliberately withheld (include_names=False):
            # this model paints what it reads, and "标记1" in the prompt is a string it
            # may render into the picture.
            prompt = render_prompt(
                prompt, req.regions, req.image_refs, include_names=False
            )

        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "prompt": prompt,
            "size": self._resolve_size(req.resolution, req.aspect_ratio),
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
            # 组图. Note "auto" does not mean "give me exactly n": the model decides both
            # *whether* to return a group and how many images it contains, and max_images
            # only caps it — so n is a ceiling, and fewer is a normal outcome, not a
            # failure. There is no upstream setting that forces an exact count.
            # The field is omitted entirely at n=1 because `disabled` is already the
            # upstream default, and Pro rejects the key even when set to `disabled`.
            payload["sequential_image_generation"] = "auto"
            payload["sequential_image_generation_options"] = {"max_images": req.n}
        if req.watermark is not None:
            payload["watermark"] = req.watermark
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        # Synchronous: response contains results directly
        data = resp.get("data") or []
        urls = [item["url"] for item in data if "url" in item]
        # In a 组图 response each slot may carry its own error instead of a url, and the
        # request as a whole still returns HTTP 200. Reporting only the urls would hand
        # back two images for a request that asked for four with nothing saying why —
        # and the two causes need opposite responses from the caller: a moderation
        # rejection means rewrite the prompt (the remaining slots were still attempted),
        # while an upstream 500 means generation *stopped there* and the rest were never
        # attempted at all, so retrying is worthwhile. Neither is inferable from a short
        # url list.
        partial_errors = [
            {
                "index": i,
                "code": (item.get("error") or {}).get("code"),
                "message": (item.get("error") or {}).get("message"),
            }
            for i, item in enumerate(data)
            if item.get("error")
        ]
        return NormalizedResult(
            urls=urls,
            expires_at=_default_expires_at(),
            task_id=None,          # Synchronous model has no task_id
            model_used=resp.get("model"),
            seed=None,
            usage=resp.get("usage"),
            partial_errors=partial_errors or None,
        )
