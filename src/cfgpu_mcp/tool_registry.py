from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from mcp.types import CallToolResult, TextContent
from pydantic import AfterValidator, BaseModel, Field, field_validator


# ── Media (material) slot annotations ───────────────────────────────────────
#
# Eight parameters across four tools are *material slots*: they carry a reference to a
# media file rather than a scalar value. Which parameters those are — and what may be
# put in them — used to be knowable only by reading English prose ("List of public
# image URLs to use as reference"). That is not enough for a host that maintains its
# own reference layer (e.g. DeerFlow/cf-dream, where the model hands the agent a short
# material id like `m3` and the agent resolves it to a freshly-signed URL at the moment
# the tool fires). Such a host needs two things this schema could not previously give it:
#
#   1. **Which parameters to re-describe.** If the schema says "public image URL" while
#      the host's own ledger says "material id", the model mixes the two formats — and
#      the parameter-level description, being nearer the decision, tends to win.
#   2. **Which parameters to resolve on the way out.** Without a slot list, a host has to
#      inspect every string in the argument tree and guess by shape, which misfires on
#      ordinary values (`16/9`, `black-forest-labs/FLUX.1-dev`, a prompt that happens to
#      contain a path) and can never be closed.
#
# So each media slot carries a machine-readable ``x-cfgpu-media`` annotation next to its
# description, and the description prose is *generated* from that annotation
# (``_compose_media_description``) so the two cannot drift.
#
# The annotation describes the **wire contract only**: cfgpu is a remote service and can
# fetch only what it can reach, which is what ``accepts`` states. It deliberately says
# nothing about host dialects — a host that speaks one replaces the generated wire clause
# with its own, reusing ``slot`` (carried in the annotation for exactly that purpose) as
# the semantic half that is true for everyone.

MEDIA_ANNOTATION_KEY = "x-cfgpu-media"

# Closed vocabulary for ``accepts``. Base64 data URIs are *not* advertised even where an
# upstream model tolerates them: inlining megabytes of media into a tool call is never the
# right call for an LLM to make, and hosts that need it can still pass it.
_WIRE_FORMS: dict[str, str] = {
    "https_url": "a publicly reachable https:// URL",
    "asset_url": "an `asset://<ASSET_ID>` handle (Seedance / WAN video models only)",
}


def _compose_media_description(slot: str, accepts: list[str], arity: str) -> str:
    """Compose a media slot's description = semantic half + generated wire clause.

    The wire clause is derived from ``accepts`` rather than hand-written, so adding a
    wire form to a slot updates its prose automatically and no slot can advertise a
    format its annotation does not declare.
    """
    forms = " or ".join(_WIRE_FORMS[a] for a in accepts)
    subject = "Each entry must be" if arity == "many" else "Must be"
    return f"{slot} {subject} {forms}."


def media_field(
    *,
    slot: str,
    role: Literal["image", "video", "audio"],
    arity: Literal["one", "many"],
    accepts: list[str],
) -> Any:
    """Declare a material slot: a Field carrying ``x-cfgpu-media`` + generated prose.

    ``slot`` is the semantic half of the description — what this input *is* — and is
    stamped into the annotation as well so a host can rebuild the description around its
    own reference format without string surgery on the composed text.

    **The annotation carries permissive facts only** — what *may* appear in this slot —
    never restrictive ones. ``role`` / ``arity`` / ``accepts`` are true for the tool: a
    slot's role never varies, arity is its Python type, and ``accepts`` is a union over
    the models, so at worst a caller sends a form some model rejects, exactly as today.
    Caps and mutual exclusions are the opposite shape: they are per-model (this schema is
    per-tool), so a host that reads them as enforceable would reject calls that are legal
    on the model actually being used. There is deliberately no ``max_items`` / ``excludes``
    / ``requires`` key; such limits live in the prose as model-relative guidance, with
    ``get_model_card`` / ``list_models`` as the authority and cfgpu's own validation as
    the enforcement point.

    Every media slot is optional and defaults to ``None`` (kept here rather than at each
    call site so the eight declarations stay comparable; the schema-consistency test pins
    this against the FastMCP wrapper signatures).
    """
    unknown = [a for a in accepts if a not in _WIRE_FORMS]
    if unknown:
        raise ValueError(f"unknown media wire form(s) {unknown}; known: {sorted(_WIRE_FORMS)}")
    spec: dict[str, Any] = {"role": role, "arity": arity, "accepts": list(accepts), "slot": slot}
    return Field(
        default=None,
        description=_compose_media_description(slot, accepts, arity),
        json_schema_extra={MEDIA_ANNOTATION_KEY: spec},
    )


