from __future__ import annotations

from cfgpu_mcp.adapters.vision_chat import QwenVisionAdapter
from cfgpu_mcp.tool_registry import UnderstandVisionInput


def _adapter(cfgpu_model_id: str = "qwen3-vl-30b-a3b-thinking") -> QwenVisionAdapter:
    config = {
        "adapter_id": "qwen3-vl-30b-a3b-thinking",
        "display_name": "Qwen3-VL 30B A3B Thinking",
        "cfgpu_model_id": cfgpu_model_id,
        "task_type": "understand",
        "endpoint": "/model/v1/chat/completions",
        "is_async": False,
        "capabilities": {"image_understanding", "video_understanding"},
        "cost_tier": 2,
        "speed_tier": 4,
    }
    return QwenVisionAdapter.from_config(config)


# ── build_payload ─────────────────────────────────────────────────────────

def test_text_only_payload_envelope_and_defaults():
    adapter = _adapter()
    payload = adapter.build_payload(UnderstandVisionInput(prompt="Hello!"))
    assert payload["model"] == "qwen3-vl-30b-a3b-thinking"
    assert payload["stream"] is False
    messages = payload["messages"]
    assert messages[0] == {"role": "system", "content": "You are a helpful assistant."}
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == [{"type": "text", "text": "Hello!"}]
    # untyped optional params are omitted
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_images_become_image_url_parts():
    adapter = _adapter()
    req = UnderstandVisionInput(prompt="描述", images=["https://x/a.jpg", "https://x/b.png"])
    content = adapter.build_payload(req)["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "描述"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://x/a.jpg"}}
    assert content[2] == {"type": "image_url", "image_url": {"url": "https://x/b.png"}}


def test_video_becomes_video_url_part():
    adapter = _adapter()
    req = UnderstandVisionInput(prompt="时间线", video="https://x/clip.mp4")
    content = adapter.build_payload(req)["messages"][1]["content"]
    assert content[-1] == {"type": "video_url", "video_url": {"url": "https://x/clip.mp4"}}


def test_system_prompt_and_sampling_overrides():
    adapter = _adapter()
    req = UnderstandVisionInput(
        prompt="x", system_prompt="你是视觉专家", max_tokens=512, temperature=0.2
    )
    payload = adapter.build_payload(req)
    assert payload["messages"][0]["content"] == "你是视觉专家"
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.2


def test_model_specific_merges_top_level():
    adapter = _adapter()
    req = UnderstandVisionInput(prompt="x", model_specific={"top_p": 0.8})
    assert adapter.build_payload(req)["top_p"] == 0.8


# ── parse_response ─────────────────────────────────────────────────────────

def test_is_sync_no_task_id():
    adapter = _adapter()
    assert adapter.is_async is False


def test_parse_extracts_message_id_and_usage():
    adapter = _adapter()
    resp = {
        "id": "chatcmpl-abc123",
        "model": "qwen3-vl-30b-a3b-thinking",
        "choices": [
            {"message": {"role": "assistant", "content": "这是一只红熊猫", "reasoning_content": "我看到了..."}}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    result = adapter.parse_response(resp)
    assert result.message == {
        "role": "assistant",
        "content": "这是一只红熊猫",
        "reasoning_content": "我看到了...",
    }
    assert result.response_id == "chatcmpl-abc123"
    assert result.urls == []
    assert result.task_id is None
    assert result.usage == {"prompt_tokens": 100, "completion_tokens": 20}
    # to_dict emits the chat-completion envelope; usage only with metadata
    d = result.to_dict()
    assert d == {
        "id": "chatcmpl-abc123",
        "model": "qwen3-vl-30b-a3b-thinking",
        "message": result.message,
    }
    assert "usage" not in d
    assert result.to_dict(return_metadata=True)["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }


def test_parse_handles_missing_choices_and_reasoning():
    adapter = _adapter()
    result = adapter.parse_response({"model": "qwen3-vl-30b-a3b-thinking", "choices": []})
    assert result.message == {"role": "assistant", "content": ""}
    assert "reasoning_content" not in result.message


def test_reuses_class_with_own_model_id():
    adapter = _adapter(cfgpu_model_id="qwen3-vl-235b-a22b-thinking")
    payload = adapter.build_payload(UnderstandVisionInput(prompt="x"))
    assert payload["model"] == "qwen3-vl-235b-a22b-thinking"
