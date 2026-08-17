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


# ── MiniMax business-error translation ───────────────────────────────────────
#
# MiniMax reports parameter rejections inside an HTTP-200 body, and its
# ``status_msg`` names the offending field and stops there:
#
#     invalid params, invalid params: voice_setting emotion
#     voice id not exist
#
# That says *what* broke and nothing about *what to do*, so the caller — human or
# model — retries with another guess. The two codes seen in production need
# opposite advice, which is why this is a table and not one generic sentence:
#
# * **2054 (voice)** has an authoritative answer to copy: the card's
#   ``系统音色列表``. The remedy points there, plus the two mistakes that produce
#   most of these — reusing a seed-tts speaker on MiniMax, and "normalising" an id
#   that legitimately contains odd bytes (trailing space, full-width bracket).
# * **2013 (emotion)** has no authoritative answer anywhere. The card documents
#   the field but never enumerates its values, so pointing at the card sends the
#   caller after something that isn't written down — and reads as if a correct
#   value were discoverable, which invites exactly the retry loop we're trying to
#   stop. The remedy is to drop the field (auto-inference is the designed default)
#   or carry the emotion in the text via the card-documented inline markers, which
#   cannot fail this way. Hence ``card_hint=False`` on that branch.
#
# Codes outside this table keep their existing generic classification: guessing at
# an unknown code's meaning would be worse than the status quo.

_MINIMAX_VOICE_REMEDY = (
    "该 voice 不在此模型的音色表中。两个语音模型族的音色互不通用："
    "形如 xxx_uranus_bigtts 或 saturn_xxx 的是 seed-tts 的 speaker，MiniMax 一律不接受。"
    "音色 id 必须逐字节照抄（部分 id 含尾随空格、全角括号或不规则大小写，"
    "自行规范化就会变成不存在的 id）。"
    "不需要特定音色时省略 voice 即可，默认为 male-qn-qingse。"
)

_MINIMAX_EMOTION_REMEDY = (
    "emotion 取值被拒。该字段没有公开的取值枚举（模型卡只举例、不列举全集），"
    "因此无处可查，也不要再换一个值重试："
    "省略 emotion 让模型按文本自动推断语气（推荐），"
    "或把情绪写进 text 的内嵌标记（如「今天真开心(laughs)」）——后者不会触发本错误。"
    "确需显式指定时，只用模型卡示范过的 happy / sad / angry。"
)

# status_msg 里出现的字段名 → 该字段的专用建议；先按字段匹配，命中即用。
# 第三项是「是否保留 get_model_card 提示」——见上方对 emotion 的说明。
_MINIMAX_FIELD_REMEDIES: tuple[tuple[str, str, bool], ...] = (
    ("emotion", _MINIMAX_EMOTION_REMEDY, False),
    ("voice", _MINIMAX_VOICE_REMEDY, True),
)

# 字段名匹配不到时按 status_code 兜底。2013 未点名字段时不给泛化建议——
# 编一句「请检查参数」既没有信息量，又会挤掉更有用的 card 提示。
_MINIMAX_CODE_REMEDIES: dict[str, tuple[str, bool]] = {
    "2013": ("", True),
    "2054": (_MINIMAX_VOICE_REMEDY, True),
}

# 调用者可自行修正的 code —— 归为 invalid_params 而不是 task_failed。后者读作
# 「生成失败」，会让上层当成可重试的生成故障，而这类错误重试多少次都一样。
_MINIMAX_CALLER_FIXABLE_CODES = frozenset({"2013", "2054"})


def _minimax_remedy(status_code: str, status_msg: str) -> tuple[str, bool]:
    """Return ``(remedy, keep_card_hint)`` for a MiniMax business failure."""
    lowered = status_msg.lower()
    for keyword, remedy, keep_hint in _MINIMAX_FIELD_REMEDIES:
        if keyword in lowered:
            return remedy, keep_hint
    return _MINIMAX_CODE_REMEDIES.get(status_code, ("", True))


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
                code = str(status_code)
                # Caller-fixable codes are invalid_params; anything else stays a
                # generation failure until its code has a stable classification.
                error_type = (
                    "invalid_params"
                    if code in _MINIMAX_CALLER_FIXABLE_CODES
                    else "task_failed"
                )
                remedy, keep_card_hint = _minimax_remedy(code, status_msg)
                # The upstream wording is always quoted first — it is what appears in
                # MiniMax's own logs, so keeping it verbatim is what makes a report
                # joinable with theirs. The remedy is appended, never a replacement.
                message = f"MiniMax 语音生成失败（status_code={status_code}）：{status_msg}"
                if remedy:
                    message = f"{message}。{remedy}"
                raise CFGPUError(
                    error_type=error_type,
                    user_message=message,
                    original={"status_code": status_code, "status_msg": status_msg},
                    retryable=False,
                    card_hint=None if keep_card_hint else False,
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
