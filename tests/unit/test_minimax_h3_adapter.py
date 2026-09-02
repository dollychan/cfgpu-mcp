from pathlib import Path

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.adapters.minimax_h3 import MinimaxH3Adapter
from cfgpu_mcp.task_manager import _extract_error_message
from cfgpu_mcp.tool_registry import GenerateVideoInput


MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


@pytest.fixture(scope="module")
def adapter() -> MinimaxH3Adapter:
    import cfgpu_mcp.adapters  # noqa: F401

    registry = AdapterRegistry(MODELS_DIR, available_providers={"cfgpu", "cfgpu-daily"})
    registry.load()
    result = registry.get("MiniMax-H3")
    assert isinstance(result, MinimaxH3Adapter)
    return result


def _req(**kwargs) -> GenerateVideoInput:
    kwargs.setdefault("prompt", "A train crossing a snowy valley")
    kwargs.setdefault("aspect_ratio", "16:9")
    return GenerateVideoInput(**kwargs)


def test_model_and_endpoints_are_wired(adapter):
    """Pinned because both halves migrate independently.

    The provider is the test-phase placement and becomes ``cfgpu`` on launch;
    the endpoints are CFGPU's shared video routes and must NOT drift back to
    MiniMax's native ``/v2/video_generation`` when that happens.
    """
    assert adapter.provider == "cfgpu-daily"
    assert adapter.model_name == "MiniMax-H3"
    assert adapter.cfgpu_model_id == "MiniMax-H3"
    assert adapter.endpoint == "/video/generations"
    assert adapter.poll_endpoint == "/video/tasks/{task_id}"


@pytest.mark.parametrize("resolution,wire", [("720p", "768P"), ("1080p", "2K")])
def test_text_to_video_payload_maps_resolution(adapter, resolution, wire):
    payload = adapter.build_payload(_req(resolution=resolution, duration_seconds=5))
    assert payload == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "A train crossing a snowy valley"}],
        "resolution": wire,
        "duration": 5,
        "aigc_watermark": False,
        "ratio": "16:9",
    }


def test_image_to_video_omits_ratio(adapter):
    payload = adapter.build_payload(_req(first_frame="https://x.test/first.png"))
    assert "ratio" not in payload
    assert payload["content"][1]["role"] == "first_frame"


def test_bare_text_to_video_is_routable_and_gets_a_concrete_ratio(adapter):
    """The plainest call — prompt only — must reach this model and send 16:9.

    ``adaptive`` is the unified schema default ("you pick"), and upstream refuses
    it for text-to-video. Treating that as a validation error made
    ``generate_video(prompt=...)`` unroutable here, so ``model="auto"`` could
    never pick this model for ordinary text-to-video however it scored.
    """
    req = GenerateVideoInput(prompt="waves on a beach")
    assert req.aspect_ratio == "adaptive"          # pin the schema default
    ok, reason = adapter.supports(req)
    assert ok, reason
    assert adapter.build_payload(req)["ratio"] == "16:9"
    assert adapter.validation_corrections(req)["aspect_ratio"] == "16:9"


def test_reference_to_video_keeps_adaptive(adapter):
    """Only text-to-video is substituted: adaptive is legal (and the upstream
    default) once any reference material is present, so a substitution there
    would silently overrule the material's own geometry."""
    req = _req(aspect_ratio="adaptive", reference_images=["https://x.test/i.png"])
    assert adapter.build_payload(req)["ratio"] == "adaptive"
    assert "aspect_ratio" not in adapter.validation_corrections(req)


def test_explicit_ratio_is_never_substituted(adapter):
    assert adapter.build_payload(_req(aspect_ratio="21:9"))["ratio"] == "21:9"


def test_watermark_maps_to_aigc_watermark(adapter):
    """The unified flag has a home on this API, unlike with_audio/prompt_extend."""
    payload = adapter.build_payload(_req(watermark=True))
    assert payload["aigc_watermark"] is True


def test_reference_content_uses_minimax_roles(adapter):
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
    """Create and query disagree on shape: only the query nests under ``task``.

    Create answers with a bare ``{"task_id": ...}``. Reading only the nested
    form here is the failure the happyhorse snake_case fix was about — the task
    submits and bills, and nothing can ever poll it back.
    """
    assert adapter.extract_task_id({"task_id": "task_01K2..."}) == "task_01K2..."


def test_nested_create_response_yields_task_id_too(adapter):
    """CFGPU's task layer is shared across upstreams, so the envelope may or may
    not survive the proxy. Both forms read, because guessing wrong is unpollable.
    """
    assert adapter.extract_task_id({"task": {"id": 424010985738629}}) == "424010985738629"


def test_flat_poll_response_is_parsed(adapter):
    """Same tolerance on the way out: an unwrapped task still yields the URL."""
    result = adapter.parse_response(
        {"id": "77", "status": "succeeded", "content": {"url": "https://cdn.test/f.mp4"}}
    )
    assert result.urls == ["https://cdn.test/f.mp4"]
    assert result.task_id == "77"


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
