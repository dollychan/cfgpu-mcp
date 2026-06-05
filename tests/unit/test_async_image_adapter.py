import pytest
from cfgpu_mcp.adapters.async_image import GptImage2Adapter, NanoBananaAdapter
from cfgpu_mcp.tool_registry import GenerateImageInput


def _gpt() -> GptImage2Adapter:
    return GptImage2Adapter.from_config({
        "adapter_id": "gpt-image-2",
        "display_name": "GPT Image 2",
        "cfgpu_model_id": "gpt-image-2",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "capabilities": {"text_to_image", "image_to_image"},
        "cost_tier": 2,
        "speed_tier": 3,
    })


def _nano() -> NanoBananaAdapter:
    return NanoBananaAdapter.from_config({
        "adapter_id": "nano-banana-2",
        "display_name": "Nano Banana 2",
        "cfgpu_model_id": "nano2",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": True,
        "poll_endpoint": "/v1/images/tasks/{task_id}",
        "capabilities": {"text_to_image", "image_to_image"},
        "cost_tier": 4,
        "speed_tier": 3,
    })


@pytest.mark.parametrize("adapter", [_gpt(), _nano()])
def test_rejects_group_generation(adapter):
    req = GenerateImageInput(prompt="x", n=3)
    ok, reason = adapter.supports(req)
    assert ok is False
    assert "doubao-seedream" in reason


@pytest.mark.parametrize("adapter", [_gpt(), _nano()])
def test_accepts_single_image(adapter):
    req = GenerateImageInput(prompt="x")
    ok, _ = adapter.supports(req)
    assert ok is True
