from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── Input Models (single source of truth for all tool schemas) ─────────────

class GenerateImageInput(BaseModel):
    """Generate image from text prompt using CFGPU models."""

    prompt: str = Field(description="Text description of the image to generate")
    model: str | list[str] = Field(
        default="auto",
        description="A single adapter_id/cfgpu_model_id (e.g. 'doubao-seedream-5-0-lite'), "
        "a list of ids to restrict automatic selection to those candidates "
        "(e.g. ['doubao-seedream-5-0-lite', 'seedream']), or 'auto' to choose from all models",
    )
    aspect_ratio: Literal["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"] = Field(default="1:1")
    resolution: Literal["1K", "2K", "3K", "4K"] = Field(default="2K")
    reference_images: Optional[list[str]] = Field(
        default=None,
        description="List of public image URLs to use as reference",
    )
    n: int = Field(
        default=1,
        description="Number of images to generate as a related group (组图 / sequential image "
        "generation). 1–15. Only doubao-seedream-* models support n>1; other image models "
        "generate a single image and reject n>1.",
    )

    @field_validator("n")
    @classmethod
    def _validate_n(cls, v: int) -> int:
        if not (1 <= v <= 15):
            raise ValueError(
                f"n={v} is out of range. Image group size must be between 1 and 15."
            )
        return v
    quality_tier: Literal["fast", "balanced", "best"] = Field(default="balanced")
    watermark: Optional[bool] = Field(
        default=None,
        description="Add an 'AI generated' watermark. None keeps each model's own default. "
        "Not supported by gpt-image-2 / nano-banana models (ignored there).",
    )
    wait: bool = Field(default=True, description="Wait for task completion before returning")
    timeout: Optional[int] = Field(default=None, description="Max wait seconds, None=auto estimate")
    return_metadata: bool = Field(default=True, description="Include seed, model_used, cost_tokens in response")
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters passed directly to API, e.g. {'tools': [{'type': 'web_search'}]}. "
        "Merged last, so it overrides typed fields like watermark.",
    )


class GenerateVideoInput(BaseModel):
    """Generate video from text prompt, image, or multimodal references using CFGPU models."""

    prompt: str = Field(description="Text description of the video to generate")
    model: str | list[str] = Field(
        default="auto",
        description="A single adapter_id/cfgpu_model_id (e.g. 'wan-2-0'), "
        "a list of ids to restrict automatic selection to those candidates "
        "(e.g. ['wan-2-0', 'wan-2-0-fast']), or 'auto' to choose from all models",
    )
    first_frame: Optional[str] = Field(default=None, description="First frame image URL (public)")
    last_frame: Optional[str] = Field(default=None, description="Last frame image URL (public), use with first_frame")
    reference_images: Optional[list[str]] = Field(
        default=None,
        description="Reference image URLs (role=reference_image), max 9, mutually exclusive with first/last_frame",
    )
    reference_videos: Optional[list[str]] = Field(
        default=None,
        description="Reference video URLs (role=reference_video), max 3",
    )
    reference_audios: Optional[list[str]] = Field(
        default=None,
        description="Reference audio URLs (role=reference_audio), max 3",
    )
    duration_seconds: int = Field(
        default=5,
        description="Video duration in seconds: 4–15 (WAN 2.0, WAN 2.0 Fast, HappyHorse) or "
        "4–12 (Doubao Seedance 1.5 Pro). Use -1 for a model-chosen 'smart' duration "
        "(supported by WAN 2.0 and Doubao Seedance 1.5 Pro).",
    )

    @field_validator("duration_seconds")
    @classmethod
    def _validate_duration(cls, v: int) -> int:
        if v != -1 and not (4 <= v <= 15):
            raise ValueError(
                f"duration_seconds={v} is out of range. Use 4–15 seconds, or -1 for a "
                f"model-chosen 'smart' duration (WAN 2.0 / Doubao Seedance 1.5 Pro). "
                f"Note Doubao Seedance 1.5 Pro caps explicit durations at 12 seconds."
            )
        return v
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"] = Field(
        default="adaptive",
        description="'adaptive' automatically matches input image ratio",
    )
    resolution: Literal["480p", "720p", "1080p"] = Field(
        default="720p",
        description="Video resolution. 1080p is supported by all current video models "
        "(WAN 2.0, WAN 2.0 Fast, Doubao Seedance 1.5 Pro, HappyHorse — HappyHorse's own "
        "default is 1080p). HappyHorse does not support 480p (minimum 720p).",
    )
    with_audio: bool = Field(default=True, description="Generate audio synchronized with video")
    quality_tier: Literal["fast", "balanced", "best"] = Field(default="balanced")
    watermark: Optional[bool] = Field(
        default=None,
        description="Add a watermark. None keeps each model's own default. Supported by all video models.",
    )
    wait: bool = Field(default=True, description="Wait for task completion before returning")
    timeout: Optional[int] = Field(default=None, description="Max wait seconds, None=auto estimate")
    return_metadata: bool = Field(default=True, description="Include seed, model_used, cost_tokens in response")
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters, e.g. {'tools': [{'type': 'web_search'}]}. "
        "Merged last, so it overrides typed fields like watermark.",
    )


