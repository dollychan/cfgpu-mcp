"""Integration tests — require CFGPU_API_TOKEN, skipped in CI by conftest."""
import pytest
from cfgpu_mcp.service import video as video_service


@pytest.mark.asyncio
async def test_text_to_video_returns_url_and_expires_at():
    result = await video_service.generate_video(
        prompt="a white ball bouncing slowly",
        model="wan-2-0-fast",
        duration_seconds=4,
        resolution="480p",
        with_audio=False,
    )
    assert result["urls"]
    assert result["expires_at"]


@pytest.mark.asyncio
async def test_wait_false_returns_task_id():
    result = await video_service.generate_video(
        prompt="a simple scene",
        model="wan-2-0-fast",
        duration_seconds=4,
        wait=False,
    )
    assert "task_id" in result
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_first_frame_image_to_video():
    result = await video_service.generate_video(
        prompt="the ball slowly moves to the right",
        model="wan-2-0-fast",
        first_frame="https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png",
        duration_seconds=4,
        resolution="480p",
        with_audio=False,
    )
    assert result["urls"]


@pytest.mark.asyncio
async def test_reference_videos_multimodal():
    result = await video_service.generate_video(
        prompt="continue this motion",
        model="wan-2-0-fast",
        reference_videos=["https://www.w3schools.com/html/mov_bbb.mp4"],
        duration_seconds=4,
        resolution="480p",
        with_audio=False,
    )
    assert result["urls"]
