"""The ``model`` parameter of every generation / understand tool must advertise a
JSON-schema ``enum`` of the registry's real model ids — so no LLM client can emit a
hallucinated model_id (the reported ``qwen-3-vl-plus`` failure). Only the public
``cfgpu_model_id`` is exposed; the internal ``adapter_id`` must never leak. This must
hold identically across Mode A (MCP/FastMCP) and Mode B (Anthropic / OpenAI / LangGraph).
"""

from __future__ import annotations

import pytest

from cfgpu_mcp.config import get_registry
from cfgpu_mcp.tool_registry import (
    _TOOL_TASK_TYPE,
    apply_model_enum,
    get_anthropic_tools,
)

MODEL_TOOLS = sorted(_TOOL_TASK_TYPE)  # image / video / audio / understand tools


def _string_enum(model_prop: dict) -> list[str]:
    branch = next(b for b in model_prop["anyOf"] if b.get("type") == "string")
    return branch["enum"]


def _array_enum(model_prop: dict) -> list[str]:
    branch = next(b for b in model_prop["anyOf"] if b.get("type") == "array")
    return branch["items"]["enum"]


def _expected_ids(tool_name: str) -> list[str]:
    task_type = _TOOL_TASK_TYPE[tool_name]
    return sorted({a.cfgpu_model_id for a in get_registry().list_all(task_type=task_type)})


def _adapter_only_ids(tool_name: str) -> set[str]:
    task_type = _TOOL_TASK_TYPE[tool_name]
    adapters = get_registry().list_all(task_type=task_type)
    return {a.adapter_id for a in adapters} - {a.cfgpu_model_id for a in adapters}


# ── shared helper ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", MODEL_TOOLS)
def test_apply_model_enum_stamps_both_branches(tool_name):
    from cfgpu_mcp.tool_registry import _REGISTRY

    model_cls = dict(_REGISTRY)[tool_name]
    schema = apply_model_enum(model_cls.model_json_schema(), tool_name)
    prop = schema["properties"]["model"]
    expected = _expected_ids(tool_name)
    assert _string_enum(prop) == ["auto", *expected]
    assert _array_enum(prop) == expected  # "auto" is not a list element


def test_apply_model_enum_noop_for_non_model_tool():
    # list_models has no `model` param → nothing to stamp, returns schema untouched.
    from cfgpu_mcp.tool_registry import ListModelsInput

    schema = ListModelsInput.model_json_schema()
    assert apply_model_enum(schema, "list_models") is schema


def test_apply_model_enum_survives_registry_failure(monkeypatch):
    from cfgpu_mcp.tool_registry import GenerateImageInput

    def _boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr("cfgpu_mcp.config.get_registry", _boom)
    schema = apply_model_enum(GenerateImageInput.model_json_schema(), "generate_image")
    # Unconstrained (no enum) rather than an exception — startup / schema build is safe.
    string_branch = next(b for b in schema["properties"]["model"]["anyOf"] if b.get("type") == "string")
    assert "enum" not in string_branch


# ── all four exposure paths agree ────────────────────────────────────────────


def _all_path_enums(tool_name: str) -> dict[str, list[str]]:
    """Return {path: string-branch enum} for a tool across all four LLM-facing paths."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from cfgpu_mcp.agent.langgraph_tools import get_langgraph_tools
    from cfgpu_mcp.agent.openai_tools import get_openai_tools
    from cfgpu_mcp.server import mcp

    mcp_tool = next(t for t in mcp._tool_manager.list_tools() if t.name == tool_name)
    ant = next(t for t in get_anthropic_tools(tools=[tool_name]))
    oai = next(t for t in get_openai_tools(tools=[tool_name]))
    lg = convert_to_openai_tool(next(iter(get_langgraph_tools(tools=[tool_name]))))
    return {
        "mcp": _string_enum(mcp_tool.parameters["properties"]["model"]),
        "anthropic": _string_enum(ant["input_schema"]["properties"]["model"]),
        "openai": _string_enum(oai["function"]["parameters"]["properties"]["model"]),
        "langgraph": _string_enum(lg["function"]["parameters"]["properties"]["model"]),
    }


@pytest.mark.parametrize("tool_name", MODEL_TOOLS)
def test_all_paths_expose_same_cfgpu_model_ids(tool_name):
    pytest.importorskip("langchain_core")
    expected = ["auto", *_expected_ids(tool_name)]
    enums = _all_path_enums(tool_name)
    for path, enum in enums.items():
        assert enum == expected, f"{path} enum drifted for {tool_name}: {enum}"


@pytest.mark.parametrize("tool_name", MODEL_TOOLS)
def test_no_path_leaks_adapter_id(tool_name):
    pytest.importorskip("langchain_core")
    adapter_only = _adapter_only_ids(tool_name)
    if not adapter_only:
        pytest.skip("no adapter_id distinct from cfgpu_model_id for this task type")
    for path, enum in _all_path_enums(tool_name).items():
        assert not (set(enum) & adapter_only), f"{path} leaked adapter_id for {tool_name}"


def test_hallucinated_understand_model_excluded_everywhere():
    pytest.importorskip("langchain_core")
    for path, enum in _all_path_enums("understand_vision").items():
        assert "qwen-3-vl-plus" not in enum, path