class TaskStatusInput(BaseModel):
    """Query the status of an async generation task."""

    task_id: str = Field(description="Task ID returned by generate_image or generate_video")


class TaskWaitInput(BaseModel):
    """Wait for an async generation task to complete and return the result."""

    task_id: str = Field(description="Task ID to wait for")
    timeout: Optional[int] = Field(default=None, description="Max wait seconds, None=auto")


class ListModelsInput(BaseModel):
    """List available CFGPU models with their capabilities and identifiers."""

    task_type: Optional[Literal["image", "video"]] = Field(
        default=None,
        description="Filter by task type, None returns all models",
    )


class GetModelCardInput(BaseModel):
    """Get detailed model information, parameters, and usage examples."""

    model_name: str = Field(description="Model adapter_id or cfgpu_model_id")


# ── NormalizedResult ────────────────────────────────────────────────────────

@dataclass
class NormalizedResult:
    urls: list[str]
    expires_at: datetime | None        # URL 过期时间，通常 24h 后
    task_id: str | None                # 同步模型为 None
    model_used: str | None             # 实际 cfgpu_model_id
    seed: int | None                   # 部分模型返回
    cost_tokens: int | None            # 部分模型返回

    def to_dict(self, return_metadata: bool = False) -> dict[str, Any]:
        base: dict[str, Any] = {
            "urls": self.urls,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
        if return_metadata:
            base.update({
                "task_id": self.task_id,
                "model_used": self.model_used,
                "seed": self.seed,
                "cost_tokens": self.cost_tokens,
            })
        return base


# ── Tool Registry ────────────────────────────────────────────────────────────

_REGISTRY: list[tuple[str, type[BaseModel]]] = [
    ("generate_image",  GenerateImageInput),
    ("generate_video",  GenerateVideoInput),
    ("task_status",     TaskStatusInput),
    ("task_wait",       TaskWaitInput),
    ("list_models",     ListModelsInput),
    ("get_model_card",  GetModelCardInput),
]

_TOOL_TASK_TYPE: dict[str, str] = {
    "generate_image": "image",
    "generate_video": "video",
}


def get_field_descriptions(tool_name: str) -> dict[str, str]:
    """Return {param_name: description} for a tool, sourced from its Pydantic model.

    Single source of truth for per-parameter docs. Consumers that build schemas from
    bare function signatures (e.g. the FastMCP wrappers, which carry no param docs) can
    inject these instead of duplicating the text.
    """
    for name, model in _REGISTRY:
        if name == tool_name:
            return {
                fname: field.description
                for fname, field in model.model_fields.items()
                if field.description
            }
    return {}


def get_anthropic_tools(
    task_types: list[str] | None = None,
    tools: list[str] | None = None,
) -> list[dict]:
    """Return tool schemas in Anthropic API format.

    Args:
        task_types: ["image"], ["video"], or None for all
        tools: explicit allowlist of tool names, or None for all
    """
    result = []
    for name, model in _REGISTRY:
        if tools is not None and name not in tools:
            continue
        tool_type = _TOOL_TASK_TYPE.get(name)
        if task_types is not None and tool_type and tool_type not in task_types:
            continue
        schema = model.model_json_schema()
        result.append({
            "name": name,
            "description": model.__doc__,
            "input_schema": schema,
        })
    return result