# ── Caption (artifact label) ────────────────────────────────────────────────
#
# Like ``request_id``, ``caption`` is a caller-supplied handle this server stores and
# echoes but never interprets — it is not part of any upstream request. It exists for
# hosts that keep their own asset ledger (DeerFlow/cf-dream registers every generated
# artifact as a *material* the user later refers to by a short id): without a label
# supplied at call time, the ledger entry is nameless and the host has to spend a second
# tool round trip naming it after the fact. Riding the generate call closes that gap, and
# riding the stored payload means the async ``generate → task_wait`` hop carries it for
# free — the host needs no state of its own to survive the two-phase case.
#
# Over-long captions are truncated, never rejected: a caption has no effect on the
# generated media, so failing the call — and costing the caller a whole turn — over the
# length of a cosmetic label would be a bad trade.

CAPTION_MAX_CHARS = 200


def _truncate_caption(v: str | None) -> str | None:
    return v[:CAPTION_MAX_CHARS] if v else v


CaptionStr = Annotated[Optional[str], AfterValidator(_truncate_caption)]


def caption_field() -> Any:
    """Declare the artifact-label slot (kept in one place so the three copies can't drift)."""
    return Field(
        default=None,
        description="Optional short human-readable label for the artifact this call "
        "produces, e.g. '角色阿雅 第一版' or 'cover image v1'. Echoed back verbatim "
        "alongside the result — including on the eventual artifact returned by a later "
        "task_status/task_wait call — so a caller that keeps its own asset ledger can "
        "file this artifact under a name a human recognises, without a second call to "
        "name it afterwards. It is a label, not a second prompt: name the subject and "
        "the version, and do not restate the generation prompt. Never sent to the model "
        f"API and has no effect on the generated media. Truncated at {CAPTION_MAX_CHARS} "
        "characters; omitted from the response when not supplied.",
    )


# ── Input Models (single source of truth for all tool schemas) ─────────────

class GenerateImageInput(BaseModel):
    """Generate image from text prompt using CFGPU models."""

    prompt: str = Field(description="Text description of the image to generate")
    model: str | list[str] = Field(
        default="auto",
        description="A single model_id from list_models (e.g. 'doubao-seedream-5-0-lite'), "
        "a list of model_ids to restrict automatic selection to those candidates "
        "(e.g. ['doubao-seedream-5-0-lite', 'cf-pro']), or 'auto' to choose from all models",
    )
    aspect_ratio: Literal["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"] = Field(default="1:1")
    resolution: Literal["1K", "2K", "3K", "4K"] = Field(default="2K")
    reference_images: Optional[list[str]] = media_field(
        slot="Reference images that guide the generation.",
        role="image",
        arity="many",
        accepts=["https_url"],
    )
    n: int = Field(
        default=1,
        description="Number of images to generate as a related group (组图 / sequential image "
        "generation). 1–15. Only doubao-seedream-* models support n>1; other image models "
        "generate a single image and reject n>1. Exception: doubao-seedream-5-0-pro is a "
        "single-image model (no 组图 support) and rejects n>1.",
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
    return_metadata: bool = Field(default=True, description="Include seed, model_used, usage in response")
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters passed directly to API, e.g. {'tools': [{'type': 'web_search'}]}. "
        "Merged last, so it overrides typed fields like watermark.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Optional caller-supplied correlation id, echoed back verbatim on "
        "the immediate response and on the eventual artifact/error returned by a later "
        "task_status/task_wait call. Use it to link an async result (whose tool_call id "
        "differs from this request's) back to this request. Unlike task_id it is known "
        "at call time and also works for synchronous models (which have no task_id). "
        "Omitted from the response when not supplied.",
    )
    caption: CaptionStr = caption_field()


