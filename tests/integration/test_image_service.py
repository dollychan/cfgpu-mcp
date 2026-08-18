"""Integration tests — submit real, billed generations.

Opt in with CFGPU_RUN_INTEGRATION=1; see tests/integration/conftest.py for why
the gate is an explicit switch and not the presence of a credential.
"""
import pytest
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.errors import CFGPUError


@pytest.mark.asyncio
async def test_text_to_image_returns_url_and_expires_at():
    result = await image_service.generate_image(
        prompt="a simple white circle on black background",
        model="doubao-seedream-5-0-lite",
        resolution="1K",
        aspect_ratio="1:1",
    )
    assert result["urls"]
    assert result["expires_at"]


@pytest.mark.asyncio
async def test_wait_false_returns_task_id():
    result = await image_service.generate_image(
        prompt="a cat",
        model="doubao-seedream-5-0-lite",
        wait=False,
    )
    # Seedream is sync, so even wait=False returns immediately with urls
    assert "urls" in result or "task_id" in result


@pytest.mark.asyncio
async def test_return_metadata_includes_model_used():
    result = await image_service.generate_image(
        prompt="a dot",
        model="doubao-seedream-5-0-lite",
        return_metadata=True,
    )
    assert result.get("model_used") is not None


@pytest.mark.asyncio
async def test_explicit_model_doubao_seedream():
    result = await image_service.generate_image(
        prompt="a red square",
        model="doubao-seedream-5-0-lite",
        return_metadata=True,
    )
    assert "doubao-seedream" in (result.get("model_used") or "")


@pytest.mark.asyncio
async def test_invalid_token_raises_auth_error(monkeypatch):
    monkeypatch.setenv("CFGPU_API_TOKEN", "invalid-token-xxx")
    import cfgpu_mcp.config as cfg
    cfg._client = None  # force new client
    with pytest.raises(CFGPUError) as exc_info:
        await image_service.generate_image(
            prompt="a cat",
            model="doubao-seedream-5-0-lite",
        )
    assert exc_info.value.error_type == "auth"
    cfg._client = None


@pytest.mark.asyncio
async def test_content_blocked_returns_error():
    with pytest.raises(CFGPUError) as exc_info:
        await image_service.generate_image(
            prompt="[intentionally blocked content for testing]",
            model="doubao-seedream-5-0-lite",
        )
    assert exc_info.value.error_type in ("content_blocked", "invalid_params")
