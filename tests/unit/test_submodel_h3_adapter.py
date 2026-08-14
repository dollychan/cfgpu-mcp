from pathlib import Path

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.submodel_h3 import SubmodelH3Adapter
from cfgpu_mcp.task_manager import _extract_error_message
from cfgpu_mcp.tool_registry import GenerateVideoInput


MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


@pytest.fixture(scope="module")
def adapter() -> SubmodelH3Adapter:
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(MODELS_DIR, available_providers={"cfgpu", "submodel"})
    registry.load()
    result = registry.get("submodel/minimax-h3")
    assert isinstance(result, SubmodelH3Adapter)
    return result


def _req(**kwargs) -> GenerateVideoInput:
    kwargs.setdefault("prompt", "A train crossing a snowy valley")
    kwargs.setdefault("aspect_ratio", "16:9")
    return GenerateVideoInput(**kwargs)


def test_model_and_endpoints_are_wired(adapter):
    assert adapter.provider == "submodel"
    assert adapter.cfgpu_model_id == "MiniMax-H3"
    assert adapter.endpoint == "/v2/video_generation"
    assert adapter.poll_endpoint == "/v2/query/video_generation/{task_id}"


@pytest.mark.parametrize("resolution,wire", [("720p", "768P"), ("1080p", "2K")])
def test_text_to_video_payload_maps_resolution(adapter, resolution, wire):
    payload = adapter.build_payload(_req(resolution=resolution, duration_seconds=5))
    assert payload == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "A train crossing a snowy valley"}],
        "resolution": wire,
        "duration": 5,
        "ratio": "16:9",
    }


def test_image_to_video_omits_ratio(adapter):
    payload = adapter.build_payload(_req(first_frame="https://x.test/first.png"))
    assert "ratio" not in payload
    assert payload["content"][1]["role"] == "first_frame"


def test_reference_content_uses_submodel_roles(adapter):
    payload = adapter.build_payload(_req(
        aspect_ratio="adaptive",
        reference_images=["https://x.test/i.png"],
        reference_videos=["https://x.test/v.mp4"],
        reference_audios=["https://x.test/a.mp3"],
    ))
    assert [item.get("role") for item in payload["content"][1:]] == [
        "reference_image", "reference_video", "reference_audio",
    ]
    assert payload["ratio"] == "adaptive"


def test_flat_create_response_yields_task_id(adapter):
    """Create and query disagree on shape, and only query is nested.

    ``POST /v2/video_generation`` answers with a bare ``{"task_id": ...}`` while
    the query wraps everything in ``task``. The base extract_task_id covers the
    flat form, so the adapter deliberately does not override it — pinned here
    because the nested query response makes the opposite look self-evident.
    """
    assert adapter.extract_task_id({"task_id": "task_01K2..."}) == "task_01K2..."


def test_nested_poll_response_is_parsed(adapter):
    response = {
        "task": {
            "id": "428863236170174",
            "model": "MiniMax-H3",
            "status": "succeeded",
            "resolution": "768P",
            "duration": 5,
            "ratio": "16:9",
            "content": {"url": "https://cdn.test/out.mp4", "prompt": "train"},
            "usage": {"total_seconds": 5, "output_seconds": 5},
        }
    }
    assert adapter.extract_status(response) == "succeeded"
    result = adapter.parse_response(response)
    assert result.urls == ["https://cdn.test/out.mp4"]
    assert result.task_id == "428863236170174"
    assert result.aspect_ratio == "16:9"
    assert result.usage == {"total_seconds": 5, "output_seconds": 5}


def test_queued_status_is_read_from_nested_task(adapter):
    assert adapter.extract_status({"task": {"status": "queued"}}) == "pending"


def test_nested_task_error_message_is_preserved(adapter):
    response = {
        "task": {
            "status": "failed",
            "error": {
                "type": "generation_failed_error",
                "message": "Generation failed after retries",
                "code": "GENERATION_FAILED",
            },
        }
    }
    assert adapter.extract_status(response) == "failed"
    assert _extract_error_message(response) == "Generation failed after retries"


@pytest.mark.parametrize("kwargs,needle", [
    ({"aspect_ratio": "adaptive"}, "explicit aspect_ratio"),
    ({"duration_seconds": -1}, "explicit duration"),
    ({"resolution": "480p"}, "does not support resolution"),
    ({"last_frame": "https://x.test/last.png"}, "requires first_frame"),
    ({"first_frame": "https://x.test/first.png", "reference_videos": ["https://x.test/v.mp4"]},
     "mutually exclusive"),
    ({"reference_images": [f"https://x.test/{i}.png" for i in range(10)]}, "at most 9"),
    ({"reference_videos": [f"https://x.test/{i}.mp4" for i in range(4)]}, "at most 3"),
    ({"reference_audios": [f"https://x.test/{i}.mp3" for i in range(4)]}, "at most 3"),
])
def test_invalid_combinations_are_rejected_locally(adapter, kwargs, needle):
    ok, reason = adapter.supports(_req(**kwargs))
    assert not ok
    assert needle in reason


def test_empty_prompt_is_rejected(adapter):
    ok, reason = adapter.supports(_req(prompt="   "))
    assert not ok
    assert "non-empty" in reason
