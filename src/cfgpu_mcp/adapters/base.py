from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from cfgpu_mcp.adapters.inputs import (
    LEGACY_INPUT_KEYS,
    InputSlot,
    check_declared_inputs,
    parse_inputs,
    parse_outputs,
)
from cfgpu_mcp.task_catalog import parse_tasks

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import (
        GenerateAudioInput,
        GenerateImageInput,
        GenerateVideoInput,
        NormalizedResult,
        UnderstandVisionInput,
    )

def _default_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


# Global registry: adapter_id → Python Adapter class (Method B)
_PYTHON_ADAPTERS: dict[str, type["ModelAdapter"]] = {}


def register_python_adapter(cls: type["ModelAdapter"]) -> type["ModelAdapter"]:
    """Class decorator: register a Python Adapter so Registry can discover it."""
    _PYTHON_ADAPTERS[cls.adapter_id] = cls
    return cls


def get_python_adapters() -> dict[str, type["ModelAdapter"]]:
    return _PYTHON_ADAPTERS


def models_with_task(task: str) -> list[str]:
    """Public ``model_name``s declaring canonical ``task``, or ``[]`` if the registry is unreachable.

    For refusal messages that need to name the alternatives. Read from the registry rather
    than hard-coded so the advice cannot go stale as the fleet changes — and never allowed
    to raise, because building a nicer error is not a reason to lose the real one.
    """
    try:
        from cfgpu_mcp.config import get_registry

        return sorted(a.model_name for a in get_registry().list_all() if task in a.tasks)
    except Exception:  # pragma: no cover - defensive: advice must never break validation
        return []


def frames_vs_references_reason(
    model_name: str,
    req: Any,
    *,
    reference_model: str | None = None,
    frame_model: str | None = None,
) -> str:
    """The refusal for first/last frames mixed with ``reference_*`` media, with both fixes.

    The bare fact ("mutually exclusive") leaves the caller to guess, and the two ways out
    produce different videos: a frame is the video's literal opening picture, a reference
    only carries the subject into a newly composed shot. Which one was meant is in the
    prompt, which only the caller can read — so both routes are spelled out, each with
    the exact argument moves, and the choice stays with the caller. Converting one into
    the other here would be a silent substitution: a plausible, billed, different video.

    ``reference_model`` / ``frame_model`` describe the switch for that route when this
    model cannot take it (HappyHorse ships each scenario as its own model), inserted after
    "改用" verbatim — e.g. ``"model=happyhorse-1.0-r2v"``; ``None`` means this model takes
    it. ``req`` is only read.
    """
    frames = [n for n in ("first_frame", "last_frame") if getattr(req, n, None)]
    refs = [
        n for n in ("reference_images", "reference_videos", "reference_audios")
        if getattr(req, n, None)
    ]
    frame_list = " / ".join(frames)
    ref_list = " / ".join(refs)

    if reference_model is None:
        keep_refs = f"，{'、'.join(n for n in refs if n != 'reference_images')} 保留" if any(
            n != "reference_images" for n in refs
        ) else ""
        merge = "（与已有参考图合并）" if "reference_images" in refs else ""
        route_ref = (
            f"把 {frame_list} 的图移入 reference_images{merge}，"
            f"删除 {frame_list}{keep_refs}"
        )
    else:
        route_ref = (
            f"改用 {reference_model}，把 {frame_list} 的图放入 reference_images，"
            f"删除 {frame_list}"
        )
    if frame_model is None:
        route_frame = f"保留 {frame_list}，删除 {ref_list}"
    else:
        route_frame = f"改用 {frame_model}，保留 {frame_list}，删除 {ref_list}"
    lost = "（reference_audios 删除后不再驱动配音/对口型）" if "reference_audios" in refs else ""

    return (
        f"{model_name}: {frame_list} is mutually exclusive with {ref_list}. "
        f"二者生成的是不同的视频，请按 prompt 的意图二选一："
        f"① 图片用于确定主体/人物形象，场景与构图由 prompt 重新描述 → {route_ref}；"
        f"② 图片需原样作为视频开场画面 → {route_frame}{lost}。"
    )


