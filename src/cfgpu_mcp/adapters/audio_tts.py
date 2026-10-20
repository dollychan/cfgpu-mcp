from __future__ import annotations

import base64
import difflib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cfgpu_mcp.adapters.base import ModelAdapter, _default_expires_at, register_python_adapter
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import GenerateAudioInput, NormalizedResult
from cfgpu_mcp.voice_catalog import catalog_for_adapter

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
    # Not audio/L16: that type is big-endian (RFC 2586) and MiniMax's pcm is
    # little-endian. Raw PCM has no header, so the descriptor also carries the
    # sample layout (see _extract_inline_audio).
    "pcm": "audio/pcm",
    # MiniMax names Ogg/Opus ``opus``; the unified schema calls the same container
    # ``ogg_opus`` (seed-tts's spelling). Both may come back in extra_info.
    "opus": "audio/ogg",
    "ogg_opus": "audio/ogg",
}


def _system_voice_ids(adapter_id: str, default_voice: str) -> frozenset[str]:
    """The voice ids this model accepts: its ``voices.yaml`` plus its own default.

    The catalog is the one ``list_voice_profiles`` serves, so a voice that tool offers is
    always one this validation accepts. The default is added because the adapter already
    sends it whenever ``voice`` is omitted — rejecting it when named explicitly would make
    the same request fail or succeed depending on spelling — and the catalog export need
    not list it (MiniMax's ``male-qn-qingse`` is absent). Ids are opaque and kept
    byte-for-byte: a trailing space, full-width punctuation, and casing can all matter.
    """
    catalog = catalog_for_adapter(adapter_id)
    if catalog is None:
        raise RuntimeError(f"no voices.yaml for {adapter_id}")
    return frozenset({entry.voice for entry in catalog.voices} | {default_voice})