class GenerateVideoInput(BaseModel):
    """Generate video from text prompt, image, or multimodal references using CFGPU models."""

    prompt: str = Field(description="Text description of the video to generate")
    model: str | list[str] = Field(
        default="auto",
        description="A single model_id from list_models (e.g. 'wan-video'), "
        "a list of model_ids to restrict automatic selection to those candidates "
        "(e.g. ['wan-video', 'wan-video-fast']), or 'auto' to choose from all models",
    )
    # All five slots accept `asset://<ASSET_ID>`: the Seedance / WAN request schema takes a
    # 素材 ID on `image_url.url`, `video_url.url` and `audio_url.url` alike (see
    # models/wan-2-0/card.md — only the image row spells the `asset://` scheme out, the
    # other two say 素材 ID). Media inputs are the whole reason the asset library exists,
    # so this is the tool where the handle is at home.
    first_frame: Optional[str] = media_field(
        slot="The image to use as the video's first frame (image-to-video).",
        role="image",
        arity="one",
        accepts=["https_url", "asset_url"],
    )
    last_frame: Optional[str] = media_field(
        slot="The image to use as the video's last frame; use together with first_frame.",
        role="image",
        arity="one",
        accepts=["https_url", "asset_url"],
    )
    reference_images: Optional[list[str]] = media_field(
        slot="Reference images that guide the generation (role=reference_image). Typically "
        "up to 9 (up to 30 on Doubao Seedance 2.5), and on most models mutually exclusive "
        "with first_frame / last_frame — call get_model_card for the chosen model's actual "
        "limits.",
        role="image",
        arity="many",
        accepts=["https_url", "asset_url"],
    )
    reference_videos: Optional[list[str]] = media_field(
        slot="Reference videos that guide the generation (role=reference_video). Support and "
        "limits vary by model (the Seedance 2.0 family takes up to 3, Seedance 2.5 up to 10) "
        "— call get_model_card for the chosen model.",
        role="video",
        arity="many",
        accepts=["https_url", "asset_url"],
    )
    reference_audios: Optional[list[str]] = media_field(
        slot="Reference audio that guides the generation (role=reference_audio). Support, "
        "limits, and whether audio may be supplied without an accompanying image or video "
        "vary by model — call get_model_card for the chosen model.",
        role="audio",
        arity="many",
        accepts=["https_url", "asset_url"],
    )
    duration_seconds: int = Field(
        default=5,
        description="Video duration in seconds: 4–30 (Doubao Seedance 2.5), 4–15 (WAN 2.0, "
        "WAN 2.0 Fast, the Doubao Seedance 2.0 family, HappyHorse) or 4–12 (Doubao Seedance "
        "1.5 Pro). Use -1 for a model-chosen 'smart' duration (supported by WAN 2.0 and the "
        "Doubao Seedance family).",
    )

    @field_validator("duration_seconds")
    @classmethod
    def _validate_duration(cls, v: int) -> int:
        # 30 is the fleet-wide maximum (Doubao Seedance 2.5); narrower per-model
        # ceilings are enforced by each adapter's supports(), so model="auto" can
        # route around them instead of hard-failing here.
        if v != -1 and not (4 <= v <= 30):
            raise ValueError(
                f"duration_seconds={v} is out of range. Use 4–30 seconds, or -1 for a "
                f"model-chosen 'smart' duration (WAN 2.0 / Doubao Seedance). Note only "
                f"Doubao Seedance 2.5 goes beyond 15 seconds; WAN 2.0 and Seedance 2.0 cap "
                f"at 15, and Doubao Seedance 1.5 Pro at 12."
            )
        return v
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"] = Field(
        default="adaptive",
        description="'adaptive' automatically matches input image ratio",
    )
    resolution: Literal["480p", "720p", "1080p"] = Field(
        default="720p",
        description="Video resolution. 1080p is supported by WAN 2.0, Doubao Seedance 2.0, "
        "Doubao Seedance 1.5 Pro, and HappyHorse (HappyHorse's own default is 1080p). "
        "Doubao Seedance 2.5 / 2.0 fast / 2.0 mini top out at 720p (480p/720p only). "
        "WAN 2.0 Fast does NOT support 1080p for text-to-video (only 480p/720p; 1080p works "
        "only with an image/video input). HappyHorse does not support 480p (minimum 720p).",
    )
    with_audio: bool = Field(default=True, description="Generate audio synchronized with video")
    quality_tier: Literal["fast", "balanced", "best"] = Field(default="balanced")
    watermark: Optional[bool] = Field(
        default=None,
        description="Add a watermark. None keeps each model's own default. Supported by all video models.",
    )
    wait: bool = Field(default=True, description="Wait for task completion before returning")
    timeout: Optional[int] = Field(default=None, description="Max wait seconds, None=auto estimate")
    return_metadata: bool = Field(default=True, description="Include seed, model_used, usage in response")
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters, e.g. {'tools': [{'type': 'web_search'}]}. "
        "Merged last, so it overrides typed fields like watermark.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Optional caller-supplied correlation id, echoed back verbatim on "
        "the immediate response and on the eventual artifact/error returned by a later "
        "task_status/task_wait call. Use it to link an async result (whose tool_call id "
        "differs from this request's) back to this request. Unlike task_id it is known "
        "at call time and also works for synchronous models (which have no task_id). "
        "Omitted from the response when not supplied.",
    )
    caption: CaptionStr = caption_field()


