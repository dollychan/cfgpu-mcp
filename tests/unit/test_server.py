import asyncio

import pytest

from cfgpu_mcp import server
from cfgpu_mcp.settings import Settings


def test_lifespan_is_wired_to_mcp():
    # FastMCP stores the lifespan on its low-level server.
    assert server.mcp._mcp_server.lifespan is not None


@pytest.mark.asyncio
async def test_lifespan_closes_resources_on_exit(monkeypatch):
    closed_on = []

    async def fake_close():
        closed_on.append(asyncio.get_running_loop())

    # _lifespan only closes shared resources under stdio (HTTP cleans up
    # elsewhere). Pin stdio so the ambient config.yaml's transport can't leak in.
    monkeypatch.setattr("cfgpu_mcp.config.close", fake_close)
    monkeypatch.setattr("cfgpu_mcp.config.get_settings", lambda: Settings(transport="stdio"))

    async with server._lifespan(server.mcp):
        body_loop = asyncio.get_running_loop()

    # close() runs exactly once, on the same loop as the lifespan body
    # (not a fresh loop from asyncio.run in an atexit hook).
    assert closed_on == [body_loop]


# ── ② model-enum injection (only model_name is advertised) ───────────────────


def _model_schema(tool_name: str) -> dict:
    for tool in server.mcp._tool_manager.list_tools():
        if tool.name == tool_name:
            return tool.parameters["properties"]["model"]
    raise AssertionError(f"tool {tool_name!r} not found")


def test_understand_model_enum_lists_only_model_names():
    from cfgpu_mcp.config import get_registry

    prop = _model_schema("understand_vision")
    string_branch = next(b for b in prop["anyOf"] if b.get("type") == "string")
    array_branch = next(b for b in prop["anyOf"] if b.get("type") == "array")

    expected_ids = sorted({a.model_name for a in get_registry().list_all(task_type="understand")})
    assert string_branch["enum"] == ["auto", *expected_ids]
    # "auto" is not a valid element inside the candidate-list form.
    assert array_branch["items"]["enum"] == expected_ids


def test_model_enum_never_exposes_internal_ids():
    """Only the canonical model_name is advertised; the internal adapter_id /
    cfgpu_model_id (e.g. 'gpt-image-2' / 'gpt-image-2' vs model_name 'cf-image-2')
    must not leak."""
    from cfgpu_mcp.config import get_registry

    for tool_name, task_type in (
        ("understand_vision", "understand"),
        ("generate_image", "image"),
        ("generate_video", "video"),
        ("generate_audio", "audio"),
    ):
        prop = _model_schema(tool_name)
        string_branch = next(b for b in prop["anyOf"] if b.get("type") == "string")
        advertised = set(string_branch["enum"]) - {"auto"}
        adapters = get_registry().list_all(task_type=task_type)
        model_names = {a.model_name for a in adapters}
        internal_only_ids = (
            {a.adapter_id for a in adapters} | {a.cfgpu_model_id for a in adapters}
        ) - model_names
        assert advertised == model_names
        assert advertised.isdisjoint(internal_only_ids)


def test_hallucinated_model_id_is_rejected_by_enum():
    """The reported failure: 'qwen-3-vl-plus' is not a real model. With the enum in
    place it is no longer a schema-valid value for the string branch."""
    prop = _model_schema("understand_vision")
    string_branch = next(b for b in prop["anyOf"] if b.get("type") == "string")
    assert "qwen-3-vl-plus" not in string_branch["enum"]


# ── ③ disabled_tools (config.yaml trims the exposed MCP surface) ─────────────


def _fresh_server():
    """A second FastMCP with the same tools, so tests can trim it without
    mutating the module-level singleton the other tests read."""
    from mcp.server.fastmcp import FastMCP

    from cfgpu_mcp.tools import generate, models, tasks, understand

    fresh = FastMCP("cfdream-test")
    for module in (generate, understand, tasks, models):
        module.register(fresh)
    return fresh


def _apply(monkeypatch, disabled_tools):
    monkeypatch.setattr(
        "cfgpu_mcp.config.get_settings",
        lambda: Settings(disabled_tools=disabled_tools),
    )
    fresh = _fresh_server()
    server._apply_disabled_tools(fresh)
    return {tool.name for tool in fresh._tool_manager.list_tools()}


def test_disabled_tools_are_unregistered(monkeypatch):
    names = _apply(monkeypatch, ["generate_audio", "understand_vision"])
    assert "generate_audio" not in names
    assert "understand_vision" not in names
    assert {"generate_image", "generate_video", "task_wait"} <= names


def test_no_disabled_tools_keeps_full_surface(monkeypatch):
    assert _apply(monkeypatch, None) == {t.name for t in _fresh_server()._tool_manager.list_tools()}


def test_unknown_disabled_tool_fails_at_startup(monkeypatch):
    """A typo would silently leave the tool exposed — the one thing the field
    exists to prevent — so it must not be tolerated."""
    with pytest.raises(ValueError, match="generate_gif"):
        _apply(monkeypatch, ["generate_gif"])


def test_disabled_tool_is_not_callable(monkeypatch):
    """Removed, not merely hidden from tools/list: a client that calls the name
    anyway gets 'unknown tool' rather than reaching a live handler."""
    monkeypatch.setattr(
        "cfgpu_mcp.config.get_settings", lambda: Settings(disabled_tools=["generate_audio"])
    )
    fresh = _fresh_server()
    server._apply_disabled_tools(fresh)
    assert fresh._tool_manager.get_tool("generate_audio") is None
