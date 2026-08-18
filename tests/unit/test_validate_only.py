"""``validate_only`` runs the real resolution path and stops before the upstream POST.

The whole value of this flag is negative — it is defined by what it does *not* do — so
the load-bearing assertions here are ``assert_not_called``. If it ever regresses into
sending the request, every caller that runs it before a human approval starts paying for
two generations instead of one, and nothing about the returned shape would look wrong.

The second theme is *equivalence*: a preflight that accepted what the billed call rejects
(or the reverse) would be worse than having none, because callers would trust it. So the
failure cases below drive real adapters through the real router rather than mocks.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cfgpu_mcp.adapters.registry import AdapterRegistry
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.service import audio as audio_service
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import video as video_service
from cfgpu_mcp.router import ModelRouter
from cfgpu_mcp.task_manager import validate_request
from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

MODELS_DIR = Path(__file__).parent.parent.parent / "src" / "cfgpu_mcp" / "models"


def _real_registry() -> AdapterRegistry:
    import cfgpu_mcp.adapters  # noqa: F401 — triggers @register_python_adapter

    registry = AdapterRegistry(model_dir=MODELS_DIR)
    registry.load()
    return registry


def _patched_real_registry(client: MagicMock, repo: MagicMock):
    """Real registry + real router; only the IO collaborators are doubles.

    The repository double is an ``AsyncMock`` that records whether it was awaited at
    all — acquiring it is itself the thing under test (a request that was never
    submitted must not create a task row).
    """
    return (
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=_real_registry())),
        patch("cfgpu_mcp.config.get_task_repository", repo),
        patch("cfgpu_mcp.config.client_for", MagicMock(return_value=client)),
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(side_effect=AssertionError("validate_only must not POST"))
    return client


_CASES = [
    (
        "image",
        image_service.generate_image,
        {"prompt": "一只猫", "model": "doubao-seedream-5-0-lite"},
    ),
    (
        "video",
        video_service.generate_video,
        {"prompt": "海浪", "model": "doubao-seedance-2-0", "duration_seconds": 5},
    ),
    (
        "audio",
        audio_service.generate_audio,
        {"text": "你好", "model": "MiniMax/speech-2.8-hd"},
    ),
]


# ── The negative contract ────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("name, svc, kwargs", _CASES)
async def test_validate_only_never_posts(name, svc, kwargs):
    """The one assertion that protects real money."""
    client = _client()
    repo = AsyncMock()
    a, b, c = _patched_real_registry(client, repo)
    with a, b, c:
        result = await svc(**kwargs, validate_only=True)

    client.post.assert_not_called()
    assert result["validated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name, svc, kwargs", _CASES)
async def test_validate_only_never_acquires_the_task_repository(name, svc, kwargs):
    """No task row for work nobody started — otherwise task_status grows phantom rows."""
    repo = AsyncMock()
    a, b, c = _patched_real_registry(_client(), repo)
    with a, b, c:
        await svc(**kwargs, validate_only=True)

    repo.assert_not_awaited()


# ── What it reports ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_only_resolves_auto_to_a_concrete_model():
    """`auto` names nothing a human can weigh, so an approval card needs the real one."""
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="海浪拍打沙滩", model="auto", validate_only=True
        )

    assert result["model_used"] != "auto"
    assert result["task_type"] == "video"
    # Enough to price the approval: whether it returns a handle, and the cost signal.
    assert isinstance(result["is_async"], bool)
    assert isinstance(result["cost_tier"], int)


@pytest.mark.asyncio
async def test_validate_only_rejects_an_empty_model_candidate_list():
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c, pytest.raises(CFGPUError) as exc:
        await image_service.generate_image(prompt="猫", model=[], validate_only=True)

    assert exc.value.error_type == "invalid_params"


# ── corrected_args: what to change before submitting for real ────────────────
#
# The split these tests defend: a *delegated* model choice gets pinned, an *explicit*
# one is untouchable. Blur it in either direction and something breaks — leave `auto`
# unpinned and the approval card names nothing a human can weigh; rewrite an explicit
# model and the card shows a name the caller never wrote.

@pytest.mark.asyncio
async def test_corrected_args_pins_auto_to_the_routed_model():
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="海浪拍打沙滩", model="auto", validate_only=True
        )

    assert result["corrected_args"]["model"] == result["model_used"]
    assert result["corrected_args"]["model"] != "auto"


@pytest.mark.asyncio
async def test_corrected_args_pins_a_candidate_list_too():
    """A list is `auto` within a subset — the choice is delegated just the same."""
    candidates = ["doubao-seedance-2-0", "doubao-seedance-2-0-fast"]
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="海浪", model=candidates, duration_seconds=5, validate_only=True
        )

    assert result["corrected_args"]["model"] in candidates


@pytest.mark.asyncio
async def test_corrected_args_never_rewrites_an_explicit_model():
    """Not even to normalize an alias.

    `AdapterRegistry.get` resolves adapter_id / cfgpu_model_id / display_name to the
    same adapter, so rewriting changes the name on the approval card without changing
    what runs — cost with no effect. `model_used` still reports the canonical name;
    reporting and instructing are different fields on purpose.
    """
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await image_service.generate_image(
            prompt="猫", model="doubao-seedream-5-0-lite", validate_only=True
        )

    assert result["corrected_args"] == {}
    assert result["model_used"] == "doubao-seedream-5-0-lite"


@pytest.mark.asyncio
async def test_resubmitting_with_corrected_args_reaches_the_same_model():
    """The point of pinning: the billed call runs what the preflight validated.

    Routing is deterministic today (`select_model` scores static adapter attributes and
    breaks ties on adapter_id), so this holds without pinning too — but only while
    nothing else in the request changes. Pinning is what keeps it true once corrections
    start touching parameters that feed `_score` (quality_tier, reference media).
    """
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        first = await audio_service.generate_audio(
            text="你好", model="auto", validate_only=True
        )
        args = {"text": "你好", "model": "auto", **first["corrected_args"]}
        second = await audio_service.generate_audio(**args, validate_only=True)

    assert second["model_used"] == first["model_used"]
    assert second["payload"] == first["payload"]


@pytest.mark.asyncio
async def test_validate_only_returns_the_payload_the_real_call_would_send():
    """Not a summary of the request — the actual built payload, so a host can show it."""
    registry = _real_registry()
    adapter = registry.get("doubao-seedance-2-0")

    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="海浪", model="doubao-seedance-2-0", duration_seconds=5, validate_only=True
        )

    from cfgpu_mcp.tool_registry import GenerateVideoInput

    expected = adapter.build_payload(
        GenerateVideoInput(prompt="海浪", model="doubao-seedance-2-0", duration_seconds=5)
    )
    assert result["payload"] == expected


@pytest.mark.asyncio
async def test_validate_only_echoes_request_id_but_not_caption():
    """Same asymmetry CFGPUError already encodes: a preflight produced no artifact,
    so there is nothing for the label to name — but the correlation handle still joins
    this preflight back to the caller's request."""
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await image_service.generate_image(
            prompt="猫",
            model="doubao-seedream-5-0-lite",
            request_id="r-1",
            caption="第一版",
            validate_only=True,
        )

    assert result["request_id"] == "r-1"
    assert "caption" not in result


