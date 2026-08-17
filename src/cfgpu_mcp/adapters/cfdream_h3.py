"""MiniMax H3, served by the co-located comfy-gateway (``provider: comfy``).

Two model ids over one contract (comfy-gateway ``API.md`` §5), because they are
two sets of weights that cannot be mixed:

- ``cfdream/minimax-h3`` — the ``fl2va`` weights: text-to-video, image-to-video
  and first/last-frame, all through one node whose frame inputs are optional.
  It rejects ``reference_*``.
- ``cfdream/minimax-h3-r2v`` — the ``ref2va`` weights: reference materials
  (images / videos / audio) drive the generation. It rejects
  ``first_frame`` / ``last_frame`` and needs at least one reference.

Both mutual exclusions are enforced in ``supports()`` rather than left to the
gateway's 400. That is what lets ``model="auto"`` route *between* the two
instead of picking one and failing: the router drops candidates whose
``supports()`` says no, so a request carrying ``reference_images`` simply has
only the r2v model left standing.

Written in Python rather than as a ``payload_mapping`` because two of the
gateway's response fields have no YAML route. ``seed`` is what the caller
reproduces a good result with, and ``GenericAdapter`` hardcodes ``seed=None``;
``expires_at`` is the real presigned-URL expiry, and ``GenericAdapter`` always
reports "24h from now", which overstates the remaining life of a link by
however long the task sat in the gateway's serial queue. Having a class here
also means ``build_payload`` can send real ints/bools/lists instead of
``_render()``'s stringified placeholders — a list would otherwise arrive as its
Python repr.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateVideoInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput

# comfy-gateway API.md §5 — the COMFY_AUTOGROW_V3 `max` on the r2v node.
MAX_REFERENCE_IMAGES = 9
MAX_REFERENCE_VIDEOS = 3
MAX_REFERENCE_AUDIOS = 3


def _parse_expires_at(raw: object) -> datetime:
    """Parse the gateway's ISO ``expires_at``; fall back to the fleet default.

    The gateway reports when the presigned URL actually expires, which is not
    "24 hours from now": the clock started when the artifact was published, and
    a task can sit in the gateway's single-slot queue for a while before that.
    A caller that re-hosts the artifact records this timestamp, so overstating
    it produces a link that is dead before its recorded expiry.
    """
    if not isinstance(raw, str) or not raw:
        return _default_expires_at()
    try:
        # fromisoformat only learned to accept a trailing "Z" in 3.11; normalize
        # so the parse doesn't depend on the interpreter version.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _default_expires_at()


@register_python_adapter
class CfdreamH3Adapter(ModelAdapter):
    """``cfdream/minimax-h3`` — t2v / i2v / first-last-frame (fl2va weights)."""

    adapter_id = "cfdream-minimax-h3"

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput") -> dict:
        assert isinstance(req, GenerateVideoInput)
        payload: dict = {
            "model": self.cfgpu_model_id,
            "prompt": req.prompt,
            "duration_seconds": self.resolve_duration_seconds(req),
            "resolution": req.resolution,
            "aspect_ratio": req.aspect_ratio,
            "with_audio": req.with_audio,
        }
        self._add_materials(payload, req)
        if req.model_specific:
            # `seed` rides here and is a top-level field on the wire, not a nested
            # object (gateway API.md §2). Omitted → the gateway randomizes, which it
            # must: H3's reference-image adherence is seed-sensitive, so a fixed
            # seed would turn an occasional failure into a permanent one.
            payload.update(req.model_specific)
        return payload

    def _add_materials(self, payload: dict, req: GenerateVideoInput) -> None:
        """Add the model's own material slots. Unset slots are omitted entirely.

        Not sent-as-empty: the gateway treats absent and empty alike, but an
        empty ``reference_images: []`` on the t2v model reads, to anyone looking
        at the stored payload later, as "references were requested and dropped".
        """
        if req.first_frame:
            payload["first_frame"] = req.first_frame
        if req.last_frame:
            payload["last_frame"] = req.last_frame

    def extract_eta(self, resp: dict) -> dict[str, Any] | None:
        """The gateway's own ETA from ``POST /v1/video/generations`` (its API.md §2).

        ``queue_ahead_seconds`` is the half that matters and the half no client can
        compute: one serial GPU means the wait is dominated by whatever is already
        in line, which only the gateway can see. Recomputing the execution estimate
        here would also fork the gateway's calibration — it is re-derived from real
        measurements and drifts (comfy-gateway DESIGN.md §10.10).
        """
        eta = {
            k: resp[k]
            for k in ("eta_seconds", "estimated_seconds", "queue_ahead_seconds")
            if isinstance(resp.get(k), int | float)
        }
        return eta or None

    def parse_response(self, resp: dict) -> NormalizedResult:
        data = resp.get("data") or []
        first = data[0] if data and isinstance(data[0], dict) else {}
        url = first.get("url")
        return NormalizedResult(
            urls=[url] if isinstance(url, str) and url else [],
            expires_at=_parse_expires_at(resp.get("expires_at")),
            task_id=resp.get("id"),
            # Left to TaskManager, which stamps the public model_name. The gateway
            # doesn't echo a model field on the poll response anyway.
            model_used=None,
            # The seed actually used — caller-supplied or gateway-randomized.
            # This is the whole reproducibility handle: pass it back as
            # model_specific={"seed": N} to re-generate the same video.
            seed=resp.get("seed"),
            # width/height/length/fps/actual_duration/gpu_seconds — the *effective*
            # geometry, computed at run time by ResolutionSelector and by the frame
            # quantizer, so it does not simply echo the request.
            usage=resp.get("usage"),
        )

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if self.resolve_duration_seconds(req) == -1:
            return False, f"{self.model_name} requires an explicit duration (no -1 smart mode)"
        if req.reference_images or req.reference_videos or req.reference_audios:
            return False, (
                f"{self.model_name} does not accept reference_images / reference_videos / "
                f"reference_audios — use cfdream/minimax-h3-r2v for reference-driven "
                f"generation (it is a different set of weights, not a different mode)"
            )
        return True, ""


@register_python_adapter
class CfdreamH3RefAdapter(CfdreamH3Adapter):
    """``cfdream/minimax-h3-r2v`` — reference-material-driven (ref2va weights)."""

    adapter_id = "cfdream-minimax-h3-r2v"

    def _add_materials(self, payload: dict, req: GenerateVideoInput) -> None:
        if req.reference_images:
            payload["reference_images"] = list(req.reference_images)
        if req.reference_videos:
            payload["reference_videos"] = list(req.reference_videos)
        if req.reference_audios:
            payload["reference_audios"] = list(req.reference_audios)

    def supports(self, req: "GenerateImageInput | GenerateVideoInput") -> tuple[bool, str]:
        # Skip CfdreamH3Adapter.supports — it rejects the very materials this
        # model exists for — but keep its duration rule by re-stating it below.
        ok, reason = ModelAdapter.supports(self, req)
        if not ok:
            return False, reason
        assert isinstance(req, GenerateVideoInput)
        if self.resolve_duration_seconds(req) == -1:
            return False, f"{self.model_name} requires an explicit duration (no -1 smart mode)"
        if req.first_frame or req.last_frame:
            return False, (
                f"{self.model_name} does not accept first_frame / last_frame — "
                f"use cfdream/minimax-h3 for those"
            )
        if not (req.reference_images or req.reference_videos or req.reference_audios):
            return False, (
                f"{self.model_name} needs at least one reference_images / reference_videos / "
                f"reference_audios — use cfdream/minimax-h3 for plain text-to-video"
            )
        for name, values, cap in (
            ("reference_images", req.reference_images, MAX_REFERENCE_IMAGES),
            ("reference_videos", req.reference_videos, MAX_REFERENCE_VIDEOS),
            ("reference_audios", req.reference_audios, MAX_REFERENCE_AUDIOS),
        ):
            if values and len(values) > cap:
                return False, f"{self.model_name} accepts at most {cap} {name} (got {len(values)})"
        return True, ""
