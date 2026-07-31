from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

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
    task_type: Literal["image", "video", "audio", "understand"]
    endpoint: str
    is_async: bool
    poll_endpoint: str | None
    capabilities: set[str]
    cost_tier: int               # 1-5
    speed_tier: int              # 1-5
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
        instance.task_type = config["task_type"]
        instance.endpoint = config["endpoint"]
        instance.is_async = config.get("is_async", True)
        instance.poll_endpoint = config.get("poll_endpoint")
        instance.capabilities = set(config.get("capabilities", []))
        instance.cost_tier = config.get("cost_tier", 3)
        instance.speed_tier = config.get("speed_tier", 3)
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
        return True, ""

    def extract_task_id(self, resp: dict) -> str | None:
        """Extract task_id from POST response. Override for non-standard response shapes."""
        return resp.get("id") or resp.get("task_id")

    def extract_status(self, resp: dict) -> str:
        """Extract status string from poll response. Override for non-standard response shapes."""
        return resp.get("status", "running")

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