_SEED_DEFAULT_VOICE = "zh_female_xiaohe_uranus_bigtts"
_MINIMAX_DEFAULT_VOICE = "male-qn-qingse"
_SEED_SYSTEM_VOICES = _system_voice_ids("seed-tts-2-0", _SEED_DEFAULT_VOICE)
_MINIMAX_SYSTEM_VOICES = _system_voice_ids("minimax-speech-2-8-hd", _MINIMAX_DEFAULT_VOICE)
# speech-2.8 hd/turbo accept eight of the nine schema emotions — every one except
# ``whisper``, which is 2.6-only. ``fluent`` is supported (confirmed 2026-10-10; the
# reference's sentence grouping fluent with whisper as 2.6-only is outdated).
_MINIMAX_EMOTIONS = frozenset(
    {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent"}
)


def _voice_correction(voice: str | None, default_voice: str) -> dict[str, str]:
    """The ``voice`` a preflight reports in ``corrected_args``.

    An omitted voice is pinned to the default the adapter will actually send. Leaving
    ``voice`` out delegates the choice, exactly like ``model="auto"``: the voice is still
    concrete — it is in ``payload`` — but ``payload`` rides structuredContent, so an
    approval card built from the caller's own arguments has no voice row, and a person
    approves speech in a voice nobody chose (MiniMax's default is not even in the
    catalog ``list_voice_profiles`` serves). Pinning names it on the card, and merging
    ``corrected_args`` makes the billed call use the voice that was approved.

    Unlike an omitted video ``resolution`` — a quality tier, left unpinned — a voice is
    the thing the listener hears, so the delegated choice is worth surfacing. A supplied
    voice is only ever stripped of surrounding whitespace, never replaced.
    """
    if voice is None:
        return {"voice": default_voice}
    if voice != voice.strip():
        return {"voice": voice.strip()}
    return {}


def _invalid_voice_reason(model_name: str, voice: str, voices: frozenset[str]) -> str:
    nearby = difflib.get_close_matches(voice, voices, n=3, cutoff=0.65)
    suggestion = f" Closest system voices: {', '.join(repr(v) for v in nearby)}." if nearby else ""
    return (
        f"voice {voice!r} is not in {model_name}'s system voice catalog."
        f"{suggestion} Omit voice to use the model default."
    )


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
    media = {
        "data": base64.b64encode(raw).decode("ascii"),
        "mime_type": _AUDIO_MIME_BY_FORMAT.get(fmt, "application/octet-stream"),
        "filename": f"speech.{fmt}",
    }
    if fmt == "pcm":
        # Headerless samples: without the layout the bytes cannot be played. Observed
        # from the relay (2026-10-10): 16-bit little-endian, audio_size equal to
        # rate x channels x 2 x seconds. Rate and channels are read from extra_info
        # rather than from the request, so they describe what was actually returned.
        media["sample_format"] = "s16le"
        rate = _dig(resp, "output.extra_info.audio_sample_rate")
        channels = _dig(resp, "output.extra_info.audio_channel")
        if isinstance(rate, int):
            media["sample_rate"] = rate
        if isinstance(channels, int):
            media["channels"] = channels
    return media


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
# * **2013 (emotion)** has a fixed eight-value enum on 2.8 (no ``whisper``). The remedy lists it and also
#   preserves the automatic-inference option for callers that do not need explicit
#   control. The model card carries the same authoritative list.
#
# Codes outside this table keep their existing generic classification: guessing at
# an unknown code's meaning would be worse than the status quo.

_MINIMAX_VOICE_REMEDY = (
    "该 voice 不在此模型的音色表中。两个语音模型族的音色互不通用："
    "含 uranus 的（形如 xxx_uranus_bigtts、ICL_uranus_xxx_tob）是 seed-tts 的 speaker，MiniMax 一律不接受。"
    "音色 id 的首尾空格、全角括号、不规则大小写等字符都须准确照抄，不得自行规范化。"
    "不需要特定音色时省略 voice 即可，默认为 male-qn-qingse。"
    "需要选择音色时，先按语言或风格关键词查询当前 model_id 的系统音色目录，"
    "只能逐字使用目录返回的 voice 字段；label 只是展示名称，不能传入。"
)

_MINIMAX_EMOTION_REMEDY = (
    "speech-2.8 的 emotion 仅支持 happy / sad / angry / fearful / disgusted / surprised / "
    "calm / fluent，不支持 whisper。也可以省略 emotion，让模型按文本自动推断语气，"
    "或在 text 中插入语气词标签（如「今天真开心(laughs)」）。"
)

# status_msg 里出现的字段名 → 该字段的专用建议；先按字段匹配，命中即用。
# 第三项仅控制通用 card hint；agent-facing recovery must never need a card.
_MINIMAX_FIELD_REMEDIES: tuple[tuple[str, str, bool], ...] = (
    ("emotion", _MINIMAX_EMOTION_REMEDY, False),
    ("voice", _MINIMAX_VOICE_REMEDY, False),
)

# 字段名匹配不到时按 status_code 兜底。2013 未点名字段时不给泛化建议——
# 编一句「请检查参数」既没有信息量，又会挤掉更有用的 card 提示。
_MINIMAX_CODE_REMEDIES: dict[str, tuple[str, bool]] = {
    "2013": ("", False),
    "2054": (_MINIMAX_VOICE_REMEDY, False),
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


# ── seed-tts 2.0 audio parameters (models/seed-tts-2-0/reference) ───────────────
#
# Output containers are mp3 / pcm / ogg_opus; wav and flac do not exist upstream.
# ogg_opus is fixed at 48 kHz, so its default sample rate is not the 24 kHz the other
# two use — sending 24000 with ogg_opus is a rejected request, not a fallback.
_SEED_FORMATS = frozenset({"mp3", "pcm", "ogg_opus"})
_SEED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
_SEED_OGG_SAMPLE_RATE = 48000
# pcm takes no bit_rate at all. Upstream widens the set to 16000/32000 only behind
# additions.disable_default_bit_rate, which stays a model_specific matter.
_SEED_BIT_RATES = (64000, 160000)
_SEED_MAX_TEXT_CHARS = 100_000
# speech_rate / loudness_rate are ints on [-50, 100] where 100 = 2.0x and -50 = 0.5x,
# i.e. linear in the multiplier: rate = (multiplier - 1) * 100.
_SEED_MULTIPLIER_RANGE = (0.5, 2.0)
_SEED_PITCH_RANGE = (-12, 12)
# The download link lives one hour (the audio itself seven days; re-polling the task
# returns a fresh link). Used only when the poll response carries no urlExpireTime.
_SEED_URL_TTL = timedelta(hours=1)


def _nearest(value: int, allowed: tuple[int, ...]) -> int:
    return min(allowed, key=lambda candidate: (abs(candidate - value), candidate))


def _multiplier_to_rate(multiplier: float) -> int:
    return round((multiplier - 1) * 100)


@register_python_adapter
class SeedTTSAdapter(ModelAdapter):
    """Python Adapter for Doubao seed-tts-2.0 (asynchronous text-to-speech).

    Payload uses the ``req_params`` envelope with a ``speaker`` voice id, a nested
    ``audio_params`` block, and ``additions`` — a JSON-encoded *string*, not an object.
    Submit returns a task id; the result URL is fetched by polling
    ``/voice/tasks/{task_id}``.
    """

    adapter_id = "seed-tts-2-0"

    _DEFAULT_VOICE = _SEED_DEFAULT_VOICE
    _DEFAULT_SAMPLE_RATE = 24000

    def validation_corrections(self, req: "GenerateAudioInput") -> dict[str, Any]:
        # Output-shape fallbacks only. speed / volume / pitch out of range stay hard
        # errors: they are delivery, not container, and clamping them changes intent.
        corrected: dict[str, Any] = {}
        fmt = req.audio_format
        if fmt not in _SEED_FORMATS:
            fmt = "mp3"
            corrected["audio_format"] = fmt
        if req.sample_rate is not None:
            rate = (
                _SEED_OGG_SAMPLE_RATE
                if fmt == "ogg_opus"
                else _nearest(req.sample_rate, _SEED_SAMPLE_RATES)
            )
            if rate != req.sample_rate:
                corrected["sample_rate"] = rate
        if req.bitrate is not None:
            bitrate = None if fmt == "pcm" else _nearest(req.bitrate, _SEED_BIT_RATES)
            if bitrate != req.bitrate:
                corrected["bitrate"] = bitrate
        corrected.update(_voice_correction(req.voice, self._DEFAULT_VOICE))
        if req.emotion is not None:
            corrected["emotion"] = None
        return corrected

    def supports(self, req: "GenerateAudioInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        if not req.text.strip():
            return False, f"{self.model_name} requires non-empty text"
        if len(req.text) > _SEED_MAX_TEXT_CHARS:
            return False, (
                f"{self.model_name} accepts at most {_SEED_MAX_TEXT_CHARS} characters of "
                f"text (got {len(req.text)}); split it into several requests"
            )
        if req.voice is not None and req.voice not in _SEED_SYSTEM_VOICES:
            return False, _invalid_voice_reason(self.model_name, req.voice, _SEED_SYSTEM_VOICES)
        if req.audio_format not in _SEED_FORMATS:
            return False, f"{self.model_name} supports audio_format mp3, pcm, or ogg_opus"
        if req.sample_rate is not None:
            if req.audio_format == "ogg_opus" and req.sample_rate != _SEED_OGG_SAMPLE_RATE:
                return False, f"{self.model_name} supports only sample_rate 48000 for ogg_opus"
            if req.sample_rate not in _SEED_SAMPLE_RATES:
                return False, (
                    f"{self.model_name} supports sample_rate "
                    f"{', '.join(str(r) for r in _SEED_SAMPLE_RATES)}"
                )
        if req.bitrate is not None:
            if req.audio_format == "pcm":
                return False, f"{self.model_name} does not support bitrate for pcm; omit bitrate"
            if req.bitrate not in _SEED_BIT_RATES:
                return False, f"{self.model_name} supports bitrate 64000 or 160000"
        low, high = _SEED_MULTIPLIER_RANGE
        if not low <= req.speed <= high:
            return False, f"{self.model_name} supports speed from {low} to {high}"
        if not low <= req.volume <= high:
            return False, f"{self.model_name} supports volume from {low} to {high}"
        low_pitch, high_pitch = _SEED_PITCH_RANGE
        if not low_pitch <= req.pitch <= high_pitch:
            return False, f"{self.model_name} supports pitch from {low_pitch} to {high_pitch}"
        if req.emotion is not None:
            return False, (
                f"{self.model_name} does not support emotion; omit it, or describe the "
                f"delivery in the text"
            )
        return True, ""

    def build_payload(self, req: "GenerateImageInput | GenerateVideoInput | GenerateAudioInput") -> dict:
        assert isinstance(req, GenerateAudioInput)
        default_rate = (
            _SEED_OGG_SAMPLE_RATE if req.audio_format == "ogg_opus" else self._DEFAULT_SAMPLE_RATE
        )
        audio_params: dict = {
            "format": req.audio_format,
            "sample_rate": req.sample_rate or default_rate,
        }
        # Neutral values are omitted rather than sent as 0: the payload for a plain
        # request stays exactly what it was before these fields were mapped.
        if req.bitrate is not None:
            audio_params["bit_rate"] = req.bitrate
        if req.speed != 1.0:
            audio_params["speech_rate"] = _multiplier_to_rate(req.speed)
        if req.volume != 1.0:
            audio_params["loudness_rate"] = _multiplier_to_rate(req.volume)
        additions: dict = {}
        if req.pitch != 0:
            additions["post_process"] = {"pitch": req.pitch}
        req_params: dict = {
            "text": req.text,
            "speaker": req.voice or self._DEFAULT_VOICE,
            "audio_params": audio_params,
            "callback_url": "",
        }
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "req_params": req_params,
        }
        if req.model_specific:
            # Shaped like the payload and merged level by level, so a caller can set one
            # field (e.g. {"req_params": {"additions": {"explicit_language": "en"}}})
            # without restating — and wiping — text and speaker. A plain top-level
            # update replaced the whole req_params.
            for key, value in req.model_specific.items():
                if key == "req_params" and isinstance(value, dict):
                    self._merge_req_params(req_params, audio_params, additions, value)
                else:
                    payload[key] = value
        if additions:
            req_params["additions"] = json.dumps(additions, ensure_ascii=False)
        return payload

    @staticmethod
    def _merge_req_params(req_params: dict, audio_params: dict, additions: dict, override: dict) -> None:
        for key, value in override.items():
            if key == "audio_params" and isinstance(value, dict):
                audio_params.update(value)
            elif key == "additions":
                # Upstream wants a JSON string; accept either spelling from the caller.
                if isinstance(value, str):
                    try:
                        value = json.loads(value) if value.strip() else {}
                    except ValueError as exc:
                        raise CFGPUError(
                            error_type="invalid_params",
                            user_message="model_specific req_params.additions is not valid JSON",
                            original={"additions": value},
                        ) from exc
                if not isinstance(value, dict):
                    raise CFGPUError(
                        error_type="invalid_params",
                        user_message="model_specific req_params.additions must be an object",
                        original={"additions": value},
                    )
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict) and isinstance(additions.get(sub_key), dict):
                        additions[sub_key].update(sub_value)
                    else:
                        additions[sub_key] = sub_value
            else:
                req_params[key] = value

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
        synthesize_text_length = data.get("synthesizeTextLength")
        if synthesize_text_length is None:
            synthesize_text_length = data.get("reqTextLength")
        usage = (
            {"characters": synthesize_text_length}
            if synthesize_text_length is not None
            else None
        )
        # Prefer the upstream url expiry (epoch seconds) when present.
        expire = data.get("urlExpireTime")
        expires_at = (
            datetime.fromtimestamp(expire, UTC)
            if isinstance(expire, (int, float))
            else datetime.now(UTC) + _SEED_URL_TTL
        )
        return NormalizedResult(
            urls=[url] if url else [],
            expires_at=expires_at,
            task_id=data.get("taskId") or data.get("task_id") or resp.get("id"),
            model_used=resp.get("model"),
            seed=None,
            usage=usage,
        )


# ── MiniMax speech-2.8 audio parameters (models/minimax-speech-2-8-hd/reference) ──
#
# The reference is MiniMax's own T2A API; CFGPU relays it under an ``input`` envelope
# with the sync API's field names (``audio_setting.sample_rate``, not the async API's
# ``audio_sample_rate``). Values below are the reference's.
#
# Unified schema format -> MiniMax format. pcmu_raw / pcmu_wav (G.711) have no unified
# spelling and stay reachable through model_specific.
_MINIMAX_FORMATS = {"mp3": "mp3", "wav": "wav", "flac": "flac", "pcm": "pcm", "ogg_opus": "opus"}
_MINIMAX_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100)
# Opus: the reference lists 8000/12000/16000/24000/48000, but the relay checks
# sample_rate against the general set first (observed 2026-10-10: opus + 48000 ->
# "invalid params: sample_rate"), and opus + 32000 passes that check only to fail
# upstream with an opaque 400 that still reported usage. Only the intersection
# can pass both.
_MINIMAX_OPUS_SAMPLE_RATES = (8000, 16000, 24000)
_MINIMAX_OPUS_DEFAULT_SAMPLE_RATE = 24000
# bitrate only takes effect on mp3, so it is sent only there and refused elsewhere: a
# parameter that reads as a request and is silently ignored is one a reader trusts.
_MINIMAX_BITRATES = (32000, 64000, 128000, 256000)
_MINIMAX_SPEED_RANGE = (0.5, 2.0)
_MINIMAX_MAX_VOLUME = 10.0          # (0, 10]
_MINIMAX_PITCH_RANGE = (-12, 12)
# No local text-length cap: the reference's 50,000 is the async API's figure and the
# relay serves the sync one, so a local number would be a guess. Upstream rejects
# over-long text itself.
# Objects inside ``input`` that model_specific merges field by field rather than
# replacing — the caller sets one key without restating the typed ones.
_MINIMAX_MERGED_INPUT_OBJECTS = frozenset({"voice_setting", "audio_setting", "voice_modify"})