# ── Media slots: count is checked, content is not ────────────────────────────
#
# A host that runs this preflight before human approval calls it from a stage where its
# media slots still hold its own material handles — the reference is only resolved to a
# real URL on the way out to us, after approval. So the values arriving here are opaque
# identifiers, and validating them as URLs would reject every call that carries an asset:
# all of them legal, all of them about to be resolved seconds later. What *is* checkable
# without understanding the value is how many there are, and that is a real per-model
# constraint — so count is enforced and content is passed through untouched.
#
# The pair below is the contract, and it only means something as a pair: the first test
# alone would be satisfied by validating nothing at all.

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slot",
    [{"first_frame": "m_c079468f"}, {"reference_images": ["m_aaa", "m_bbb"]}],
    ids=["single-valued slot", "list-valued slot"],
)
async def test_media_slot_values_reach_the_payload_untouched(slot):
    """Whatever the caller put in a media slot is what the payload carries.

    No URL parsing, no scheme check, no existence probe — those would all fail on an
    opaque handle. Asserting on the built payload rather than just `validated` is the
    point: it shows the value survived `build_payload` rather than being dropped.
    """
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="镜头缓慢推近",
            model="doubao-seedance-2-0",
            duration_seconds=5,
            validate_only=True,
            **slot,
        )

    assert result["validated"] is True
    (value,) = slot.values()
    handles = [value] if isinstance(value, str) else value
    flat = json.dumps(result["payload"], ensure_ascii=False)
    for handle in handles:
        assert handle in flat, f"{handle} did not survive into the payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("count, ok", [(9, True), (10, False)], ids=["at limit", "over limit"])