@dataclass
class PollConfig:
    base_interval: float = 5.0
    max_interval: float = 20.0
    backoff_factor: float = 1.3
    default_timeout: int = 600

    @classmethod
    def from_dict(cls, d: dict) -> "PollConfig":
        return cls(
            base_interval=d.get("base_interval", 5.0),
            max_interval=d.get("max_interval", 20.0),
            backoff_factor=d.get("backoff_factor", 1.3),
            default_timeout=d.get("default_timeout", 600),
        )


#: The quality tiers ``default_for`` may name — the same three
#: ``GenerateImageInput / GenerateVideoInput / GenerateAudioInput.quality_tier``
#: admits. Duplicated rather than imported from tool_registry, which imports this
#: module; ``test_quality_tier_vocabulary_matches_the_schema`` pins the two in
#: step, since a tier added to the schema but not here would be rejected as a
#: typo in every adapter.yaml that named it.
_QUALITY_TIERS = frozenset({"fast", "balanced", "best"})

#: ``UnderstandVisionInput.analysis_depth`` has a separate vocabulary from media
#: generation's quality tier. Keeping its defaults separate prevents an adapter's
#: generation ``default_for`` declaration from affecting vision routing.
_ANALYSIS_DEPTHS = frozenset({"fast", "balanced", "thorough"})

#: The fleet video-resolution vocabulary, ordered by output size. Read by
#: ``validation_corrections`` to pick the nearest supported tier *at or below* the
#: requested one, so a fallback never silently upgrades a caller into a pricier tier.
#: Every member of ``GenerateVideoInput.resolution`` must appear here — a missing one
#: is not an error but a silently skipped correction, which is why
#: ``test_resolution_order_covers_the_whole_schema_enum`` pins the two together.
_RESOLUTION_ORDER = {"480p": 0, "720p": 1, "768p": 2, "1080p": 3, "2k": 4, "4k": 5}


