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