class GenerateAudioInput(BaseModel):
    """Generate speech audio from text (text-to-speech) using CFGPU voice models."""

    text: str = Field(description="Text to synthesize into speech")
    model: str | list[str] = Field(
        default="auto",
        description="A single model_id from list_models (e.g. 'seed-tts-2.0'), "
        "a list of model_ids to restrict automatic selection to those candidates "
        "(e.g. ['MiniMax/speech-2.8-hd', 'MiniMax/speech-2.8-turbo']), or 'auto' to choose from all voice models",
    )
    voice: Optional[str] = Field(
        default=None,
        description="Voice/speaker id. seed-tts-2-0 uses speaker ids like "
        "'zh_female_xiaohe_uranus_bigtts'; MiniMax uses voice ids like 'male-qn-qingse'. "
        "None falls back to each model's own default.",
    )
    audio_format: Literal["mp3", "wav", "pcm", "flac"] = Field(
        default="mp3", description="Output audio container/format"
    )
    sample_rate: Optional[int] = Field(
        default=None,
        description="Output sample rate in Hz (e.g. 24000, 32000). None uses the model's default.",
    )
    bitrate: Optional[int] = Field(
        default=None,
        description="Output bitrate in bps (MiniMax only, e.g. 128000). None uses the model's default.",
    )
    speed: float = Field(default=1.0, description="Speech speed multiplier (MiniMax only)")
    volume: float = Field(default=1.0, description="Speech volume multiplier (MiniMax only)")
    pitch: int = Field(default=0, description="Speech pitch offset (MiniMax only)")
    emotion: Optional[str] = Field(
        default=None,
        description="Emotion hint such as 'happy', 'sad', 'angry' (MiniMax only). "
        "None lets the model infer emotion from the text.",
    )
    quality_tier: Literal["fast", "balanced", "best"] = Field(default="balanced")
    wait: bool = Field(default=True, description="Wait for task completion before returning")
    timeout: Optional[int] = Field(default=None, description="Max wait seconds, None=auto estimate")
    return_metadata: bool = Field(default=True, description="Include model_used, usage in response")
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters passed directly to API. Merged last, so it "
        "overrides typed fields.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Optional caller-supplied correlation id, echoed back verbatim on "
        "the immediate response and on the eventual artifact/error returned by a later "
        "task_status/task_wait call. Use it to link an async result (whose tool_call id "
        "differs from this request's) back to this request. Unlike task_id it is known "
        "at call time and also works for synchronous models (which have no task_id). "
        "Omitted from the response when not supplied.",
    )
    caption: CaptionStr = caption_field()


