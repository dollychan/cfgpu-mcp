from __future__ import annotations

import base64

import pytest

from cfgpu_mcp.adapters.audio_tts import (
    MiniMaxSpeechAdapter,
    SeedTTSAdapter,
    _extract_audio_url,
    _extract_inline_audio,
)
from cfgpu_mcp.tool_registry import GenerateAudioInput


def _seed_adapter() -> SeedTTSAdapter:
    config = {
        "adapter_id": "seed-tts-2-0",
        "display_name": "Seed TTS 2.0",
        "cfgpu_model_id": "seed-tts-2.0",
        "task_type": "audio",
        "endpoint": "/voice/generations",
        "is_async": True,
        "poll_endpoint": "/voice/tasks/{task_id}",
        "capabilities": {"text_to_speech"},
        "cost_tier": 3,
        "speed_tier": 3,
        "poll_config": {"base_interval": 3, "max_interval": 15, "backoff_factor": 1.3, "default_timeout": 300},
    }
    return SeedTTSAdapter.from_config(config)


def _minimax_adapter(cfgpu_model_id: str = "MiniMax/speech-2.8-hd") -> MiniMaxSpeechAdapter:
    config = {
        "adapter_id": "minimax-speech-2-8-hd",
        "display_name": "MiniMax Speech 2.8 HD",
        "cfgpu_model_id": cfgpu_model_id,
        "task_type": "audio",
        "endpoint": "/voice/generations",
        "is_async": False,
        "capabilities": {"text_to_speech", "emotion"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return MiniMaxSpeechAdapter.from_config(config)


# ── seed-tts ──────────────────────────────────────────────────────────────

def test_seed_payload_envelope_and_defaults():
    adapter = _seed_adapter()
    req = GenerateAudioInput(text="你好世界")
    payload = adapter.build_payload(req)
    assert payload["model"] == "seed-tts-2.0"
    rp = payload["req_params"]
    assert rp["text"] == "你好世界"
    assert rp["speaker"] == SeedTTSAdapter._DEFAULT_VOICE
    assert rp["audio_params"] == {"format": "mp3", "sample_rate": 24000}
    assert "callback_url" in rp


def test_seed_voice_and_sample_rate_override():
    adapter = _seed_adapter()
    req = GenerateAudioInput(text="x", voice="custom_speaker", sample_rate=16000, audio_format="wav")
    rp = adapter.build_payload(req)["req_params"]
    assert rp["speaker"] == "custom_speaker"
    assert rp["audio_params"] == {"format": "wav", "sample_rate": 16000}


def test_seed_extract_task_id_from_create_envelope():
    adapter = _seed_adapter()
    resp = {"code": 20000000, "message": "ok",
            "data": {"task_status": 1, "req_text_length": 20, "task_id": "vt-123"}}
    assert adapter.extract_task_id(resp) == "vt-123"


def test_seed_extract_status_from_poll_booleans():
    adapter = _seed_adapter()
    ok = {"data": {"taskStatus": 2, "audioUrl": "https://x/a.mp3"},
          "success": True, "failure": False, "running": False}
    fail = {"data": {"taskStatus": 3}, "success": False, "failure": True, "running": False}
    pending = {"data": {"taskStatus": 1}, "success": False, "failure": False, "running": True}
    assert adapter.extract_status(ok) == "succeeded"
    assert adapter.extract_status(fail) == "failed"
    assert adapter.extract_status(pending) == "running"


def test_seed_is_async_response_task_id():
    adapter = _seed_adapter()
    assert adapter.is_async is True
    result = adapter.parse_response({
        "code": 20000000, "message": "ok",
        "data": {"taskId": "vt-123", "taskStatus": 2, "audioUrl": "https://x/a.mp3",
                 "urlExpireTime": 1782895148},
    })
    assert result.urls == ["https://x/a.mp3"]
    assert result.task_id == "vt-123"
    assert result.expires_at.timestamp() == 1782895148


def test_seed_model_specific_merges_top_level():
    adapter = _seed_adapter()
    req = GenerateAudioInput(text="x", model_specific={"foo": "bar"})
    payload = adapter.build_payload(req)
    assert payload["foo"] == "bar"


# ── MiniMax ───────────────────────────────────────────────────────────────

def test_minimax_payload_envelope_and_defaults():
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="hello")
    payload = adapter.build_payload(req)
    assert payload["model"] == "MiniMax/speech-2.8-hd"
    inp = payload["input"]
    assert inp["text"] == "hello"
    vs = inp["voice_setting"]
    assert vs == {"voice_id": "male-qn-qingse", "speed": 1.0, "vol": 1.0, "pitch": 0}
    assert inp["audio_setting"] == {
        "sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1,
    }


def test_minimax_emotion_only_when_set():
    adapter = _minimax_adapter()
    assert "emotion" not in adapter.build_payload(GenerateAudioInput(text="x"))["input"]["voice_setting"]
    vs = adapter.build_payload(GenerateAudioInput(text="x", emotion="happy"))["input"]["voice_setting"]
    assert vs["emotion"] == "happy"


def test_minimax_voice_speed_overrides():
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="x", voice="female-1", speed=1.5, volume=2.0, pitch=3,
                             sample_rate=24000, bitrate=64000)
    inp = adapter.build_payload(req)["input"]
    assert inp["voice_setting"] == {"voice_id": "female-1", "speed": 1.5, "vol": 2.0, "pitch": 3}
    assert inp["audio_setting"]["sample_rate"] == 24000
    assert inp["audio_setting"]["bitrate"] == 64000


