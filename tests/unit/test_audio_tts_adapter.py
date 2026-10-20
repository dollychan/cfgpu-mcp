from __future__ import annotations

import base64
import json

import pytest

from cfgpu_mcp.adapters.audio_tts import (
    MiniMaxSpeechAdapter,
    SeedTTSAdapter,
    _MINIMAX_SYSTEM_VOICES,
    _extract_audio_url,
    _extract_inline_audio,
)
from cfgpu_mcp.errors import CFGPUError
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
        "tasks": ["text_to_speech"],
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
        "tasks": ["text_to_speech", "expressive_speech"],
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
    req = GenerateAudioInput(text="x", voice="custom_speaker", sample_rate=16000, audio_format="pcm")
    rp = adapter.build_payload(req)["req_params"]
    assert rp["speaker"] == "custom_speaker"
    assert rp["audio_params"] == {"format": "pcm", "sample_rate": 16000}


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
                 "reqTextLength": 21, "synthesizeTextLength": 20,
                 "urlExpireTime": 1782895148},
    })
    assert result.urls == ["https://x/a.mp3"]
    assert result.task_id == "vt-123"
    assert result.expires_at.timestamp() == 1782895148
    assert result.usage == {"characters": 20}


def test_seed_usage_is_none_without_synthesize_text_length():
    adapter = _seed_adapter()
    result = adapter.parse_response({"data": {"taskId": "vt-123", "taskStatus": 1}})
    assert result.usage is None


@pytest.mark.parametrize("req_text_length", [20, 0])
def test_seed_usage_falls_back_to_req_text_length(req_text_length):
    adapter = _seed_adapter()
    result = adapter.parse_response({
        "data": {
            "taskId": "vt-123",
            "taskStatus": 2,
            "reqTextLength": req_text_length,
        },
    })
    assert result.usage == {"characters": req_text_length}


def test_seed_model_specific_merges_top_level():
    adapter = _seed_adapter()
    req = GenerateAudioInput(text="x", model_specific={"foo": "bar"})
    payload = adapter.build_payload(req)
    assert payload["foo"] == "bar"


# The seed-tts 2.0 parameter surface (models/seed-tts-2-0/reference).

def _seed_req(**kw) -> GenerateAudioInput:
    return GenerateAudioInput(text="你好", **kw)


def test_seed_plain_request_sends_no_tuning_fields():
    rp = _seed_adapter().build_payload(_seed_req())["req_params"]
    assert rp["audio_params"] == {"format": "mp3", "sample_rate": 24000}
    assert "additions" not in rp


@pytest.mark.parametrize("fmt", ["mp3", "pcm", "ogg_opus"])
def test_seed_accepts_its_three_containers(fmt):
    ok, reason = _seed_adapter().supports(_seed_req(audio_format=fmt))
    assert ok, reason


@pytest.mark.parametrize("fmt", ["wav", "flac"])
def test_seed_refuses_containers_upstream_lacks(fmt):
    adapter = _seed_adapter()
    ok, reason = adapter.supports(_seed_req(audio_format=fmt))
    assert not ok and "ogg_opus" in reason
    assert adapter.validation_corrections(_seed_req(audio_format=fmt))["audio_format"] == "mp3"


def test_seed_pcm_is_not_corrected_away():
    corrected = _seed_adapter().validation_corrections(_seed_req(audio_format="pcm", voice="zh_female_xiaohe_uranus_bigtts"))
    assert corrected == {}


def test_seed_ogg_opus_defaults_to_48k_and_refuses_other_rates():
    adapter = _seed_adapter()
    rp = adapter.build_payload(_seed_req(audio_format="ogg_opus"))["req_params"]
    assert rp["audio_params"] == {"format": "ogg_opus", "sample_rate": 48000}
    req = _seed_req(audio_format="ogg_opus", sample_rate=24000)
    assert not adapter.supports(req)[0]
    assert adapter.validation_corrections(req)["sample_rate"] == 48000