async def test_media_slot_count_is_enforced_on_opaque_handles(count, ok):
    """The other half: `max_reference_images` still bites, and it bites on handles.

    Counting needs no knowledge of what a value *is*, which is exactly why this is the
    one media check a preflight can make honestly. `doubao-seedance-2-0` caps at 9
    (`SeedanceVideoAdapter.supports`, not the base — the ceiling is per-adapter).
    """
    refs = [f"m_{i}" for i in range(count)]
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        call = video_service.generate_video(
            prompt="x",
            model="doubao-seedance-2-0",
            reference_images=refs,
            duration_seconds=5,
            validate_only=True,
        )
        if ok:
            assert (await call)["validated"] is True
        else:
            with pytest.raises(CFGPUError) as exc:
                await call
            assert exc.value.error_type == "invalid_params"
            assert "at most 9" in exc.value.user_message


# ── Equivalence with the billed path ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_only_rejects_what_supports_would_reject():
    """A per-model constraint (duration ceiling) must fail here exactly as it does live,
    with the same error_type — the caller reads this identically either way."""
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        with pytest.raises(CFGPUError) as exc:
            await video_service.generate_video(
                prompt="海浪",
                model="doubao-seedance-2-0",
                duration_seconds=25,  # ceiling is 15
                validate_only=True,
            )

    assert exc.value.error_type == "invalid_params"
    assert exc.value.model_id  # stamped by the service layer, as on the billed path


@pytest.mark.asyncio
async def test_validate_only_corrects_an_unsupported_resolution():
    """A safe enum fallback is applied to the payload and made reproducible."""
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await video_service.generate_video(
            prompt="海浪",
            model="doubao-seedance-2-0-fast",  # 480p/720p only
            resolution="4k",
            validate_only=True,
        )

    assert result["corrected_args"] == {"resolution": "720p"}
    assert result["payload"]["resolution"] == "720p"


@pytest.mark.asyncio
async def test_validate_only_runs_build_payload():
    """Why the short-circuit sits *after* build_payload rather than after the router.

    Several adapters do their real per-model checking inside build_payload, so a
    preflight that stopped at the router would pass requests the billed call rejects —
    the one failure mode that makes a preflight worse than none.
    """
    adapter = MagicMock()
    adapter.adapter_id = "x"
    adapter.model_name = "x"
    adapter.task_type = "image"
    adapter.is_async = False
    adapter.cost_tier = 1
    adapter.speed_tier = 1
    adapter.validation_corrections.return_value = {}
    adapter.supports.return_value = (True, "")
    adapter.build_payload.side_effect = CFGPUError(
        error_type="invalid_params", user_message="adapter-level rejection"
    )
    router = MagicMock()
    router.resolve.return_value = adapter

    with (
        patch("cfgpu_mcp.config.get_registry", MagicMock(return_value=MagicMock())),
        patch("cfgpu_mcp.config.get_task_repository", AsyncMock()),
        patch("cfgpu_mcp.config.client_for", MagicMock(return_value=_client())),
        patch("cfgpu_mcp.router.ModelRouter", MagicMock(return_value=router)),
    ):
        with pytest.raises(CFGPUError, match="adapter-level rejection"):
            await image_service.generate_image(prompt="x", validate_only=True)

    adapter.build_payload.assert_called_once()


# ── Fleet-wide model ranges and safe fallbacks ───────────────────────────────

@pytest.mark.parametrize(
    "model,resolution,aspect_ratio,expected",
    [
        ("doubao-seedream-4-0", "1K", "1:1", {"resolution": "2K"}),
        ("doubao-seedream-4-5", "1K", "1:1", {"resolution": "2K"}),
        ("doubao-seedream-5-0-lite", "1K", "1:1", {"resolution": "2K"}),
        ("doubao-seedream-5-0-pro", "4K", "1:1", {"resolution": "2K"}),
        ("cf-image-2", "3K", "21:9", {"resolution": "2K", "aspect_ratio": "1:1"}),
        ("cf2", "3K", "3:2", {"resolution": "2K", "aspect_ratio": "1:1"}),
        ("cf-pro", "3K", "3:2", {"resolution": "2K", "aspect_ratio": "1:1"}),
        ("cf-pro-official", "3K", "3:2", {"resolution": "2K", "aspect_ratio": "1:1"}),
        ("cf-pro-premium", "3K", "3:2", {"resolution": "2K", "aspect_ratio": "1:1"}),
    ],
)
def test_every_image_model_reports_safe_enum_fallbacks(model, resolution, aspect_ratio, expected):
    registry = _real_registry()
    req = GenerateImageInput(
        prompt="x", model=model, resolution=resolution, aspect_ratio=aspect_ratio
    )
    adapter = ModelRouter(registry).resolve(req, for_validation=True)

    result = validate_request(adapter, req)

    assert result["corrected_args"] == expected


