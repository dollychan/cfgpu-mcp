"""Per-model input contract — the ``inputs:`` block of adapter.yaml.

Which material slots a model accepts, how many items each takes, and (video) what a
supplied audio track is *for*. Declared by video, image and understanding models, each
with its own tool's slots (``INPUT_SLOTS``). Before this existed those facts lived in three places that drifted
independently: ad-hoc checks inside each ``supports()``, a ``max_reference_*`` field
declared by some models and not others, and the agent-facing ``profile.yaml``, whose
``reference_to_video`` promised audio on models that refuse every audio track.

The contract is declared once here and read by three consumers:

- ``ModelAdapter.supports()`` enforces the counts and the audio ``standalone`` rule;
- ``list_model_profiles`` publishes it per model, so an agent can see *how* a model
  consumes audio before choosing it;
- ``tests/unit/test_video_inputs.py`` probes every adapter's ``supports()`` and fails
  if the declaration and the code disagree in either direction, and
  ``test_media_task_profiles.py`` fails if a profile claims a task its inputs cannot
  carry.

Combination rules (first frame vs. reference material, "driving audio only with a first
frame", one voice sample per visual reference) stay in each adapter's ``supports()``:
they carry model-specific remedies in their refusals and do not cause capability drift.
An undeclared slot is likewise refused by the adapter's own code, so its refusal can
name the right sibling model — the probe test is what keeps the two in agreement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateVideoInput

#: Every material slot of ``generate_video``, in schema order.
VIDEO_INPUT_SLOTS = (
    "first_frame",
    "last_frame",
    "reference_images",
    "reference_videos",
    "reference_audios",
)
_FRAME_SLOTS = frozenset({"first_frame", "last_frame"})

#: The material slots of each tool, per task_type. ``regions`` is a slot like the
#: others — boxes are material the model must read — but its limit is per image.
INPUT_SLOTS = {
    "video": VIDEO_INPUT_SLOTS,
    "image": ("reference_images", "regions"),
    "understand": ("images", "video", "regions"),
}
_VISUAL_SLOTS = ("first_frame", "last_frame", "reference_images", "reference_videos")

#: How a model consumes a supplied audio track. One slot, three different contracts —
#: and the difference decides which model a request like "make a video from this
#: speech" may be routed to:
#:
#: - ``driving``   — the track is the soundtrack and the picture follows it (timing,
#:                   lip movement). The only role that implies the audio is heard as-is.
#: - ``voice``     — a timbre sample bound to a visual reference; the model speaks new
#:                   lines in that voice. The supplied recording itself is not played.
#: - ``reference`` — material the prompt directs (background music, a voice to imitate,
#:                   a rhythm). What happens to it depends on the prompt; nothing
#:                   guarantees it survives into the output unchanged.
AUDIO_ROLES = ("driving", "voice", "reference")

#: How a model consumes a supplied video. The same slot carries two different jobs:
#:
#: - ``reference`` — material the prompt directs (a motion, a style, a subject to keep).
#:                   On models that also edit or extend, the prompt can make it the
#:                   thing edited; nothing about the call says so.
#: - ``source``    — always the video being edited or continued (万相 2.7 i2v's
#:                   ``first_clip``, the 万相 / HappyHorse edit models' source video).
#:                   Sending reference material here edits or extends *it*.
VIDEO_ROLES = ("reference", "source")
_ROLES = {"reference_videos": VIDEO_ROLES, "reference_audios": AUDIO_ROLES}

_SLOT_KEYS = {
    "first_frame": frozenset(),
    "last_frame": frozenset(),
    "reference_images": frozenset({"max"}),
    "reference_videos": frozenset({"max", "role"}),
    "reference_audios": frozenset({"max", "role", "standalone"}),
    "images": frozenset({"max"}),
    "video": frozenset(),
    "regions": frozenset({"max_per_image"}),
}

#: Pre-``inputs`` spellings. Rejected rather than ignored: a leftover limit that is
#: silently dropped stops being enforced and nothing says so.
LEGACY_INPUT_KEYS = (
    "max_reference_images",
    "max_reference_videos",
    "max_reference_audios",
    "allow_audio_only_reference",
    "max_regions_per_image",
)


@dataclass(frozen=True)
class InputSlot:
    """One accepted material slot. Absent from the mapping = not accepted at all."""

    max: int | None = None          # None = no local limit (frames are always 1)
    role: str | None = None         # reference_videos / reference_audios: VIDEO_ROLES / AUDIO_ROLES
    standalone: bool = False        # reference_audios only: valid with no image/video input
    max_per_image: int | None = None  # regions only: boxes per image, None = no local limit

    def describe(self, name: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.max is not None:
            out["max"] = self.max
        if self.max_per_image is not None:
            out["max_per_image"] = self.max_per_image
        if self.role is not None:
            out["role"] = self.role
        if name == "reference_audios":
            out["standalone"] = self.standalone
        return out


def parse_inputs(adapter_id: str, raw: Any, task_type: str = "video") -> dict[str, InputSlot] | None:
    """Validate an ``inputs:`` block. ``None`` = undeclared; ``{}`` = accepts no material.

    A slot set to ``null`` is dropped, which is how a variant removes a slot it
    inherits through ``extends`` (the merge is one level deep, slot by slot).
    """
    if raw is None:
        return None
    if task_type not in INPUT_SLOTS:
        raise ValueError(f"{adapter_id}: inputs: is not declared for {task_type} models")
    if not isinstance(raw, dict):
        raise ValueError(f"{adapter_id}: inputs must be a mapping of slot → settings")
    valid = INPUT_SLOTS[task_type]
    unknown = set(raw) - set(valid)
    if unknown:
        raise ValueError(
            f"{adapter_id}: inputs has unknown slots {sorted(unknown)} "
            f"(valid for {task_type}: {list(valid)})"
        )
    slots: dict[str, InputSlot] = {}
    for name in valid:
        if name not in raw or raw[name] is None:
            continue
        spec = raw[name]
        if not isinstance(spec, dict):
            raise ValueError(f"{adapter_id}: inputs.{name} must be a mapping (use {{}} for no settings)")
        extra = set(spec) - _SLOT_KEYS[name]
        if extra:
            raise ValueError(
                f"{adapter_id}: inputs.{name} has unsupported keys {sorted(extra)} "
                f"(allowed: {sorted(_SLOT_KEYS[name]) or 'none'})"
            )
        for key in ("max", "max_per_image"):
            limit = spec.get(key)
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
                raise ValueError(f"{adapter_id}: inputs.{name}.{key} must be a positive integer")
        role = spec.get("role")
        standalone = spec.get("standalone", False)
        if name in _ROLES and role not in _ROLES[name]:
            raise ValueError(
                f"{adapter_id}: inputs.{name}.role must be one of {list(_ROLES[name])} "
                f"(got {role!r}) — the same slot carries jobs with different results, so "
                "which one this model does cannot be left implicit"
            )
        if name == "reference_audios":
            if not isinstance(standalone, bool):
                raise ValueError(f"{adapter_id}: inputs.reference_audios.standalone must be a boolean")
        slots[name] = InputSlot(
            max=spec.get("max"), role=role, standalone=standalone,
            max_per_image=spec.get("max_per_image"),
        )
    return slots


#: Whether the generated video carries sound — the ``outputs:`` block's one key.
#:
#: - ``always``     — every output has a soundtrack; ``with_audio`` cannot turn it off.
#: - ``switchable`` — ``with_audio`` reaches the request and decides it.
#: - ``never``      — silent output.
#:
#: Absent = undocumented (e.g. an edit model whose sound follows its source). It is
#: what ``synced_audio_output`` means, so a test holds that task equal to
#: ``always``/``switchable``, and ``switchable`` equal to ``with_audio`` changing the
#: payload — the flag is honoured exactly where this says so.
AUDIO_OUTPUTS = ("always", "switchable", "never")


def parse_outputs(adapter_id: str, raw: Any) -> dict[str, str] | None:
    """Validate an ``outputs:`` block. ``None`` = undeclared."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"audio"}:
        raise ValueError(f"{adapter_id}: outputs takes only an audio key")
    audio = raw.get("audio")
    if audio is not None and audio not in AUDIO_OUTPUTS:
        raise ValueError(
            f"{adapter_id}: outputs.audio must be one of {list(AUDIO_OUTPUTS)} (got {audio!r})"
        )
    return {"audio": audio} if audio is not None else {}


