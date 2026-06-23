"""Guard against drift between the three layers that each re-declare tool params.

The same parameters (name + default) are declared in three places:
  1. tool_registry.py  — Pydantic GenerateImageInput / GenerateVideoInput (Mode B schema)
  2. tools/generate.py  — FastMCP wrapper signatures (the schema the MCP server advertises)
  3. service/*.py       — service function signatures

Only layer 2's defaults actually take effect on the MCP path (the wrapper forwards
explicit values, so layers 1 and 3 never fall back to their own defaults). If they
diverge, the MCP and Mode-B paths behave differently and nothing else catches it.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from pydantic_core import PydanticUndefined

from cfgpu_mcp.service import audio as audio_service
from cfgpu_mcp.service import image as image_service
from cfgpu_mcp.service import video as video_service
from cfgpu_mcp.service import vision as vision_service
from cfgpu_mcp.tool_registry import (
    GenerateAudioInput,
    GenerateImageInput,
    GenerateVideoInput,
    UnderstandVisionInput,
)

_CASES = [
    ("generate_image", GenerateImageInput, image_service.generate_image),
    ("generate_video", GenerateVideoInput, video_service.generate_video),
    ("generate_audio", GenerateAudioInput, audio_service.generate_audio),
    ("understand_vision", UnderstandVisionInput, vision_service.understand_vision),
]


def _mcp_input_schema(tool_name: str) -> dict:
    from cfgpu_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == tool_name).inputSchema


@pytest.mark.parametrize("tool_name, model, _svc", _CASES)
def test_mcp_schema_exposes_exactly_the_pydantic_fields(tool_name, model, _svc):
    """Every unified-schema field is exposed by the MCP wrapper, and no extras."""
    schema = _mcp_input_schema(tool_name)
    assert set(schema["properties"]) == set(model.model_fields), (
        f"{tool_name}: MCP wrapper params drifted from {model.__name__} fields"
    )


@pytest.mark.parametrize("tool_name, model, _svc", _CASES)
def test_mcp_schema_defaults_match_pydantic(tool_name, model, _svc):
    """The defaults the MCP server advertises must equal the Pydantic defaults."""
    schema = _mcp_input_schema(tool_name)
    props = schema["properties"]
    required = set(schema.get("required", []))

    for name, field in model.model_fields.items():
        if field.is_required():
            assert name in required, f"{tool_name}.{name} should be required in MCP schema"
            continue
        assert name not in required, f"{tool_name}.{name} should be optional in MCP schema"
        mcp_default = props[name].get("default", PydanticUndefined)
        assert mcp_default == field.default, (
            f"{tool_name}.{name}: MCP default {mcp_default!r} != "
            f"Pydantic default {field.default!r}"
        )


@pytest.mark.parametrize("tool_name, model, _svc", _CASES)
def test_mcp_schema_carries_pydantic_descriptions(tool_name, model, _svc):
    """Per-param descriptions are injected from the Pydantic models, not duplicated in
    the wrappers — so every described field must surface that exact text on the MCP schema."""
    from cfgpu_mcp.tool_registry import get_field_descriptions

    props = _mcp_input_schema(tool_name)["properties"]
    descriptions = get_field_descriptions(tool_name)
    assert descriptions, f"{tool_name}: expected some field descriptions"
    for name, expected in descriptions.items():
        assert props[name].get("description") == expected, (
            f"{tool_name}.{name}: MCP schema description out of sync with tool_registry"
        )


@pytest.mark.parametrize("tool_name, model, svc", _CASES)
def test_service_signature_defaults_match_pydantic(tool_name, model, svc):
    """The service function signature must agree with the Pydantic defaults too."""
    params = inspect.signature(svc).parameters
    for name, field in model.model_fields.items():
        assert name in params, f"{svc.__name__}: missing param '{name}'"
        p = params[name]
        if field.is_required():
            assert p.default is inspect.Parameter.empty, (
                f"{svc.__name__}.{name} should have no default (required field)"
            )
            continue
        assert p.default == field.default, (
            f"{svc.__name__}.{name}: service default {p.default!r} != "
            f"Pydantic default {field.default!r}"
        )