@pytest.mark.parametrize(
    "model,extra,expected_resolution",
    [
        ("wan-video", {}, "1080p"),
        ("wan-video-fast", {}, "720p"),
        ("doubao-seedance-1-5-pro", {}, "1080p"),
        ("doubao-seedance-2-0", {}, None),
        ("doubao-seedance-2-0-fast", {}, "720p"),
        ("doubao-seedance-2-0-mini", {}, "720p"),
        ("doubao-seedance-2-5", {}, "1080p"),
        ("happyhorse-1.0-t2v", {}, "1080p"),
        ("happyhorse-1.0-i2v", {"first_frame": "m_image"}, "1080p"),
        ("happyhorse-1.0-r2v", {"reference_images": ["m_image"]}, "1080p"),
        ("happyhorse-1.0-video-edit", {"reference_videos": ["m_video"]}, "1080p"),
        ("kling-video-o1", {}, "1080p"),
        ("kling-v3-omni", {}, "1080p"),
        ("wan2.7-t2v", {}, "1080p"),
        ("wan2.7-i2v", {"first_frame": "m_image"}, "1080p"),
        ("wan2.7-r2v", {"reference_images": ["m_image"]}, "1080p"),
        ("wan2.7-videoedit", {"reference_videos": ["m_video"]}, "1080p"),
        ("wan2.6-t2v", {}, "1080p"),
        ("wan2.6-i2v", {"first_frame": "m_image"}, "1080p"),
        ("wan2.6-r2v", {"reference_images": ["m_image"]}, "1080p"),
        ("cf-imagine-video", {}, "1080p"),
        ("cf-imagine-video-1.5", {}, "1080p"),
        ("cfdream/minimax-h3", {}, "1080p"),
        ("cfdream/minimax-h3-r2v", {"reference_images": ["m_image"]}, "1080p"),
        ("submodel/minimax-h3", {"aspect_ratio": "16:9"}, "1080p"),
    ],
)
def test_every_video_model_checks_4k_against_its_resolution_set(
    model, extra, expected_resolution
):
    registry = _real_registry()
    req = GenerateVideoInput(prompt="x", model=model, resolution="4k", **extra)
    adapter = ModelRouter(registry).resolve(req, for_validation=True)

    result = validate_request(adapter, req)

    if expected_resolution is None:
        assert "resolution" not in result["corrected_args"]
    else:
        assert result["corrected_args"]["resolution"] == expected_resolution


@pytest.mark.parametrize(
    "model,extra",
    [
        ("wan2.6-t2v", {}),
        ("wan2.6-r2v", {"reference_images": ["m_image"]}),
        ("wan2.7-t2v", {}),
        ("wan2.7-r2v", {"reference_images": ["m_image"]}),
        ("wan2.7-videoedit", {"reference_videos": ["m_video"]}),
    ],
)
def test_wan_2_6_and_2_7_fall_back_to_supported_resolution_and_ratio(model, extra):
    registry = _real_registry()
    req = GenerateVideoInput(
        prompt="x", model=model, resolution="480p", aspect_ratio="21:9", **extra
    )
    adapter = ModelRouter(registry).resolve(req, for_validation=True)

    result = validate_request(adapter, req)

    assert result["corrected_args"]["resolution"] == "720p"
    assert result["corrected_args"]["aspect_ratio"] == "16:9"
    assert result["payload"]["parameters"]["ratio"] == "16:9"


@pytest.mark.parametrize("model", ["wan2.6-i2v", "wan2.7-i2v"])
def test_wan_i2v_ignores_aspect_ratio_because_api_has_no_ratio_parameter(model):
    registry = _real_registry()
    req = GenerateVideoInput(
        prompt="x",
        model=model,
        first_frame="m_image",
        aspect_ratio="21:9",
    )
    adapter = ModelRouter(registry).resolve(req, for_validation=True)

    result = validate_request(adapter, req)

    assert "aspect_ratio" not in result["corrected_args"]
    assert "ratio" not in result["payload"]["parameters"]


@pytest.mark.parametrize(
    "model,voice",
    [
        ("seed-tts-2.0", "zh_female_xiaohe_uranus_bigtts"),
        ("MiniMax/speech-2.8-hd", "male-qn-qingse"),
        ("MiniMax/speech-2.8-turbo", "Santa_Claus"),
    ],
)
@pytest.mark.asyncio
async def test_audio_validate_only_accepts_exact_system_voice_ids(model, voice):
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await audio_service.generate_audio(
            text="hello", model=model, voice=voice, validate_only=True
        )

    assert result["validated"] is True


