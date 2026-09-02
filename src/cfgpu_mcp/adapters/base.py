from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

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


def models_with_capability(capability: str) -> list[str]:
    """Public ``model_name``s carrying ``capability``, or ``[]`` if the registry is unreachable.

    For refusal messages that need to name the alternatives. Read from the registry rather
    than hard-coded so the advice cannot go stale as the fleet changes — and never allowed
    to raise, because building a nicer error is not a reason to lose the real one.
    """
    try:
        from cfgpu_mcp.config import get_registry

        return sorted(a.model_name for a in get_registry().list_all() if capability in a.capabilities)
    except Exception:  # pragma: no cover - defensive: advice must never break validation
        return []


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
    capabilities: set[str]
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
    max_duration_seconds: int    # video only: longest explicit duration accepted
    default_duration_seconds: int
    resolutions: list[str] | None  # video only: allowed resolution values, None = unrestricted
    #: How many marked regions this model accepts on a single image, or None for no
    #: local limit. Declarative because it is per-model while the ``regions`` schema is
    #: per-tool: writing the tightest model's cap into the schema would present one
    #: model's limit as a universal and reject calls that are legal on the model
    #: actually selected.
    max_regions_per_image: int | None
    max_reference_images: int | None  # video only: per-model reference material limits
    max_reference_videos: int | None
    max_reference_audios: int | None
    allow_audio_only_reference: bool
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
        instance.capabilities = set(config.get("capabilities", []))
        instance.cost_tier = config.get("cost_tier", 3)
        instance.speed_tier = config.get("speed_tier", 3)
        # Both default to 0 = undeclared, so a model that says nothing routes
        # exactly as it did before these existed. Note they are inherited through
        # ``extends`` like every other field: a variant that must *not* inherit a
        # parent's rank has to zero it out explicitly (see the nano-banana chain).
        instance.auto_priority = config.get("auto_priority", 0)
        instance.quality_rank = config.get("quality_rank", 0)
        # GenerateVideoInput's own validator allows the widest range any model in
        # the fleet accepts (4–30, for Doubao Seedance 2.5). Every narrower model
        # declares its real ceiling here so supports() can reject locally instead
        # of letting the POST fail upstream. 15 was the schema-wide cap before
        # 2.5 arrived, so it stays the default and no existing model changes.
        instance.max_duration_seconds = config.get("max_duration_seconds", 15)
        instance.default_duration_seconds = config.get("default_duration_seconds", 5)
        # Resolution is a per-model value set, not a fleet-wide one: asking a model
        # for a resolution it does not offer fails upstream as "the parameter
        # resolution specified in the request is not valid for model X in i2v".
        # Models that have documented their set list it here; None means "no local
        # restriction", which is what every model did before this existed.
        instance.resolutions = config.get("resolutions")
        instance.max_regions_per_image = config.get("max_regions_per_image")
        instance.max_reference_images = config.get("max_reference_images")
        instance.max_reference_videos = config.get("max_reference_videos")
        instance.max_reference_audios = config.get("max_reference_audios")
        instance.allow_audio_only_reference = bool(
            config.get("allow_audio_only_reference", False)
        )
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
                return False, f"{self.adapter_id} is a {self.task_type} model, not a {tt} model"
        # Regions are checked here, for every adapter, rather than in the two adapters
        # that accept them — the gate exists precisely for the models that do *not*, and
        # one left unguarded silently drops the coordinates and bills for the wrong
        # picture.
        ok, reason = check_regions(self, req)
        if not ok:
            return False, reason
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
            if duration_seconds != -1 and duration_seconds > self.max_duration_seconds:
                return False, (
                    f"{self.adapter_id} supports explicit durations of "
                    f"4–{self.max_duration_seconds} seconds "
                    f"(or -1 for a model-chosen smart duration)"
                )
            if self.resolutions is not None and req.resolution not in self.resolutions:
                return False, (
                    f"{self.adapter_id} does not support resolution "
                    f"{req.resolution} (supported: {', '.join(self.resolutions)})"
                )
        return True, ""

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
        if self.resolutions is None or req.resolution in self.resolutions:
            return {}

        order = {"480p": 0, "720p": 1, "1080p": 2, "4k": 3}
        supported = [value for value in self.resolutions if value in order]
        if not supported:
            return {}
        requested_rank = order[req.resolution]
        lower = [value for value in supported if order[value] <= requested_rank]
        fallback = max(lower, key=order.__getitem__) if lower else min(supported, key=order.__getitem__)
        return {"resolution": fallback}

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
