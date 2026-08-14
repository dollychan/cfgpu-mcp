from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateAudioInput, NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

# Candidate dotted paths where the generated audio URL may live in a CFGPU voice
# response. The voice API isn't fully documented for its response shape, and the
# two providers (Doubao seed-tts, MiniMax) differ, so we probe several known and
# plausible locations. We only accept http(s) URLs — this also rejects MiniMax's
# alternative hex-encoded `data.audio` blob, which is not a downloadable URL.
_AUDIO_URL_PATHS = (
    "content.audioUrl",
    "content.audio_url",
    "data.audioUrl",
    "data.audio_url",
    "data.url",
    "data.audio",
    "audioUrl",
    "audio_url",
    "url",
    "output.audio_url",
)


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path through nested dicts/lists; return None if any hop misses."""
    for key in path.split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(key)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _extract_audio_url(resp: dict) -> str | None:
    for path in _AUDIO_URL_PATHS:
        val = _dig(resp, path)
        if isinstance(val, str) and val.startswith(("http://", "https://")):
            return val
    return None


# MiniMax speech returns the audio inline (is_async: false, no URL) as a hex string at
# ``output.data.audio`` with the container format at ``output.extra_info.audio_format``.
# Map that format to a MIME type for the inline_media descriptor.
_AUDIO_MIME_BY_FORMAT = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


def _extract_inline_audio(resp: dict) -> dict | None:
    """Decode MiniMax's inline hex audio blob into an ``inline_media`` descriptor.

    The hex at ``output.data.audio`` decodes directly to the container bytes (MPEG
    Layer III frames are already a playable ``.mp3``), which we re-encode as base64 for
    the LLM-hidden structuredContent side channel. Returns ``None`` when the blob is
    absent/unusable so the caller falls back to (empty) URL handling.
    """
    audio_hex = _dig(resp, "output.data.audio")
    if not isinstance(audio_hex, str) or not audio_hex.strip():
        return None
    try:
        raw = bytes.fromhex(audio_hex.strip())
    except ValueError:
        return None
    if not raw:
        return None
    fmt = _dig(resp, "output.extra_info.audio_format")
    fmt = fmt.lower() if isinstance(fmt, str) and fmt else "mp3"
    return {
        "data": base64.b64encode(raw).decode("ascii"),
        "mime_type": _AUDIO_MIME_BY_FORMAT.get(fmt, "application/octet-stream"),
        "filename": f"speech.{fmt}",
    }


@register_python_adapter
class SeedTTSAdapter(ModelAdapter):
    """Python Adapter for Doubao seed-tts-2.0 (asynchronous text-to-speech).

    Payload uses the ``req_params`` envelope with a ``speaker`` voice id and a
    nested ``audio_params`` block. Submit returns a task id; the result URL is
    fetched by polling ``/voice/tasks/{task_id}``.
    """

    adapter_id = "seed-tts-2-0"

    _DEFAULT_VOICE = "zh_female_xiaohe_uranus_bigtts"
    _DEFAULT_SAMPLE_RATE = 24000

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput") -> dict:
        assert isinstance(req, GenerateAudioInput)
        req_params: dict = {
            "text": req.text,
            "speaker": req.voice or self._DEFAULT_VOICE,
            "audio_params": {
                "format": req.audio_format,
                "sample_rate": req.sample_rate or self._DEFAULT_SAMPLE_RATE,
            },
            "callback_url": "",
        }
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "req_params": req_params,
        }
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def extract_task_id(self, resp: dict) -> str | None:
        # Create response nests the id under `data`:
        #   {"code":..., "data":{"task_status":1, "task_id":"..."}, "message":"ok"}
        data = resp.get("data") or {}
        return data.get("task_id") or data.get("taskId")

    def extract_status(self, resp: dict) -> str:
        # Poll response:
        #   {"data":{"taskStatus":2, "audioUrl":...}, "success":true,
        #    "failure":false, "running":false}
        # The top-level booleans are the authoritative signal; `taskStatus` is an
        # integer (1=processing, 2=success) that _STATUS_MAP can't map directly.
        if resp.get("success") is True:
            return "succeeded"
        if resp.get("failure") is True:
            return "failed"
        return "running"

    def parse_response(self, resp: dict) -> NormalizedResult:
        data = resp.get("data") or {}
        url = _extract_audio_url(resp)
        # Prefer the upstream url expiry (epoch seconds) when present.
        expire = data.get("urlExpireTime")
        expires_at = (
            datetime.fromtimestamp(expire, UTC)
            if isinstance(expire, (int, float))
            else _default_expires_at()
        )
        return NormalizedResult(
            urls=[url] if url else [],
            expires_at=expires_at,
            task_id=data.get("taskId") or data.get("task_id") or resp.get("id"),
            model_used=resp.get("model"),
            seed=None,
            usage=resp.get("usage"),
        )


@register_python_adapter
class MiniMaxSpeechAdapter(ModelAdapter):
    """Python Adapter for the MiniMax speech family (synchronous text-to-speech).

    Payload uses the ``input`` envelope with ``voice_setting`` / ``audio_setting``
    blocks. The result is returned directly in the POST response (is_async: false).
    Registered under ``minimax-speech-2-8-hd``; the turbo variant reuses this class
    via the registry extends-chain.
    """

    adapter_id = "minimax-speech-2-8-hd"

    _DEFAULT_VOICE = "male-qn-qingse"
    _DEFAULT_SAMPLE_RATE = 32000
    _DEFAULT_BITRATE = 128000

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput") -> dict:
        assert isinstance(req, GenerateAudioInput)
        voice_setting: dict = {
            "voice_id": req.voice or self._DEFAULT_VOICE,
            "speed": req.speed,
            "vol": req.volume,
            "pitch": req.pitch,
        }
        if req.emotion:
            voice_setting["emotion"] = req.emotion
        inp: dict = {
            "text": req.text,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": req.sample_rate or self._DEFAULT_SAMPLE_RATE,
                "bitrate": req.bitrate or self._DEFAULT_BITRATE,
                "format": req.audio_format,
                "channel": 1,
            },
        }
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "input": inp,
        }
        if req.model_specific:
            payload.update(req.model_specific)
        return payload

    def parse_response(self, resp: dict) -> NormalizedResult:
        # MiniMax reports business failures inside an HTTP-200 response. Treating that
        # envelope as a successful synchronous result would otherwise produce the very
        # misleading shape ``urls: []`` with neither inline media nor an error.
        base_resp = _dig(resp, "output.base_resp")
        if isinstance(base_resp, dict):
            status_code = base_resp.get("status_code")
            if status_code not in (None, 0, "0"):
                status_msg = str(base_resp.get("status_msg") or "unknown MiniMax error")
                # 2054 is a caller-fixable voice selection error. Other MiniMax
                # business failures are generation failures unless/until their codes
                # have a more precise stable classification.
                error_type = (
                    "invalid_params" if str(status_code) == "2054" else "task_failed"
                )
                raise CFGPUError(
                    error_type=error_type,
                    user_message=(
                        f"MiniMax 语音生成失败（status_code={status_code}）：{status_msg}"
                    ),
                    original={"status_code": status_code, "status_msg": status_msg},
                    retryable=False,
                )

        url = _extract_audio_url(resp)
        # Prefer a real URL when present; otherwise capture the inline hex blob so the
        # consumer can materialise it (decode → its own OSS object_key). Keeping the
        # blob in structuredContent (via generate_audio's structured_keys) means it
        # never enters the LLM context.
        inline = None if url else _extract_inline_audio(resp)
        return NormalizedResult(
            urls=[url] if url else [],
            inline_media=[inline] if inline else None,
            expires_at=_default_expires_at(),
            task_id=None,          # Synchronous model has no task_id
            model_used=resp.get("model"),
            seed=None,
            usage=resp.get("usage"),
        )