def _task_of(req: Any) -> str:
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

    if isinstance(req, GenerateVideoInput):
        return "video"
    return "image" if isinstance(req, GenerateImageInput) else "understand"


def describe_inputs(inputs: dict[str, InputSlot]) -> dict[str, dict[str, Any]]:
    """The agent-facing form, in schema slot order."""
    return {name: slot.describe(name) for name, slot in inputs.items()}


def check_declared_inputs(
    model_name: str, inputs: dict[str, InputSlot], req: Any, *, refuse_undeclared: bool = False
) -> tuple[bool, str]:
    """Enforce declared counts and the audio ``standalone`` rule for declared slots.

    ``refuse_undeclared`` also refuses material in a slot the model did not declare.
    Image and understanding models take it from here; video adapters refuse undeclared
    slots in their own code, so the refusal can name a sibling model. ``regions``'
    per-image limit is checked by ``regions.check_regions``, whose refusal says how to
    split the call.
    """
    if refuse_undeclared:
        for name in INPUT_SLOTS.get(_task_of(req), ()):
            if getattr(req, name, None) and name not in inputs:
                return False, f"{model_name} does not accept {name}"
    for name, slot in inputs.items():
        if slot.max is None or name in _FRAME_SLOTS:
            continue
        values = getattr(req, name) or []
        if len(values) > slot.max:
            return False, f"{model_name} accepts at most {slot.max} {name} (got {len(values)})"
    audio = inputs.get("reference_audios")
    if (
        audio is not None
        and getattr(req, "reference_audios", None)
        and not audio.standalone
        and not any(getattr(req, name) for name in _VISUAL_SLOTS)
    ):
        return False, (
            f"{model_name} does not accept audio-only input; "
            "include at least one image or video input with reference_audios"
        )
    return True, ""