def test_seed_sample_rate_must_be_a_published_value():
    adapter = _seed_adapter()
    assert adapter.supports(_seed_req(sample_rate=44100))[0]
    req = _seed_req(sample_rate=44000)
    assert not adapter.supports(req)[0]
    assert adapter.validation_corrections(req)["sample_rate"] == 44100


def test_seed_bitrate_maps_to_bit_rate():
    adapter = _seed_adapter()
    req = _seed_req(bitrate=160000)
    assert adapter.supports(req)[0]
    assert adapter.build_payload(req)["req_params"]["audio_params"]["bit_rate"] == 160000
    bad = _seed_req(bitrate=128000)
    assert not adapter.supports(bad)[0]
    assert adapter.validation_corrections(bad)["bitrate"] == 160000


def test_seed_pcm_takes_no_bitrate():
    adapter = _seed_adapter()
    req = _seed_req(audio_format="pcm", bitrate=64000)
    assert not adapter.supports(req)[0]
    assert adapter.validation_corrections(req)["bitrate"] is None


@pytest.mark.parametrize(
    "multiplier,rate", [(0.5, -50), (0.8, -20), (1.5, 50), (2.0, 100)]
)
def test_seed_speed_and_volume_map_linearly(multiplier, rate):
    ap = _seed_adapter().build_payload(_seed_req(speed=multiplier, volume=multiplier))["req_params"]["audio_params"]
    assert ap["speech_rate"] == rate
    assert ap["loudness_rate"] == rate


@pytest.mark.parametrize("field,value", [("speed", 0.4), ("speed", 2.1), ("volume", 0.4), ("volume", 2.5), ("pitch", 13), ("pitch", -13)])
def test_seed_refuses_out_of_range_delivery(field, value):
    adapter = _seed_adapter()
    req = _seed_req(**{field: value})
    ok, reason = adapter.supports(req)
    assert not ok and field in reason
    # Delivery is intent, not container shape: never silently clamped.
    assert field not in adapter.validation_corrections(req)


def test_seed_pitch_rides_additions_as_a_json_string():
    rp = _seed_adapter().build_payload(_seed_req(pitch=-3))["req_params"]
    assert isinstance(rp["additions"], str)
    assert json.loads(rp["additions"]) == {"post_process": {"pitch": -3}}


def test_seed_still_refuses_emotion():
    adapter = _seed_adapter()
    req = _seed_req(emotion="happy")
    assert not adapter.supports(req)[0]
    assert adapter.validation_corrections(req)["emotion"] is None


def test_seed_refuses_text_over_the_upstream_limit():
    adapter = _seed_adapter()
    assert adapter.supports(GenerateAudioInput(text="字" * 100_000))[0]
    ok, reason = adapter.supports(GenerateAudioInput(text="字" * 100_001))
    assert not ok and "100000" in reason


def test_seed_model_specific_req_params_merges_without_wiping_text():
    req = _seed_req(
        pitch=2,
        model_specific={
            "req_params": {
                "ssml": "<speak>你好</speak>",
                "audio_params": {"enable_timestamp": True},
                "additions": {"explicit_language": "zh-cn", "post_process": {"foo": 1}},
            }
        },
    )
    rp = _seed_adapter().build_payload(req)["req_params"]
    assert rp["text"] == "你好" and rp["speaker"]
    assert rp["ssml"] == "<speak>你好</speak>"
    assert rp["audio_params"]["enable_timestamp"] is True
    assert rp["audio_params"]["format"] == "mp3"
    assert json.loads(rp["additions"]) == {
        "post_process": {"pitch": 2, "foo": 1},
        "explicit_language": "zh-cn",
    }


def test_seed_model_specific_additions_accepts_a_json_string():
    req = _seed_req(model_specific={"req_params": {"additions": '{"silence_duration": 500}'}})
    rp = _seed_adapter().build_payload(req)["req_params"]
    assert json.loads(rp["additions"]) == {"silence_duration": 500}


def test_seed_model_specific_rejects_malformed_additions():
    req = _seed_req(model_specific={"req_params": {"additions": "{not json"}})
    with pytest.raises(CFGPUError) as exc:
        _seed_adapter().build_payload(req)
    assert exc.value.error_type == "invalid_params"