@register_python_adapter
class MiniMaxSpeechAdapter(ModelAdapter):
    """Python Adapter for the MiniMax speech family (synchronous text-to-speech).

    Payload uses the ``input`` envelope with ``voice_setting`` / ``audio_setting``
    blocks. The result is returned directly in the POST response (is_async: false).
    Registered under ``minimax-speech-2-8-hd``; the turbo variant reuses this class
    via the registry extends-chain.
    """

    adapter_id = "minimax-speech-2-8-hd"

    _DEFAULT_VOICE = _MINIMAX_DEFAULT_VOICE
    _DEFAULT_SAMPLE_RATE = 32000
    _DEFAULT_BITRATE = 128000

    def validation_corrections(self, req: "GenerateAudioInput") -> dict[str, Any]:
        # Output-shape fallbacks only (container, sample rate, bitrate), as on seed-tts.
        # speed / volume / pitch / emotion out of range stay hard errors.
        corrected: dict[str, Any] = {}
        fmt = req.audio_format
        if fmt not in _MINIMAX_FORMATS:
            fmt = "mp3"
            corrected["audio_format"] = fmt
        if req.sample_rate is not None:
            rate = _nearest(req.sample_rate, self._sample_rates(fmt))
            if rate != req.sample_rate:
                corrected["sample_rate"] = rate
        if req.bitrate is not None:
            bitrate = _nearest(req.bitrate, _MINIMAX_BITRATES) if fmt == "mp3" else None
            if bitrate != req.bitrate:
                corrected["bitrate"] = bitrate
        corrected.update(_voice_correction(req.voice, self._DEFAULT_VOICE))
        return corrected

    @staticmethod
    def _sample_rates(fmt: str) -> tuple[int, ...]:
        return _MINIMAX_OPUS_SAMPLE_RATES if fmt == "ogg_opus" else _MINIMAX_SAMPLE_RATES

    def supports(self, req: "GenerateAudioInput") -> tuple[bool, str]:
        ok, reason = super().supports(req)
        if not ok:
            return False, reason
        if not req.text.strip():
            return False, f"{self.model_name} requires non-empty text"
        if req.voice is not None and req.voice not in _MINIMAX_SYSTEM_VOICES:
            return False, _invalid_voice_reason(self.model_name, req.voice, _MINIMAX_SYSTEM_VOICES)
        if req.emotion is not None and req.emotion not in _MINIMAX_EMOTIONS:
            return False, (
                f"{self.model_name} does not support emotion {req.emotion!r} "
                f"(supported: {', '.join(sorted(_MINIMAX_EMOTIONS))}); omit it to let the "
                f"model infer the delivery from the text"
            )
        if req.audio_format not in _MINIMAX_FORMATS:
            return False, f"{self.model_name} supports audio_format {', '.join(_MINIMAX_FORMATS)}"
        rates = self._sample_rates(req.audio_format)
        if req.sample_rate is not None and req.sample_rate not in rates:
            return False, (
                f"{self.model_name} supports sample_rate {', '.join(str(r) for r in rates)}"
                f" for {req.audio_format}"
            )
        if req.bitrate is not None:
            if req.audio_format != "mp3":
                return False, f"{self.model_name} applies bitrate only to mp3; omit bitrate"
            if req.bitrate not in _MINIMAX_BITRATES:
                return False, (
                    f"{self.model_name} supports bitrate "
                    f"{', '.join(str(b) for b in _MINIMAX_BITRATES)}"
                )
        low, high = _MINIMAX_SPEED_RANGE
        if not low <= req.speed <= high:
            return False, f"{self.model_name} supports speed from {low} to {high}"
        if not 0 < req.volume <= _MINIMAX_MAX_VOLUME:
            return False, f"{self.model_name} supports volume above 0 and up to {_MINIMAX_MAX_VOLUME:g}"
        low_pitch, high_pitch = _MINIMAX_PITCH_RANGE
        if not low_pitch <= req.pitch <= high_pitch:
            return False, f"{self.model_name} supports pitch from {low_pitch} to {high_pitch}"
        return True, ""

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
        is_opus = req.audio_format == "ogg_opus"
        default_rate = _MINIMAX_OPUS_DEFAULT_SAMPLE_RATE if is_opus else self._DEFAULT_SAMPLE_RATE
        audio_setting: dict = {
            "sample_rate": req.sample_rate or default_rate,
            "format": _MINIMAX_FORMATS.get(req.audio_format, req.audio_format),
            # Upstream defaults to stereo; speech is mono, and this was always sent.
            "channel": 1,
        }
        if req.audio_format == "mp3":
            audio_setting["bitrate"] = req.bitrate or self._DEFAULT_BITRATE
        inp: dict = {
            "text": req.text,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
        }
        payload: dict = {
            "model": self.cfgpu_model_id,   # Only place cfgpu_model_id is used
            "input": inp,
        }
        if req.model_specific:
            # Shaped like the payload: ``input`` is merged key by key, and its
            # voice_setting / audio_setting / voice_modify objects one level further,
            # so {"input": {"pronunciation_dict": ...}} adds a field instead of
            # replacing text and voice_setting (which the old top-level update did).
            for key, value in req.model_specific.items():
                if key == "input" and isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if (
                            sub_key in _MINIMAX_MERGED_INPUT_OBJECTS
                            and isinstance(sub_value, dict)
                            and isinstance(inp.get(sub_key), dict)
                        ):
                            inp[sub_key].update(sub_value)
                        else:
                            inp[sub_key] = sub_value
                else:
                    payload[key] = value
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