class ModelAdapter(ABC):
    # Subclasses must declare these as class attributes
    adapter_id: str
    display_name: str
    cfgpu_model_id: str          # Only used in build_payload()
    model_name: str              # Public model identifier — the only one ever exposed to callers
    provider: str                # Which upstream serves this model; see settings.ProviderSettings
    task_type: Literal["image", "video", "audio", "understand"]
    endpoint: str
    is_async: bool
    poll_endpoint: str | None
    #: Never hold a tool call open for this model, whatever the caller passed for
    #: `wait`. For a model whose latency is dominated by an unbounded serial-GPU
    #: queue, blocking is structurally wrong: the wait outlives every MCP client
    #: timeout, so the caller loses the connection *and* the task_id, and can no
    #: longer reach a job that is running fine. See service/video.py.
    force_async: bool
    #: Canonical task IDs (``capabilities/media_tasks.yaml``), in display order. The one
    #: vocabulary for what this model can do: code gates (regions, 组图, layer
    #: decomposition, transparent background), routing preferences and the agent-facing
    #: ``list_model_profiles`` all read it. See ``task_catalog.py``.
    tasks: tuple[str, ...]
    cost_tier: int               # 1-5
    speed_tier: int              # 1-5
    #: Tie-break for ``model="auto"`` (all quality tiers). Without it a tie falls
    #: through to alphabetical ``adapter_id``, which encodes nothing about the
    #: model: eight of the ten image models share speed_tier 3 / cost_tier 2, so
    #: the whole family scored level and the alphabetically first — the *oldest* —
    #: name won every request. Default 0 = "no opinion", the previous behaviour.
    auto_priority: int
    #: Flagship declaration, read only by ``quality_tier="best"``. cost_tier used
    #: to stand in for this ("pricier tends to mean flagship"), which ranks the
    #: *billing tier* rather than the output — a premium-priced service tier of an
    #: older model outranked a newer flagship. The proxy remains for models that
    #: declare no rank. Default 0 = undeclared.
    quality_rank: int
    #: Quality tiers this model is the declared default landing spot for, e.g.
    #: ``["balanced"]``. Empty = undeclared, which is what every model did before
    #: this existed.
    #:
    #: **Distinct from ``auto_priority``, and deliberately stronger.**
    #: ``auto_priority`` breaks ties *within* a score, so it can only ever pick
    #: among models the heuristic already rates equally — it cannot express "we
    #: have decided", only "when the heuristic is silent, prefer this one".
    #: Encoding a decision by inflating ``speed_tier`` / ``cost_tier`` until the
    #: heuristic agrees is the alternative, and it corrupts the inputs: those two
    #: feed *every* tier's score, so a number bent to win one tier silently moves
    #: the others, and the file then states a cost or a latency that is not true.
    #: This field keeps the estimate and the decision in separate places.
    #:
    #: Scoped per quality tier because "which model is the default" genuinely has
    #: different answers for different asks: the model we want for an ordinary
    #: request need not be the one we want when the caller explicitly asks for
    #: speed. It is applied only among candidates ``supports()`` already accepted,
    #: so it is a preference and not a pin — a model that cannot serve the request
    #: is gone before this is read, and the next-best candidate takes over.
    default_for: frozenset[str]
    #: Analysis depths for which this vision model is the declared automatic default.
    #: Only read for ``UnderstandVisionInput``; media tasks use ``default_for`` above.
    analysis_depth_default_for: frozenset[str]
    max_duration_seconds: int    # video only: longest explicit duration accepted
    min_duration_seconds: int    # video only: shortest explicit duration accepted
    default_duration_seconds: int
    #: Whether ``duration_seconds=-1`` ("the model picks the length") is accepted.
    #:
    #: A protocol fact, not a per-deployment setting, so it lives on the class: only the
    #: Seedance request schema has a smart-duration value. Everywhere else ``-1`` is
    #: refused by the one check in ``supports()`` below, whose message names this
    #: model's real range — the caller must write the seconds out, because a length
    #: this server picked would be a billed choice nobody made.
    accepts_smart_duration: bool = False
    resolutions: list[str] | None  # video only: allowed resolution values, None = unrestricted
    #: The tier used when the caller omits ``resolution`` (video only).
    #:
    #: The schema default is ``None`` ("you pick") rather than a concrete tier for the
    #: same reason ``duration_seconds`` is: ``resolution`` is checked by ``supports()``,
    #: so a fleet-wide concrete default is a value every model must offer or be filtered
    #: out of ``model="auto"`` by a request the caller never actually made. MiniMax H3
    #: offers 480p/768p/2k and no 720p at all, which under a ``"720p"`` schema default would
    #: have removed it from every ordinary call — including the plainest one,
    #: ``generate_video(prompt=...)`` — and made naming it explicitly a hard error.
    default_resolution: str
    #: How many marked regions this model accepts on a single image, or None for no
    #: local limit. Declarative because it is per-model while the ``regions`` schema is
    #: per-tool: writing the tightest model's cap into the schema would present one
    #: model's limit as a universal and reject calls that are legal on the model
    #: actually selected.
    max_regions_per_image: int | None
    #: Video only: which material slots this model accepts, their counts, and how it
    #: uses a supplied audio track (``adapters/inputs.py``). ``None`` = undeclared,
    #: which a test forbids for every video model in the fleet.
    inputs: "dict[str, InputSlot] | None"
    #: Video only: what the output carries besides pictures — today only whether it
    #: has sound (``adapters/inputs.py`` AUDIO_OUTPUTS). ``None`` = undeclared.
    outputs: dict[str, str] | None
    #: Maximum number of images that can be requested in one generation call. ``None``
    #: means the shared request-schema limit is the only local restriction.
    max_images_per_request: int | None
    poll_config: PollConfig | None
    extends: str | None          # parent adapter_id, or None
    card_base: str | None        # model dir to inherit card.md from; None = no inheritance

    @classmethod
    def from_config(cls, config: dict) -> "ModelAdapter":
        """Instantiate from merged YAML config. Supports variant models reusing a base class."""
        instance = cls.__new__(cls)
        instance.adapter_id = config["adapter_id"]
        instance.display_name = config.get("display_name", config["adapter_id"])
        instance.cfgpu_model_id = config["cfgpu_model_id"]
        # Falls back to adapter_id for configs/fixtures predating model_name.
        instance.model_name = config.get("model_name", config["adapter_id"])
        # Which upstream API serves this model. "cfgpu" is both the default and
        # what every model declared before providers existed, so an absent key
        # keeps the previous behaviour exactly.
        instance.provider = config.get("provider", "cfgpu")
        instance.task_type = config["task_type"]
        instance.endpoint = config["endpoint"]
        instance.is_async = config.get("is_async", True)
        instance.poll_endpoint = config.get("poll_endpoint")
        # Opt-in per model. Absent = previous behaviour (honour the caller's `wait`).
        instance.force_async = bool(config.get("force_async", False))
        if "capabilities" in config:
            raise ValueError(
                f"{instance.adapter_id}: the capabilities: vocabulary was retired — list "
                f"canonical task IDs under tasks: (task_catalog.py) and material slots under "
                f"inputs: (adapters/inputs.py)"
            )
        instance.tasks = parse_tasks(instance.adapter_id, instance.task_type, config.get("tasks"))
        instance.cost_tier = config.get("cost_tier", 3)
        instance.speed_tier = config.get("speed_tier", 3)
        # Both default to 0 = undeclared, so a model that says nothing routes
        # exactly as it did before these existed. Note they are inherited through
        # ``extends`` like every other field: a variant that must *not* inherit a
        # parent's rank has to zero it out explicitly (see the nano-banana chain).
        instance.auto_priority = config.get("auto_priority", 0)
        instance.quality_rank = config.get("quality_rank", 0)
        # A bare string is tolerated for the same reason disabled_models tolerates
        # one: forgetting YAML list syntax for a single entry is the common slip,
        # and reading it as a one-item list is unambiguous. An unknown tier name is
        # not tolerated — it would silently declare nothing, which looks exactly
        # like the model simply losing on score.
        default_for = config.get("default_for") or []
        if isinstance(default_for, str):
            default_for = [default_for]
        unknown = set(default_for) - _QUALITY_TIERS
        if unknown:
            raise ValueError(
                f"{instance.adapter_id}: default_for has unknown quality tiers "
                f"{sorted(unknown)} (valid: {sorted(_QUALITY_TIERS)})"
            )
        instance.default_for = frozenset(default_for)
        analysis_depth_default_for = config.get("analysis_depth_default_for") or []
        if isinstance(analysis_depth_default_for, str):
            analysis_depth_default_for = [analysis_depth_default_for]
        unknown = set(analysis_depth_default_for) - _ANALYSIS_DEPTHS
        if unknown:
            raise ValueError(
                f"{instance.adapter_id}: analysis_depth_default_for has unknown depths "
                f"{sorted(unknown)} (valid: {sorted(_ANALYSIS_DEPTHS)})"
            )
        instance.analysis_depth_default_for = frozenset(analysis_depth_default_for)
        # GenerateVideoInput's own validator allows the widest range any model in
        # the fleet accepts (2–30; Wan 2.7 allows 2 seconds). Every model can
        # declare its real range here so supports() can reject locally instead
        # of letting the POST fail upstream. 15 was the schema-wide cap before
        # 2.5 arrived, so it stays the default and no existing model changes.
        instance.max_duration_seconds = config.get("max_duration_seconds", 15)
        instance.min_duration_seconds = config.get("min_duration_seconds", 4)
        instance.default_duration_seconds = config.get("default_duration_seconds", 5)
        # Resolution is a per-model value set, not a fleet-wide one: asking a model
        # for a resolution it does not offer fails upstream as "the parameter
        # resolution specified in the request is not valid for model X in i2v".
        # Models that have documented their set list it here; None means "no local
        # restriction", which is what every model did before this existed.
        instance.resolutions = config.get("resolutions")
        # 720p was the schema-wide default before `resolution` became optional, so it
        # stays the default here and no existing model's behaviour changes. A model
        # whose set excludes it (MiniMax H3) must declare its own.
        instance.default_resolution = config.get("default_resolution", "720p")
        instance.max_regions_per_image = config.get("max_regions_per_image")
        legacy = [key for key in LEGACY_INPUT_KEYS if key in config]
        if legacy:
            raise ValueError(
                f"{instance.adapter_id}: {legacy} moved into the inputs: block "
                f"(see adapters/inputs.py)"
            )
        instance.inputs = parse_inputs(instance.adapter_id, config.get("inputs"))
        if instance.inputs is not None and instance.task_type != "video":
            raise ValueError(f"{instance.adapter_id}: inputs: is declared for video models only")
        instance.outputs = parse_outputs(instance.adapter_id, config.get("outputs"))
        if instance.outputs is not None and instance.task_type != "video":
            raise ValueError(f"{instance.adapter_id}: outputs: is declared for video models only")
        instance.max_images_per_request = config.get("max_images_per_request")
        pc = config.get("poll_config")
        instance.poll_config = PollConfig.from_dict(pc) if pc else None
        instance.extends = config.get("extends")
        # "card_base" key absent → inherit from extends; "card_base: ~" → None (no inheritance)
        instance.card_base = (
            config["card_base"] if "card_base" in config else config.get("extends")
        )
        return instance

    @abstractmethod
    def build_payload(
        self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput"
    ) -> dict:
        """Map unified schema → CFGPU API payload. Only place cfgpu_model_id is used."""

    @abstractmethod
    def parse_response(self, resp: dict) -> "NormalizedResult":
        """Map CFGPU response → NormalizedResult. Missing fields set to None."""

    def supports(
        self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput"
    ) -> tuple[bool, str]:
        """Return (ok, reason). Subclasses can override for fine-grained checks."""
        from cfgpu_mcp.adapters.regions import check_regions
        from cfgpu_mcp.tool_registry import (
            GenerateAudioInput,
            GenerateImageInput,
            GenerateVideoInput,
            UnderstandVisionInput,
        )

        expected = {
            GenerateImageInput: "image",
            GenerateVideoInput: "video",
            GenerateAudioInput: "audio",
            UnderstandVisionInput: "understand",
        }
        for cls, tt in expected.items():
            if isinstance(req, cls) and self.task_type != tt:
                return False, f"{self.model_name} is a {self.task_type} model, not a {tt} model"
        # Regions are checked here, for every adapter, rather than in the two adapters
        # that accept them — the gate exists precisely for the models that do *not*, and
        # one left unguarded silently drops the coordinates and bills for the wrong
        # picture.
        ok, reason = check_regions(self, req)
        if not ok:
            return False, reason
        if isinstance(req, GenerateImageInput) and (
            self.max_images_per_request is not None
            and req.n > self.max_images_per_request
        ):
            return False, (
                f"{self.model_name} supports n values from 1 to "
                f"{self.max_images_per_request} (got {req.n})"
            )
        if isinstance(req, GenerateVideoInput):
            if not req.prompt.strip() and not (
                req.first_frame
                or req.last_frame
                or req.reference_images
                or req.reference_videos
                or req.reference_audios
            ):
                return False, "text-to-video requires a non-empty prompt"
            duration_seconds = self.resolve_duration_seconds(req)
            if duration_seconds == -1 and not self.accepts_smart_duration:
                return False, (
                    f"{self.model_name} requires an explicit duration: set duration_seconds "
                    f"to {self.min_duration_seconds}–{self.max_duration_seconds} seconds (-1, a model-chosen duration, "
                    f"is accepted only by the Seedance family)"
                )
            if duration_seconds != -1 and duration_seconds < self.min_duration_seconds:
                return False, (
                    f"{self.model_name} supports explicit durations of "
                    f"{self.min_duration_seconds}–{self.max_duration_seconds} seconds"
                )
            if duration_seconds != -1 and duration_seconds > self.max_duration_seconds:
                # Offer -1 only where it is accepted: suggesting it to every model sent
                # callers from one refusal straight into the next.
                smart = " (or -1 for a model-chosen duration)" if self.accepts_smart_duration else ""
                return False, (
                    f"{self.model_name} supports explicit durations of "
                    f"{self.min_duration_seconds}–{self.max_duration_seconds} seconds{smart}"
                )
            if self.inputs is not None:
                ok, reason = check_declared_inputs(self.model_name, self.inputs, req)
                if not ok:
                    return False, reason
            resolution = self.resolve_resolution(req)
            if self.resolutions is not None and resolution not in self.resolutions:
                return False, (
                    f"{self.model_name} does not support resolution "
                    f"{resolution} (supported: {', '.join(self.resolutions)})"
                )
        return True, ""

    def translate_task_failure(self, error: str) -> tuple[str, str, bool | None] | None:
        """Reclassify an asynchronous task failure the caller can fix.

        ``error`` is the stored reason (upstream code + message, as ``poll`` recorded
        it). Return ``(error_type, user_message, card_hint)`` to replace the default
        ``task_failed`` — which reads as "generation failed" and invites a retry that
        can never succeed when the cause is the request itself — or ``None`` to keep
        it. The upstream wording must stay first in ``user_message`` so a report
        remains joinable with the provider's own logs; the stored row is not rewritten.
        """
        return None

    def validation_corrections(
        self,
        req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput",
    ) -> dict[str, Any]:
        """Safe tool-argument fallbacks used by ``validate_only``.

        Corrections are deliberately limited to ordered output tiers.  Dropping a
        reference, inventing a missing frame, or changing a voice would alter the
        caller's creative intent, so those remain hard validation errors.  A resolution
        fallback is different: adapters already have a concrete supported tier to use,
        and reporting it in ``corrected_args`` makes that previously silent choice
        explicit and reproducible on the billed call.
        """
        from cfgpu_mcp.tool_registry import GenerateVideoInput

        if not isinstance(req, GenerateVideoInput):
            return {}
        # An omitted resolution is already this model's own tier, so there is nothing to
        # correct — and writing the resolved value into corrected_args would turn a
        # delegated choice into a pin the caller never asked for.
        if req.resolution is None:
            return {}
        if self.resolutions is None or req.resolution in self.resolutions:
            return {}

        order = _RESOLUTION_ORDER
        supported = [value for value in self.resolutions if value in order]
        if not supported:
            return {}
        requested_rank = order[req.resolution]
        lower = [value for value in supported if order[value] <= requested_rank]
        fallback = max(lower, key=order.__getitem__) if lower else min(supported, key=order.__getitem__)
        return {"resolution": fallback}

    def resolve_resolution(self, req: "GenerateVideoInput") -> str:
        """Resolve an omitted unified resolution to this model's own default tier."""
        return req.resolution or self.default_resolution

    def resolve_duration_seconds(self, req: "GenerateVideoInput") -> int:
        """Resolve an omitted unified duration to this model's real default."""
        if req.duration_seconds is None:
            return self.default_duration_seconds
        return req.duration_seconds

    def extract_task_id(self, resp: dict) -> str | None:
        """Extract task_id from POST response. Override for non-standard response shapes."""
        return resp.get("id") or resp.get("task_id")

    def extract_status(self, resp: dict) -> str:
        """Extract status string from poll response. Override for non-standard response shapes."""
        return resp.get("status", "running")

    def extract_eta(self, resp: dict) -> dict[str, Any] | None:
        """Upstream's own ETA for a freshly submitted task, or None if it reports none.

        Override where the upstream returns one. Most do not, and inventing a number
        here would be worse than saying nothing: the caller would pace its polling
        against a guess.
        """
        return None

    def estimate_poll_timeout(
        self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput | UnderstandVisionInput"
    ) -> int:
        """Estimate polling timeout in seconds. Only meaningful for async models."""
        if not self.is_async:
            return 0
        if self.poll_config:
            return self.poll_config.default_timeout
        return 300

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.adapter_id!r}, model={self.cfgpu_model_id!r})"
