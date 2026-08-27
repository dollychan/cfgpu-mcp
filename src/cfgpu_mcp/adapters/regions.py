"""Rendering unified ``RegionSpec`` regions into a prompt-embedded coordinate dialect.

Two model families read regions out of the prompt text rather than a structured field:
Seedream 5.0 Pro (editing) and Qwen3.6-Plus (understanding). They speak the *same*
space — a [0, 999] grid over the image, origin top-left — so they share this module
outright. That sharing is the point rather than a convenience: the inclusive-last-cell
rule below is easy to get subtly wrong, and two copies of it would be fixed one at a
time.

The caller's half of the contract is a `[[label]]` placeholder in the prompt, which says
*where in the sentence* a region belongs. Position carries meaning in these dialects —
"put the subject of 图1 <bbox>…</bbox> at 图2 <bbox>…</bbox>" is not the same request
with the boxes swapped — and only the caller knows the sentence, so only the caller can
place them. *Which* image a box is on, however, is ours to say and not the caller's:
image ordinals follow the slot order we were handed, so every rendered box names its own
image (see `_render_region`). Regions that no placeholder mentions are appended as a
structured suffix: never dropped, because a dropped region produces a plausible, billed,
wrong picture.

万相 2.7 图像 (``wan2.7-image``) is the third consumer and the structured one: it has its
own ``bbox_list`` parameter taking **absolute pixels of the original image**, so its
numbers never enter the prompt. It reuses everything here that is not the coordinate
substitution itself — the placeholder grammar, the fail-closed ambiguity rules, the
per-image ordinals — with ``structured=True`` swapping the substituted text for neutral
wording ("图2中框选的区域"), so the sentence keeps a referent while the numbers travel in
their own field. `to_pixels` is the one conversion; `to_grid` is now that same function
over a 1000x1000 image, which is what the grid always was.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

from cfgpu_mcp.adapters.base import models_with_capability
from cfgpu_mcp.errors import CFGPUError

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter
    from cfgpu_mcp.tool_registry import RegionSpec

#: Both dialects divide each axis into this many cells and address them by index, so the
#: last valid index is ``GRID - 1``.
GRID = 1000

#: ``[[...]]`` is deliberately unusual punctuation: it essentially never occurs in
#: Chinese or English prose, so a placeholder cannot be produced by accident and a
#: non-placeholder cannot be eaten by mistake. Nested brackets are excluded so an
#: unbalanced string degrades to "no match" rather than swallowing the rest of the line.
_PLACEHOLDER = re.compile(r"\[\[([^\[\]]{1,200})\]\]")

_CAP_EDIT = "region_edit"
_CAP_UNDERSTAND = "region_understand"


def to_pixels(box: list[float], image_size: "list[int] | tuple[int, int]") -> tuple[int, int, int, int]:
    """Normalised ``[0,1]`` box → integer coordinates on a ``[width, height]`` raster.

    The two edges round differently on purpose. The far edge names the *index of the
    last cell the box covers*, not the coordinate of the image's right edge — hence
    ``ceil(v*w) - 1``. Rounding both edges the same way would make the full-image box
    ``[0,0,1,1]`` come out one cell short, and that off-by-one would then drift a little
    further with every edit round-trip. With this rule ``[0,0,1,1]`` maps to
    ``[0, 0, w-1, h-1]``, matching what the model documentation says the bottom-right
    corner is.

    One function serves both dialects because they differ only in the raster: the
    ``[0, 999]`` grid *is* this conversion over a 1000x1000 image, and 万相 2.7's
    ``bbox_list`` is it over the image's real pixel dimensions. Two implementations of a
    rule this easy to get subtly wrong would be fixed one at a time.
    """
    width, height = image_size
    x1, y1, x2, y2 = box
    px1 = min(max(math.floor(x1 * width), 0), width - 1)
    py1 = min(max(math.floor(y1 * height), 0), height - 1)
    px2 = min(max(math.ceil(x2 * width) - 1, px1), width - 1)
    py2 = min(max(math.ceil(y2 * height) - 1, py1), height - 1)
    return px1, py1, px2, py2


def to_grid(box: list[float]) -> tuple[int, int, int, int]:
    """Normalised ``[0,1]`` box → the ``[0, GRID-1]`` cell indices the prompt dialects use."""
    return to_pixels(box, (GRID, GRID))


def format_bbox(box: list[float]) -> str:
    """``<bbox>x1 y1 x2 y2</bbox>`` — the one form we emit.

    Qwen also accepts ``bbox[x1, y1, x2, y2]`` and other spellings, and Seedream
    documents only this one. Emitting a single form everywhere keeps the two adapters
    byte-identical in the part that matters.
    """
    return "<bbox>{} {} {} {}</bbox>".format(*to_grid(box))


def _ordinal(image_index: int) -> str:
    """These dialects number images from 1 in the prompt ('图 1')."""
    return f"图{image_index + 1}"


def _region_name(region: "RegionSpec", fallback_ordinal: int) -> str:
    return region.label or f"第{fallback_ordinal}个区域"


def _render_region(region: "RegionSpec", *, include_names: bool, fallback_ordinal: int) -> str:
    """One region as the dialect writes it, with or without its human-authored text.

    The image ordinal is part of the region, not decoration placed around it. A box is a
    position *on a particular picture*, and one request routinely carries boxes from two
    of them — "stand the figure from 图2 <bbox>…</bbox> at 图1 <bbox>…</bbox>" is a normal
    composite edit, and it is the form the model's own documentation uses. Emitting bare
    coordinates leaves the model to guess which image each box addresses, and a wrong
    guess is the worst failure available here: a plausible, billed picture edited in the
    wrong place. Coordinates from the second image silently land on the first.

    The ordinal goes out unconditionally rather than only when several images are in
    play. On a single image it is redundant wording; the conditional version would put
    the dangerous case on the branch that runs least and is therefore tested least. A
    caller that also wrote ``[[<ref>]]`` next to the mark gets "图1的图1 <bbox>…</bbox>" —
    wordy but correct, and the alternative (guessing from surrounding text whether an
    ordinal is already there) is the open-matching problem this module exists to avoid.

    ``include_names`` is what separates a reading model from a painting one. A
    generation model is given coordinates and nothing else: a label or a user note in
    its prompt is text it may well render *into the picture*, which is precisely what
    marks are meant not to become. A vision model only reads, so it gets the name (the
    answer comes back saying "标记3", which is the word the user used) and the note.
    """
    parts: list[str] = [f"{_ordinal(region.image_index)} "]
    if include_names:
        parts.append(_region_name(region, fallback_ordinal))
    parts.append(format_bbox(region.box))
    if include_names and region.note:
        parts.append(f"（{region.note}）")
    return "".join(parts)


def _render_region_structured(region: "RegionSpec", *, fallback_ordinal: int, only: bool) -> str:
    """One region as a *referent* rather than as coordinates.

    For a model with its own ``bbox_list`` field the numbers must not be in the prompt at
    all — the model would then be reading the same box twice, once in a raster it was not
    given (normalised? grid? pixels of which image?) and once in the field that actually
    means something. What the sentence still needs is a phrase in the clause the caller
    marked, so the instruction has something to attach to: upstream's own examples read
    "place the alarm clock from image 1 in the selected area of image 2".

    ``only`` collapses "第1个区域" to plain "区域" when the image carries a single box,
    which is the common case; a numbered reference to a set of one invites the model to
    go looking for the others.

    Names and notes are withheld here for the same reason as in ``_render_region``: this
    is a painting model, and a label in its prompt is text it may render into the picture.
    """
    where = _ordinal(region.image_index)
    if only:
        return f"{where}中框选的区域"
    return f"{where}中框选的第{fallback_ordinal}个区域"


def build_bbox_list(regions: list["RegionSpec"], n_images: int) -> list[list[list[int]]]:
    """Regions → the per-image ``bbox_list`` 万相 2.7 takes, in absolute pixels.

    The outer list is positionally aligned with the image slot and padded with ``[]`` for
    every un-annotated image — that padding is exactly what ``RegionSpec``'s flat shape
    exists to keep away from the caller, since a short list does not fail, it lands the
    box on the wrong image.

    Boxes keep the order they were passed in, so the *n*-th box on an image is the one the
    prompt calls 第 n 个区域.

    ``image_size`` is required here and never guessed: this is the one dialect that wants
    absolute pixels, and a wrong size does not fail — it edits a plausible, billed
    rectangle somewhere else on the picture. ``supports()`` rejects such a request long
    before this, so reaching the raise means a direct caller bypassed that gate.
    """
    out: list[list[list[int]]] = [[] for _ in range(n_images)]
    for region in regions:
        if region.image_size is None:
            raise ValueError(
                f"region on image_index={region.image_index} has no image_size; "
                f"bbox_list is in absolute pixels and a size is never inferred."
            )
        out[region.image_index].append(list(to_pixels(region.box, region.image_size)))
    return out


def regions_missing_size(regions: list["RegionSpec"]) -> list[int]:
    """Image indices carrying a region with no ``image_size``, for ``supports()``."""
    return sorted({r.image_index for r in regions if r.image_size is None})


def _ambiguous(token: str, candidates: list[str], *, kind: str) -> CFGPUError:
    return CFGPUError(
        error_type="invalid_params",
        user_message=(
            f"prompt 里的占位符 [[{token}]] {kind}：候选有 {', '.join(candidates)}。"
            f"请改用其中一种完整写法，本次请求未发送。"
        ),
        original={"placeholder": token, "candidates": candidates},
        # The card cannot answer "which of these did you mean" — the message already
        # lists the exact alternatives, so the generic pointer would only add noise.
        card_hint=False,
    )


def render_prompt(
    prompt: str,
    regions: list["RegionSpec"] | None,
    image_refs: list[str] | None,
    *,
    include_names: bool,
    structured: bool = False,
) -> str:
    """Substitute ``[[...]]`` placeholders and append whatever regions went unmentioned.

    Three placeholder forms, all written in the words the caller already has:

    ``[[<label>]]``           one region, when that name is unambiguous in this call
    ``[[<ref>#<label>]]``     the qualified form, when a name repeats across images
    ``[[<ref>]]``             a whole image, rendered as that dialect's image ordinal

    Region names are unique per image but may legitimately repeat across images (every
    image restarts at 标记1). So a bare ``[[标记1]]`` with two images in play is a
    genuine ambiguity, and it **fails closed**. Picking the first would edit the wrong
    image: an outcome that produces a picture, costs money, and looks reasonable — the
    worst failure mode available. An unmatched placeholder, by contrast, is left alone
    and its region falls through to the suffix: the caller may simply not be using
    placeholders, and being wrong about that costs precision, not correctness.

    ``structured=True`` is for a model with its own ``bbox_list`` field. Placeholders
    resolve to neutral wording instead of coordinates, and there is **no trailing
    suffix**: the suffix exists in the prose dialects because the prompt is the only
    channel a box has, whereas here every region reaches the model in its own field
    whether or not a placeholder named it. Appending a bare mention with no instruction
    attached would not be a safety net — "图1中框选的第2个区域。" alone on a line reads as
    one more thing to go and edit.
    """
    regions = list(regions or [])
    refs = list(image_refs or [])
    ref_index = {ref: i for i, ref in enumerate(refs) if ref}

    # Per-image ordinal, so an unlabelled region can still be named in the suffix.
    per_image_ordinal: dict[int, int] = {}
    ordinals: list[int] = []
    for r in regions:
        per_image_ordinal[r.image_index] = per_image_ordinal.get(r.image_index, 0) + 1
        ordinals.append(per_image_ordinal[r.image_index])

    by_label: dict[str, list[int]] = {}
    for i, r in enumerate(regions):
        if r.label:
            by_label.setdefault(r.label, []).append(i)

    def qualified(i: int) -> str:
        r = regions[i]
        head = refs[r.image_index] if r.image_index < len(refs) else _ordinal(r.image_index)
        return f"[[{head}#{r.label}]]"

    used: set[int] = set()

    def render_one(i: int) -> str:
        if structured:
            return _render_region_structured(
                regions[i],
                fallback_ordinal=ordinals[i],
                only=per_image_ordinal[regions[i].image_index] == 1,
            )
        return _render_region(
            regions[i], include_names=include_names, fallback_ordinal=ordinals[i]
        )

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if "#" in token:
            ref, _, label = token.partition("#")
            idx = ref_index.get(ref)
            if idx is None:
                return match.group(0)  # unknown handle → leave it, region falls to suffix
            hits = [i for i in by_label.get(label, []) if regions[i].image_index == idx]
            if len(hits) != 1:
                return match.group(0)
            used.add(hits[0])
            return render_one(hits[0])

        hits = by_label.get(token, [])
        is_ref = token in ref_index
        if hits and is_ref:
            raise _ambiguous(
                token,
                [f"[[{token}]] (整张图)", *(qualified(i) for i in hits)],
                kind="同时是一个图片句柄和一个标记名",
            )
        if len(hits) > 1:
            raise _ambiguous(
                token, [qualified(i) for i in hits], kind="在多张图上都有同名标记"
            )
        if len(hits) == 1:
            used.add(hits[0])
            return render_one(hits[0])
        if is_ref:
            return _ordinal(ref_index[token])
        return match.group(0)

    rendered = _PLACEHOLDER.sub(substitute, prompt)

    leftovers = [] if structured else [i for i in range(len(regions)) if i not in used]
    if leftovers:
        heading = "标注区域" if include_names else "参考区域"
        # No ordinal is prepended here: _render_region already carries it, and the two
        # paths must render a box identically — a suffix box and an inline box differ
        # only in where the caller put it, never in what it says.
        items = [render_one(i) for i in leftovers]
        rendered = f"{rendered}\n\n{heading}：" + "；".join(items) + "。"
    return rendered


#: Appended for reading models when regions are present. Qwen otherwise echoes the
#: coordinates straight back into its answer — and that answer becomes a tool result in
#: the caller's context, which is the one place the coordinates were kept out of. Worse,
#: the echoed form is *exactly* the dialect one editing model accepts, so a model that
#: copies it forward gets a real effect from some models and silent nothing from others.
#: "Works sometimes" is the failure mode that trains a habit and only breaks later.
#:
#: A prompt convention beats filtering the response: filtering is an open matching
#: problem whose misses are silent and whose false positives corrupt legitimate numbers,
#: and it would have to answer "which image is this box on?" — a question that need not
#: be asked at all. The convention also makes the answer *better*, not merely cleaner:
#: "标记3 里是皮卡丘" hands back the caller's own word for the thing.
_OUTPUT_CONTRACT_NAMED = (
    "回答时请用标记名（如「{example}」）指代上述区域，不要在回答中输出 bbox 坐标。"
)
_OUTPUT_CONTRACT_PLAIN = (
    "回答时请用「图N的第K个区域」指代上述区域，不要在回答中输出 bbox 坐标。"
)


def output_contract(regions: list["RegionSpec"]) -> str:
    """The 'answer by name, not by coordinate' instruction for a reading model."""
    example = next((r.label for r in regions if r.label), None)
    return _OUTPUT_CONTRACT_NAMED.format(example=example) if example else _OUTPUT_CONTRACT_PLAIN


def unsupported_reason(model_name: str, *, understand: bool) -> str:
    """The refusal text for a model that cannot take regions.

    With no fallback rendering path behind it, this error *is* the end of the line — so
    it has to be executable, not merely correct. It names the models that do accept
    regions, and for editing it names a second route that needs no new machinery at all:
    ask a vision model what is inside each region, then rewrite the request as ordinary
    description. Describing a place in words is how this was always done without marks;
    the regions just make the description accurate.
    """
    if understand:
        alternatives = models_with_capability(_CAP_UNDERSTAND)
        tail = (
            f"请改用支持的模型：{' / '.join(alternatives)}。"
            if alternatives
            else "当前没有支持区域理解的模型可用。"
        )
        return f"{model_name} 不支持按区域理解图片（缺 {_CAP_UNDERSTAND} 能力）。{tail}"

    alternatives = models_with_capability(_CAP_EDIT)
    first = (
        f"(1) 换用支持的模型：{' / '.join(alternatives)}；"
        if alternatives
        else "(1) 当前没有支持区域编辑的模型可用；"
    )
    return (
        f"{model_name} 不支持区域编辑（缺 {_CAP_EDIT} 能力），且区域绝不会被静默忽略"
        f"——那会产出一张整图重绘、看着合理、还要计费的图。两种做法："
        f"{first}"
        f"(2) 保持当前模型，改走语义描述：先调 understand_vision(images=[…], regions=[…]) "
        f"问清每个标记里是什么物体、在画面什么位置、与周围的关系，再把答案改写成纯自然语言的 "
        f"prompt（如「把左下角压在蓝色地毯边缘的那辆红色玩具车换成…」），不带 regions 重新调用。"
    )


def check_regions(adapter: "ModelAdapter", req) -> tuple[bool, str]:
    """The per-model half of region validation, for ``ModelAdapter.supports()``.

    Structural checks (index bounds, empty image slot, crossed ``image_size``) are not
    here: they are model-independent, so they run at the schema layer where no model can
    route around them and no adapter can repair them. What is left is genuinely
    per-model — whether this model reads regions at all, and how many boxes it takes per
    image — which is why it cannot live in a per-tool schema either.
    """
    from cfgpu_mcp.tool_registry import UnderstandVisionInput

    regions = getattr(req, "regions", None)
    if not regions:
        return True, ""
    understand = isinstance(req, UnderstandVisionInput)
    capability = _CAP_UNDERSTAND if understand else _CAP_EDIT
    if capability not in adapter.capabilities:
        return False, unsupported_reason(adapter.model_name, understand=understand)
    cap = adapter.max_regions_per_image
    if cap is not None:
        counts: dict[int, int] = {}
        for r in regions:
            counts[r.image_index] = counts.get(r.image_index, 0) + 1
        worst = max(counts.items(), key=lambda kv: kv[1])
        if worst[1] > cap:
            return False, (
                f"{adapter.model_name} 每张图最多接受 {cap} 个区域，"
                f"但 image_index={worst[0]} 上有 {worst[1]} 个。"
                f"请分多次调用，每次只处理其中 {cap} 个区域。"
            )
    return True, ""