@pytest.mark.parametrize(
    "model,voice",
    [
        ("seed-tts-2.0", "male-qn-qingse"),
        ("MiniMax/speech-2.8-hd", "zh_female_xiaohe_uranus_bigtts"),
        ("MiniMax/speech-2.8-turbo", "Santa_Claus_typo"),
    ],
)
@pytest.mark.asyncio
async def test_audio_validate_only_rejects_voice_outside_selected_catalog(model, voice):
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c, pytest.raises(CFGPUError) as exc:
        await audio_service.generate_audio(
            text="hello", model=model, voice=voice, validate_only=True
        )

    assert exc.value.error_type == "invalid_params"
    assert "system voice catalog" in exc.value.user_message


@pytest.mark.asyncio
async def test_audio_validate_only_strips_voice_surrounding_whitespace():
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await audio_service.generate_audio(
            text="hello",
            model="MiniMax/speech-2.8-turbo",
            voice=" Santa_Claus ",
            validate_only=True,
        )

    assert result["validated"] is True
    assert result["corrected_args"] == {"voice": "Santa_Claus"}
    assert result["payload"]["input"]["voice_setting"]["voice_id"] == "Santa_Claus"


@pytest.mark.parametrize(
    "voice,expected_model",
    [
        ("zh_female_xiaohe_uranus_bigtts", "seed-tts-2.0"),
        ("male-qn-qingse", "MiniMax/speech-2.8-turbo"),
    ],
)
@pytest.mark.asyncio
async def test_audio_auto_routes_only_to_a_model_owning_the_voice(voice, expected_model):
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await audio_service.generate_audio(
            text="hello", model="auto", voice=voice, validate_only=True
        )

    assert result["model_used"] == expected_model
    assert result["corrected_args"]["model"] == expected_model


@pytest.mark.asyncio
async def test_audio_pcm_falls_back_and_reports_the_effective_format():
    a, b, c = _patched_real_registry(_client(), AsyncMock())
    with a, b, c:
        result = await audio_service.generate_audio(
            text="hello",
            model="MiniMax/speech-2.8-hd",
            audio_format="pcm",
            validate_only=True,
        )

    assert result["corrected_args"] == {"audio_format": "mp3"}
    assert result["payload"]["input"]["audio_setting"]["format"] == "mp3"


# ── The MCP layer ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_keeps_the_payload_out_of_the_llm_content():
    """The built payload is for the host rendering the approval, not for the model.

    It rides the existing ``structured_keys=("usage", "payload")`` split, so it reaches
    ``ToolMessage.artifact`` and never the model context — the same treatment every
    other ``payload`` gets. The verdict itself stays in ``content``: when a host lets
    the model see the preflight result at all, "validated" is the part it needs.
    """
    import json

    from cfgpu_mcp.server import mcp

    preflight = {
        "validated": True,
        "model_used": "doubao-seedance-2-0",
        "task_type": "video",
        "is_async": True,
        "cost_tier": 3,
        "speed_tier": 2,
        "corrected_args": {"model": "doubao-seedance-2-0"},
        "payload": {"model": "doubao-seedance-2-0-260128", "content": []},
    }
    expected_payload = dict(preflight["payload"])  # split_structured pops in place
    with patch(
        "cfgpu_mcp.service.video.generate_video", AsyncMock(return_value=preflight)
    ):
        out = await mcp.call_tool(
            "generate_video", {"prompt": "海浪", "validate_only": True}
        )

    assert out.structuredContent["payload"] == expected_payload
    content = json.loads(out.content[0].text)
    assert content["validated"] is True
    assert content["model_used"] == "doubao-seedance-2-0"
    assert "payload" not in content
    # Stays in content, unlike `payload`: this is the instruction to act on, and both
    # the host rewriting the call and the model reading the result need to see it.
    assert content["corrected_args"] == {"model": "doubao-seedance-2-0"}
    # No artifact was produced, so nothing may claim one — `annotate_artifact` keys on
    # urls/inline_media and must leave a preflight alone, or the model reads a
    # never-submitted request as a finished generation.
    assert "artifact" not in content
    assert content.get("status") != "Success"


# ── Off by default ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_is_off_and_still_posts():
    """The flag is opt-in: an existing caller that never heard of it is unaffected."""
    client = MagicMock()
    client.post = AsyncMock(return_value={"data": [{"url": "https://cdn/img.jpg"}]})
    repo = AsyncMock()
    a, b, c = _patched_real_registry(client, repo)
    with a, b, c:
        await image_service.generate_image(prompt="猫", model="doubao-seedream-5-0-lite")

    client.post.assert_awaited_once()