def test_seed_url_expiry_falls_back_to_one_hour():
    from datetime import UTC, datetime, timedelta

    result = _seed_adapter().parse_response({"success": True, "data": {"audioUrl": "https://x/a.mp3"}})
    remaining = result.expires_at - datetime.now(UTC)
    assert timedelta(minutes=59) < remaining <= timedelta(hours=1)




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


@pytest.mark.parametrize(
    "emotion",
    [
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
    ],
)
def test_minimax_supports_every_documented_emotion(emotion):
    adapter = _minimax_adapter()
    ok, reason = adapter.supports(GenerateAudioInput(text="x", emotion=emotion))
    assert ok, reason


def test_minimax_2_8_refuses_whisper_without_correcting_it():
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="x", emotion="whisper")
    ok, reason = adapter.supports(req)
    assert not ok and "whisper" in reason
    assert "emotion" not in adapter.validation_corrections(req)


# The speech-2.8 parameter surface (models/minimax-speech-2-8-hd/reference).

@pytest.mark.parametrize(
    "fmt,wire", [("mp3", "mp3"), ("wav", "wav"), ("flac", "flac"), ("pcm", "pcm"), ("ogg_opus", "opus")]
)
def test_minimax_accepts_every_schema_format(fmt, wire):
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="x", audio_format=fmt)
    ok, reason = adapter.supports(req)
    assert ok, reason
    assert "audio_format" not in adapter.validation_corrections(req)
    assert adapter.build_payload(req)["input"]["audio_setting"]["format"] == wire


def test_minimax_bitrate_is_sent_only_for_mp3():
    adapter = _minimax_adapter()
    for fmt in ("wav", "flac", "pcm", "ogg_opus"):
        setting = adapter.build_payload(GenerateAudioInput(text="x", audio_format=fmt))["input"]["audio_setting"]
        assert "bitrate" not in setting, fmt
    req = GenerateAudioInput(text="x", audio_format="wav", bitrate=128000)
    ok, reason = adapter.supports(req)
    assert not ok and "mp3" in reason
    assert adapter.validation_corrections(req)["bitrate"] is None


def test_minimax_bitrate_must_be_a_published_value():
    adapter = _minimax_adapter()
    assert adapter.supports(GenerateAudioInput(text="x", bitrate=256000))[0]
    req = GenerateAudioInput(text="x", bitrate=160000)
    assert not adapter.supports(req)[0]
    assert adapter.validation_corrections(req)["bitrate"] == 128000


def test_minimax_sample_rate_sets_depend_on_format():
    adapter = _minimax_adapter()
    assert adapter.supports(GenerateAudioInput(text="x", sample_rate=44100))[0]
    assert not adapter.supports(GenerateAudioInput(text="x", sample_rate=48000))[0]
    opus = GenerateAudioInput(text="x", audio_format="ogg_opus", sample_rate=16000)
    assert adapter.supports(opus)[0]
    bad_opus = GenerateAudioInput(text="x", audio_format="ogg_opus", sample_rate=44100)
    assert not adapter.supports(bad_opus)[0]
    assert adapter.validation_corrections(bad_opus)["sample_rate"] == 24000


@pytest.mark.parametrize("rate", [12000, 32000, 48000])
def test_minimax_opus_refuses_rates_the_relay_rejects(rate):
    """48000/12000 fail the relay's general sample_rate check; 32000 fails upstream."""
    ok, reason = _minimax_adapter().supports(
        GenerateAudioInput(text="x", audio_format="ogg_opus", sample_rate=rate)
    )
    assert not ok and "sample_rate" in reason


def test_minimax_ogg_opus_defaults_to_24k():
    setting = _minimax_adapter().build_payload(
        GenerateAudioInput(text="x", audio_format="ogg_opus")
    )["input"]["audio_setting"]
    assert setting == {"sample_rate": 24000, "format": "opus", "channel": 1}


