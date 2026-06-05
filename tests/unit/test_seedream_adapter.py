import pytest
from cfgpu_mcp.adapters.seedream import SeedreamAdapter
from cfgpu_mcp.tool_registry import GenerateImageInput


def _make_adapter() -> SeedreamAdapter:
    config = {
        "adapter_id": "doubao-seedream-5-0-lite",
        "display_name": "Doubao Seedream 5.0 lite",
        "cfgpu_model_id": "doubao-seedream-5-0-260128",
        "task_type": "image",
        "endpoint": "/v1/images/generations",
        "is_async": False,
        "poll_endpoint": None,
        "capabilities": {"text_to_image", "image_to_image", "multi_image_fusion"},
        "cost_tier": 2,
        "speed_tier": 3,
    }
    return SeedreamAdapter.from_config(config)


def test_2k_1x1_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="1:1")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2048x2048"


def test_2k_16x9_maps_to_correct_size():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", resolution="2K", aspect_ratio="16:9")
    payload = adapter.build_payload(req)
    assert payload["size"] == "2848x1600"


def test_single_reference_image_is_string():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", reference_images=["https://example.com/img.jpg"])
    payload = adapter.build_payload(req)
    assert isinstance(payload["image"], str)


def test_multiple_reference_images_is_list():
    adapter = _make_adapter()
    req = GenerateImageInput(
        prompt="x",
        reference_images=["https://example.com/a.jpg", "https://example.com/b.jpg"],
    )
    payload = adapter.build_payload(req)
    assert isinstance(payload["image"], list)
    assert len(payload["image"]) == 2


def test_no_reference_images_omits_image_field():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert "image" not in payload


def test_model_specific_merged():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", model_specific={"watermark": False, "output_format": "png"})
    payload = adapter.build_payload(req)
    assert payload["watermark"] is False
    assert payload["output_format"] == "png"


def test_cfgpu_model_id_only_in_model_field():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert payload["model"] == "doubao-seedream-5-0-260128"
    # Verify it only appears once in the whole payload
    assert str(payload).count("doubao-seedream-5-0-260128") == 1


def test_parse_response_task_id_is_none():
    adapter = _make_adapter()
    resp = {"model": "doubao-seedream-5-0-260128", "data": [{"url": "https://cdn/img.jpg"}]}
    result = adapter.parse_response(resp)
    assert result.task_id is None


def test_parse_response_extracts_urls():
    adapter = _make_adapter()
    resp = {"data": [{"url": "https://cdn/a.jpg"}, {"url": "https://cdn/b.jpg"}]}
    result = adapter.parse_response(resp)
    assert len(result.urls) == 2


def test_parse_response_expires_at_set():
    adapter = _make_adapter()
    resp = {"data": [{"url": "https://cdn/img.jpg"}]}
    result = adapter.parse_response(resp)
    assert result.expires_at is not None


def test_n_greater_than_one_enables_group_generation():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x", n=4)
    payload = adapter.build_payload(req)
    assert payload["sequential_image_generation"] == "auto"
    assert payload["sequential_image_generation_options"] == {"max_images": 4}


def test_n_equals_one_omits_group_fields():
    adapter = _make_adapter()
    req = GenerateImageInput(prompt="x")
    payload = adapter.build_payload(req)
    assert "sequential_image_generation" not in payload
    assert "sequential_image_generation_options" not in payload