def test_minimax_is_sync_no_task_id():
    adapter = _minimax_adapter()
    assert adapter.is_async is False
    result = adapter.parse_response({"data": {"audio_url": "https://x/o.mp3"}, "model": "MiniMax/speech-2.8-hd"})
    assert result.urls == ["https://x/o.mp3"]
    assert result.task_id is None


def test_minimax_inline_audio_when_no_url():
    """MiniMax returns audio inline as a hex blob under output.data.audio (no URL).

    parse_response should decode it into an inline_media descriptor (base64 + mime),
    leaving urls empty, so the consumer can materialise it into its own OSS object.
    """
    adapter = _minimax_adapter()
    raw = b"\xff\xfb\x90\x00fake-mp3-frames"
    resp = {
        "output": {
            "extra_info": {"audio_format": "mp3", "audio_size": len(raw)},
            "data": {"audio": raw.hex(), "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
        "usage": {"characters": 34},
        "request_id": "req-1",
    }
    result = adapter.parse_response(resp)
    assert result.urls == []
    assert result.task_id is None
    assert result.inline_media == [
        {"data": base64.b64encode(raw).decode("ascii"), "mime_type": "audio/mpeg", "filename": "speech.mp3"}
    ]
    # inline_media surfaces in the media dict (artifact payload, not gated by metadata).
    assert result.to_dict()["inline_media"] == result.inline_media


def test_minimax_prefers_url_over_inline():
    """A real URL wins; no inline_media is emitted when a downloadable URL exists."""
    adapter = _minimax_adapter()
    resp = {"data": {"audio_url": "https://x/o.mp3", "audio": "deadbeef"}}
    result = adapter.parse_response(resp)
    assert result.urls == ["https://x/o.mp3"]
    assert result.inline_media is None


@pytest.mark.parametrize("resp,expected_mime", [
    ({"output": {"data": {"audio": b"x".hex()}, "extra_info": {"audio_format": "mp3"}}}, "audio/mpeg"),
    ({"output": {"data": {"audio": b"x".hex()}, "extra_info": {"audio_format": "wav"}}}, "audio/wav"),
    ({"output": {"data": {"audio": b"x".hex()}}}, "audio/mpeg"),  # format defaults to mp3
])
def test_extract_inline_audio_mime(resp, expected_mime):
    assert _extract_inline_audio(resp)["mime_type"] == expected_mime


@pytest.mark.parametrize("resp", [
    {},                                                  # no output
    {"output": {"data": {"audio": ""}}},                # empty blob
    {"output": {"data": {"audio": "not-hex-zz"}}},      # undecodable hex
    {"output": {"data": {}}},                           # no audio key
])
def test_extract_inline_audio_absent_or_bad(resp):
    assert _extract_inline_audio(resp) is None


def test_turbo_reuses_class_with_own_model_id():
    adapter = _minimax_adapter(cfgpu_model_id="MiniMax/speech-2.8-turbo")
    payload = adapter.build_payload(GenerateAudioInput(text="x"))
    assert payload["model"] == "MiniMax/speech-2.8-turbo"


# ── URL extraction ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("resp,expected", [
    ({"content": {"audioUrl": "https://a/x.mp3"}}, "https://a/x.mp3"),
    ({"data": {"audio_url": "https://a/y.mp3"}}, "https://a/y.mp3"),
    ({"data": {"url": "http://a/z.wav"}}, "http://a/z.wav"),
    ({"url": "https://a/top.mp3"}, "https://a/top.mp3"),
    ({"data": {"audio": "deadbeefhex"}}, None),   # hex blob, not a URL → rejected
    ({}, None),
])
def test_extract_audio_url(resp, expected):
    assert _extract_audio_url(resp) == expected