@pytest.mark.parametrize(
    "field,value",
    [("speed", 0.4), ("speed", 2.1), ("volume", 0.0), ("volume", 10.5), ("pitch", 13), ("pitch", -13)],
)
def test_minimax_refuses_out_of_range_delivery(field, value):
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="x", **{field: value})
    ok, reason = adapter.supports(req)
    assert not ok and field in reason
    assert field not in adapter.validation_corrections(req)


def test_minimax_volume_reaches_ten():
    assert _minimax_adapter().supports(GenerateAudioInput(text="x", volume=10.0))[0]


def test_minimax_leaves_the_text_length_limit_to_upstream():
    assert _minimax_adapter().supports(GenerateAudioInput(text="字" * 60_000))[0]


def test_minimax_model_specific_input_merges_without_wiping_text():
    req = GenerateAudioInput(
        text="处理危险",
        speed=1.2,
        model_specific={
            "input": {
                "pronunciation_dict": {"tone": ["处理/(chu3)(li3)"]},
                "language_boost": "auto",
                "voice_setting": {"english_normalization": True},
                "audio_setting": {"channel": 2},
                "voice_modify": {"pitch": 10, "sound_effects": "robotic"},
            },
            "foo": "bar",
        },
    )
    payload = _minimax_adapter().build_payload(req)
    inp = payload["input"]
    assert inp["text"] == "处理危险"
    assert inp["voice_setting"]["speed"] == 1.2
    assert inp["voice_setting"]["english_normalization"] is True
    assert inp["audio_setting"]["channel"] == 2
    assert inp["audio_setting"]["format"] == "mp3"
    assert inp["pronunciation_dict"] == {"tone": ["处理/(chu3)(li3)"]}
    assert inp["language_boost"] == "auto"
    assert inp["voice_modify"] == {"pitch": 10, "sound_effects": "robotic"}
    assert payload["foo"] == "bar"


def test_minimax_inline_pcm_carries_its_sample_layout():
    """Shape observed from the relay: headerless s16le, layout only in extra_info."""
    samples = bytes.fromhex("e4ffd6ff2c001200")
    media = _extract_inline_audio({
        "output": {
            "data": {"audio": samples.hex()},
            "extra_info": {"audio_format": "pcm", "audio_sample_rate": 32000, "audio_channel": 1},
        }
    })
    assert media["mime_type"] == "audio/pcm"
    assert media["filename"] == "speech.pcm"
    assert media["sample_format"] == "s16le"
    assert media["sample_rate"] == 32000
    assert media["channels"] == 1
    assert base64.b64decode(media["data"]) == samples


def test_minimax_inline_mp3_shape_is_unchanged():
    media = _extract_inline_audio(
        {"output": {"data": {"audio": "fffb"}, "extra_info": {"audio_format": "mp3", "audio_sample_rate": 32000}}}
    )
    assert set(media) == {"data", "mime_type", "filename"}


def test_minimax_inline_opus_gets_an_ogg_mime_type():
    media = _extract_inline_audio(
        {"output": {"data": {"audio": "4f676753"}, "extra_info": {"audio_format": "opus"}}}
    )
    assert media["mime_type"] == "audio/ogg"


def test_minimax_voice_speed_overrides():
    adapter = _minimax_adapter()
    req = GenerateAudioInput(text="x", voice="female-1", speed=1.5, volume=2.0, pitch=3,
                             sample_rate=24000, bitrate=64000)
    inp = adapter.build_payload(req)["input"]
    assert inp["voice_setting"] == {"voice_id": "female-1", "speed": 1.5, "vol": 2.0, "pitch": 3}
    assert inp["audio_setting"]["sample_rate"] == 24000
    assert inp["audio_setting"]["bitrate"] == 64000


def test_minimax_voice_catalog_preserves_mixed_width_punctuation():
    assert "Cantonese_ProfessionalHost（F)" in _MINIMAX_SYSTEM_VOICES


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