class UnderstandVisionInput(BaseModel):
    """Understand and reason over images and video using CFGPU vision-language models.

    Covers image understanding, image reasoning, and video understanding: the model
    is given a natural-language instruction (``prompt``) plus optional image and/or
    video URLs, and returns a text answer (not a media file). Synchronous — the
    result is returned directly, there is no task_id to poll.
    """

    prompt: str = Field(
        description="Instruction or question about the supplied media, e.g. "
        "'描述这张图片' or '列出视频中关键事件的时间线'. Required even when media is "
        "attached; for pure text chat, supply just the prompt with no images/video.",
    )
    model: str | list[str] = Field(
        default="auto",
        description="A single model_id from list_models (e.g. 'qwen3.6-plus'), "
        "a list of model_ids to restrict automatic selection to those candidates, "
        "or 'auto' to choose from all vision-understanding models. Prefer 'auto' "
        "unless a specific model is required — an unknown id falls back to auto.",
    )
    images: Optional[list[str]] = media_field(
        slot="Images to analyze (image understanding / reasoning). Multiple images are "
        "compared/reasoned over jointly. Images ONLY — a video must NOT go here; route it "
        "through the `video` parameter, since a video placed in `images` is misclassified "
        "as a still frame.",
        role="image",
        arity="many",
        accepts=["https_url"],
    )
    video: Optional[str] = media_field(
        slot="A single video to understand (long video supported). Use this for ANY video "
        "input — never put a video into `images`. Can be combined with images and text in "
        "one request.",
        role="video",
        arity="one",
        accepts=["https_url"],
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System message steering the model's behaviour. "
        "None uses a generic 'You are a helpful assistant.' default.",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum number of output tokens. None uses the model's default.",
    )
    temperature: Optional[float] = Field(
        default=None,
        description="Sampling temperature. None uses the model's default.",
    )
    return_metadata: bool = Field(
        default=True,
        description="Include token usage in the response. The answer (message.content) "
        "and the model's reasoning (message.reasoning_content, for Thinking models) are "
        "always returned regardless.",
    )
    model_specific: Optional[dict] = Field(
        default=None,
        description="Model-specific parameters passed directly to the chat/completions API "
        "(e.g. {'top_p': 0.8}). Merged last, so it overrides typed fields.",
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

    task_type: Optional[Literal["image", "video", "audio", "understand"]] = Field(
        default=None,
        description="Filter by task type, None returns all models",
    )


class GetModelCardInput(BaseModel):
    """Get detailed model information, parameters, and usage examples."""

    model_name: str = Field(description="A model_id from list_models")


# ── NormalizedResult ────────────────────────────────────────────────────────

@dataclass
class NormalizedResult:
    urls: list[str]
    expires_at: datetime | None        # URL 过期时间，通常 24h 后
    task_id: str | None                # 同步模型为 None
    model_used: str | None             # 实际使用的 model_name（对外公开标识）
    seed: int | None                   # 部分模型返回
    usage: dict[str, Any] | None       # 原始 API 返回的 usage 对象（计费结构因 API 而异）
    aspect_ratio: str | None = None    # 回传请求的 aspect_ratio（由 TaskManager 填充）
    # ── Vision-understanding (text-returning) fields ──
    # message: chat-completion assistant message, e.g.
    #   {"role": "assistant", "content": "...", "reasoning_content": "..."}.
    # Its presence is what marks a result as text-returning rather than media.
    response_id: str | None = None     # chat/completions 响应 id（chatcmpl-...）
    message: dict[str, Any] | None = None
    # ── Inline media (no downloadable URL) ──
    # Some providers return generated media inline instead of a URL (e.g. MiniMax
    # speech hands back a hex audio blob). Each descriptor carries base64 `data` +
    # `mime_type` (+ optional `filename`). This rides structuredContent (the split
    # pops it out of the LLM-facing content — see generate_audio's structured_keys),
    # so the raw blob never enters the model context; the consumer materialises it
    # into its own OSS object_key.
    inline_media: list[dict[str, Any]] | None = None

    def to_dict(self, return_metadata: bool = False) -> dict[str, Any]:
        # Vision-understanding results carry a chat message rather than media urls —
        # emit the chat-completion-shaped envelope {id, model, message[, usage]}.
        if self.message is not None:
            out: dict[str, Any] = {
                "id": self.response_id,
                "model": self.model_used,
                "message": self.message,
            }
            if return_metadata:
                out["usage"] = self.usage
            return out

        base: dict[str, Any] = {
            "urls": self.urls,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
        if self.inline_media:  # actual artifact payload — always surfaced, like urls
            base["inline_media"] = self.inline_media
        if return_metadata:
            base.update({
                "task_id": self.task_id,
                "model_used": self.model_used,
                "aspect_ratio": self.aspect_ratio,
                "seed": self.seed,
                "usage": self.usage,
            })
        return base


# ── Artifact flagging ────────────────────────────────────────────────────────

_ARTIFACT_DONE_STATUS = "Success. URLs already generated; no further task_status/task_wait polling needed."


def annotate_artifact(result: Any) -> Any:
    """Stamp ``"artifact": True`` on a tool result that carries generated media.

    A result is considered to hold an artifact when it contains a non-empty
    ``urls`` list — either at the top level (generate_* results) or nested under
    ``result`` (task_status / task_wait results) — or a non-empty ``inline_media``
    list (media returned inline without a URL, e.g. MiniMax speech). Error dicts and
    pending/no-wait results (which have neither) are left untouched.

    A terminal ``status`` hint is also stamped alongside the flag so the LLM can
    tell, from the content it actually sees, that generation already finished — the
    MaterialsMiddleware rewrites ``urls`` out of the content, and without this the
    model would otherwise keep polling task_status/task_wait on an already-done task.
    """
    if not isinstance(result, dict):
        return result
    if result.get("urls") or result.get("inline_media"):
        result["artifact"] = True
        result["status"] = _ARTIFACT_DONE_STATUS
    else:
        nested = result.get("result")
        if isinstance(nested, dict) and nested.get("urls"):
            result["artifact"] = True
            result["status"] = _ARTIFACT_DONE_STATUS
    return result


# ── Caller-supplied echo fields (service-layer data contract) ───────────────

def stamp_echo(result: Any, *, request_id: str | None = None, caption: str | None = None) -> Any:
    """Echo the caller's own handles onto a result dict (in place), if set.

    Both fields are supplied on a ``generate_*`` call, stored, and handed back
    untouched; neither is part of any upstream request:

    - ``request_id`` is a correlation handle. It lets the caller join a result back to
      the originating request across the async ``generate → task_wait/task_status`` hop
      (where the tool_call ids differ) and for synchronous models that have no
      ``task_id``.
    - ``caption`` is a human-readable label for the artifact, for callers that keep
      their own asset ledger and would otherwise have to name the entry in a second
      call.

    Stamped by the **service layer** — not the MCP wrapper — so all three call modes
    (MCP, agent dispatcher, CLI) echo them. Omitted when falsy so callers that supply
    neither see unchanged result shapes; ``setdefault`` never clobbers a value already
    present. Non-dict results (e.g. a ``CallToolResult``) pass through untouched.

    The two diverge on **failures**: ``CFGPUError`` carries ``request_id`` (joining a
    failure back to its request is exactly what a correlation handle is for) but not
    ``caption`` — a failed call produced no artifact, so there is nothing to label.
    """
    if not isinstance(result, dict):
        return result
    if request_id:
        result.setdefault("request_id", request_id)
    if caption:
        result.setdefault("caption", caption)
    return result


# ── Structured-content split (MCP tool layer only) ──────────────────────────

def reshape_vision_result(result: Any) -> Any:
    """Flatten a vision result's nested ``message`` for the MCP content shape.

    The vision service returns ``message`` as the chat ``{role, content,
    reasoning_content}`` object. For the MCP tool the answer text is hoisted to a
    top-level ``message`` field (next to ``model``) and the chain-of-thought is split
    out as a sibling ``reasoning_content`` so it can be routed to structuredContent.
    Error dicts and non-dict results pass through unchanged.
    """
    if not isinstance(result, dict) or result.get("error"):
        return result
    message = result.get("message")
    if isinstance(message, dict):
        result["message"] = message.get("content")
        result["reasoning_content"] = message.get("reasoning_content")
    return result


def split_structured(result: Any, *, structured_keys: tuple[str, ...]) -> Any:
    """Split a success result into lean LLM-facing content + client-facing structuredContent.

    ``langchain-mcp-adapters`` maps an MCP ``CallToolResult``'s text ``content`` to
    ``ToolMessage.content`` (which enters the LLM context) and its ``structuredContent``
    to ``ToolMessage.artifact`` (a side channel never shown to the LLM). This helper
    pops ``structured_keys`` (data the client renders but the model does not need —
    e.g. ``usage`` / ``payload`` / ``reasoning_content``) out of the content dict and
    into ``structuredContent``, keeping the LLM-facing payload small and avoiding the
    downstream truncation that previously collapsed the whole result into an opaque
    truncated string.

    ``-> dict`` tool annotations produce no ``outputSchema``, so FastMCP and the
    lowlevel server pass a returned ``CallToolResult`` through verbatim (no output
    validation). Non-dict results and error dicts (``error`` truthy) pass through
    unchanged so the model still sees the full failure reason.
    """
    if not isinstance(result, dict) or result.get("error"):
        return result
    structured = {k: result.pop(k) for k in structured_keys if k in result}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))],
        structuredContent=structured or None,
        isError=False,
    )


