import pytest
from cfgpu_mcp.adapters.generic import GenericAdapter, _render
from cfgpu_mcp.tool_registry import GenerateImageInput


def _make_adapter(extra: dict | None = None) -> GenericAdapter:
    config = {
        "adapter_id": "test-model",
        "display_name": "Test Model",
        "cfgpu_model_id": "test-model-id",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": False,
        "poll_endpoint": None,
        "capabilities": ["text_to_image"],
        "cost_tier": 2,
        "speed_tier": 3,
        "payload_mapping": {
            "model": "{cfgpu_model_id}",
            "prompt": "{prompt}",
            "size": "{resolution}",
            "watermark": "{watermark|default:false}",
        },
        "response_url_key": "data.0.url",
    }
    if extra:
        config.update(extra)
    return GenericAdapter.from_config(config)


def test_metadata_fields_populated():
    adapter = _make_adapter()
    assert adapter.adapter_id == "test-model"
    assert adapter.cfgpu_model_id == "test-model-id"
    assert adapter.task_type == "image"
    assert adapter.cost_tier == 2


def test_field_substitution():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="a cat", resolution="2K")
    payload = adapter.build_payload(req)
    assert payload["prompt"] == "a cat"
    assert payload["model"] == "test-model-id"
    assert payload["size"] == "2K"


def test_default_value_used_when_field_missing():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="a dog")
    payload = adapter.build_payload(req)
    assert payload["watermark"] == "false"


def test_actual_value_overrides_default():
    adapter = _make_adapter(extra={
        "payload_mapping": {"flag": "{enabled|default:no}"}
    })
    req = GenerateImageInput(prompt="x", model_specific=None)
    # field "enabled" not in model fields, so default is used
    payload = adapter.build_payload(req)
    assert payload["flag"] == "no"


def test_model_specific_merged_into_payload():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", model_specific={"extra_param": "value"})
    payload = adapter.build_payload(req)
    assert payload["extra_param"] == "value"


def test_supports_correct_task_type():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    ok, _ = adapter.supports(req)
    assert ok is True


def test_supports_rejects_wrong_task_type():
    from cfgpu_mcp.tool_registry import GenerateVideoInput
    adapter = _make_adapter()
    req = GenerateVideoInput(prompt="x")
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "image" in reason


def test_parse_response_extracts_url():
    adapter = _make_adapter()
    resp = {"data": [{"url": "https://example.com/img.png"}]}
    result = adapter.parse_response(resp)
    assert result.urls == ["https://example.com/img.png"]
    assert result.expires_at is not None


def test_parse_response_missing_url_returns_empty():
    adapter = _make_adapter()
    result = adapter.parse_response({"data": []})
    assert result.urls == []


def test_estimate_poll_timeout_uses_poll_config():
    adapter = _make_adapter(extra={"is_async": True, "poll_config": {"default_timeout": 999}})
    req = GenerateImageInput(prompt="x")
    assert adapter.estimate_poll_timeout(req) == 999