def test_minimax_http_200_business_error_is_raised():
    """MiniMax carries business failures inside a successful HTTP response."""
    adapter = _minimax_adapter()
    resp = {
        "output": {
            "base_resp": {"status_code": 2054, "status_msg": "voice id not exist"},
        },
        "request_id": "09c19313-2c20-93fd-b022-49b2b5d8dc78",
    }

    with pytest.raises(CFGPUError) as exc_info:
        adapter.parse_response(resp)

    error = exc_info.value
    assert error.error_type == "invalid_params"
    assert error.retryable is False
    assert "voice id not exist" in error.user_message
    assert error.original == {"status_code": 2054, "status_msg": "voice id not exist"}


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


# ── MiniMax business-error translation ───────────────────────────────────────
# The upstream status_msg names the offending field and stops there ("invalid
# params: voice_setting emotion"). That tells a caller *that* it failed, not what
# to do next. Both fields now have authoritative ranges, so each rejection should
# return its concrete valid set instead of inviting another guess.

def _minimax_error(status_code, status_msg=""):
    adapter = _minimax_adapter()
    resp = {"output": {"base_resp": {"status_code": status_code, "status_msg": status_msg}}}
    with pytest.raises(CFGPUError) as exc_info:
        adapter.parse_response(resp)
    return exc_info.value


def test_emotion_rejection_is_classified_caller_fixable():
    """2013 is a bad argument, not a generation failure — task_failed misleads."""
    error = _minimax_error(2013, "invalid params, invalid params: voice_setting emotion")

    assert error.error_type == "invalid_params"
    assert error.retryable is False


def test_emotion_rejection_names_the_actionable_fix():
    error = _minimax_error(2013, "invalid params, invalid params: voice_setting emotion")

    # Says what to do, not just what broke.
    assert "emotion" in error.user_message
    assert "省略" in error.user_message
    assert "fearful" in error.user_message
    assert "不支持 whisper" in error.user_message
    # The card-documented alternative that cannot fail this way.
    assert "(laughs)" in error.user_message


def test_emotion_rejection_is_self_contained_without_a_card_hint():
    error = _minimax_error(2013, "invalid params, invalid params: voice_setting emotion")
    error.model_id = "MiniMax/speech-2.8-hd"
    message = error.to_tool_result_dict()["message"]

    assert "get_model_card" not in message


def test_generic_2013_without_emotion_stays_actionable_without_a_card_hint():
    """A 2013 about another field must not direct the agent to a hidden model card."""
    error = _minimax_error(2013, "invalid params: audio_setting sample_rate")
    error.model_id = "MiniMax/speech-2.8-hd"

    assert error.error_type == "invalid_params"
    assert "get_model_card" not in error.to_tool_result_dict()["message"]


def test_voice_rejection_explains_cross_family_reuse():
    """The top cause of 2054 is a seed-tts speaker sent to MiniMax."""
    error = _minimax_error(2054, "voice id not exist")

    assert "_uranus_bigtts" in error.user_message
    assert "seed-tts" in error.user_message


def test_voice_rejection_offers_the_documented_default():
    error = _minimax_error(2054, "voice id not exist")

    assert "male-qn-qingse" in error.user_message


def test_voice_rejection_points_to_the_agent_facing_voice_catalog():
    error = _minimax_error(2054, "voice id not exist")
    error.model_id = "MiniMax/speech-2.8-hd"

    message = error.to_tool_result_dict()["message"]
    assert "系统音色目录" in message and "voice 字段" in message
    # The data, not a tool: tool names are the host's choice (disabled_tools).
    assert "list_voice_profiles" not in message
    assert "get_model_card" not in message


def test_unknown_status_code_keeps_generic_failure_classification():
    """Only codes we understand get reclassified; the rest stay as they were."""
    error = _minimax_error(1002, "rate limit")

    assert error.error_type == "task_failed"
    assert "1002" in error.user_message
    assert "rate limit" in error.user_message


def test_translated_errors_preserve_the_raw_upstream_payload():
    """The remedy is added to the message; the original is never rewritten."""
    error = _minimax_error(2054, "voice id not exist")

    assert error.original == {"status_code": 2054, "status_msg": "voice id not exist"}
    # The upstream wording is still quoted for the human reading the log.
    assert "voice id not exist" in error.user_message
    assert "2054" in error.user_message