# ── Tool Registry ────────────────────────────────────────────────────────────

_REGISTRY: list[tuple[str, type[BaseModel]]] = [
    ("generate_image",  GenerateImageInput),
    ("generate_video",  GenerateVideoInput),
    ("generate_audio",  GenerateAudioInput),
    ("understand_vision", UnderstandVisionInput),
    ("task_status",     TaskStatusInput),
    ("task_wait",       TaskWaitInput),
    ("list_models",     ListModelsInput),
    ("get_model_card",  GetModelCardInput),
]

_TOOL_TASK_TYPE: dict[str, str] = {
    "generate_image": "image",
    "generate_video": "video",
    "generate_audio": "audio",
    "understand_vision": "understand",
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


def get_media_slots(tool_name: str) -> dict[str, dict]:
    """Return ``{param_name: x-cfgpu-media spec}`` for a tool's material slots.

    The single source of truth for "which parameters of this tool carry media
    references". Hosts use it to re-describe those parameters in their own reference
    format and to locate exactly the values they must resolve before the call — instead
    of guessing by inspecting every string argument. Tools with no media slots return
    ``{}``. Specs are deep-copied so a consumer cannot mutate the class-level annotation.
    """
    for name, model in _REGISTRY:
        if name == tool_name:
            return {
                fname: copy.deepcopy(field.json_schema_extra[MEDIA_ANNOTATION_KEY])
                for fname, field in model.model_fields.items()
                if isinstance(field.json_schema_extra, dict)
                and MEDIA_ANNOTATION_KEY in field.json_schema_extra
            }
    return {}


def apply_media_annotations(schema: dict, tool_name: str) -> dict:
    """Stamp each material slot's ``x-cfgpu-media`` annotation onto a built schema (in place).

    Needed only where the schema is built from a bare function signature — i.e. the
    FastMCP wrappers (Mode A), same reason ``get_field_descriptions`` has to be injected
    there. Every other exposure (Anthropic / OpenAI / LangGraph) builds from
    ``model_json_schema()``, which already carries ``json_schema_extra``, so those paths
    must not call this. Existing annotations are never overwritten. Returns ``schema``
    for chaining; a tool without media slots is a no-op.
    """
    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema
    for fname, spec in get_media_slots(tool_name).items():
        prop = props.get(fname)
        if isinstance(prop, dict):
            prop.setdefault(MEDIA_ANNOTATION_KEY, spec)
    return schema


def _stamp_model_enum(prop: dict, model_ids: list[str]) -> None:
    """Constrain a ``model`` JSON-schema property to ``model_ids`` (in place).

    The field type is ``str | list[str]`` (default ``"auto"``), so Pydantic / FastMCP
    emit ``anyOf: [{type: string}, {type: array, items: {type: string}}]``. We stamp the
    string branch's ``enum`` with ``["auto", *model_ids]`` and the array branch's
    ``items.enum`` with just ``model_ids`` ("auto" is not a valid list element — the list
    form restricts auto-selection to named candidates). Falls back to a flat ``enum`` if
    the schema is ever a bare string.
    """
    branches = prop.get("anyOf")
    if isinstance(branches, list):
        for branch in branches:
            if branch.get("type") == "string":
                branch["enum"] = ["auto", *model_ids]
            elif branch.get("type") == "array":
                items = branch.get("items")
                if isinstance(items, dict):
                    items["enum"] = list(model_ids)
    elif prop.get("type") == "string":
        prop["enum"] = ["auto", *model_ids]


def apply_model_enum(schema: dict, tool_name: str) -> dict:
    """Pin a tool's ``model`` parameter to the registry's real model ids (in place).

    Shared by every LLM-facing schema exposure — MCP/FastMCP (Mode A) and the Anthropic /
    OpenAI / LangGraph builders (Mode B) — so no client can hallucinate a non-existent
    model_id (e.g. ``qwen-3-vl-plus``). Only the public ``model_name`` is advertised; the
    internal ``adapter_id`` / ``cfgpu_model_id`` are never exposed, though ``registry.get()``
    still resolves any of them, so existing callers keep working. The model list is
    registry-driven, hence the enum can only be stamped at schema-build time, not declared
    statically on the Pydantic model. Best-effort: a registry load failure leaves the schema
    unconstrained (validation + the understand-vision auto-fallback remain the backstops).
    Returns ``schema`` for chaining. Non-model tools (task_status, list_models, ...) are a
    no-op.
    """
    task_type = _TOOL_TASK_TYPE.get(tool_name)
    if task_type is None:
        return schema
    prop = schema.get("properties", {}).get("model")
    if not isinstance(prop, dict):
        return schema
    try:
        from cfgpu_mcp.config import get_registry

        model_ids = sorted({a.model_name for a in get_registry().list_all(task_type=task_type)})
    except Exception:  # pragma: no cover - defensive: never block schema build on config
        return schema
    if model_ids:
        _stamp_model_enum(prop, model_ids)
    return schema


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
        schema = apply_model_enum(model.model_json_schema(), name)
        result.append({
            "name": name,
            "description": model.__doc__,
            "input_schema": schema,
        })
    return result
