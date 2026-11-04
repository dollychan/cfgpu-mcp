"""Pydantic validation errors reach the caller as plain field messages.

``str(ValidationError)`` ends every entry with ``[type=…, input_value=…, input_type=…]``
and "For further information visit https://errors.pydantic.dev/…". An LLM caller reads
the whole string as advice: the link is a dead end it cannot open, and ``input_value``
echoes back the very argument it just wrote (sometimes kilobytes of prompt). Only the
field path and the message say what to fix.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError, field_validator

from cfgpu_mcp import server
from cfgpu_mcp.errors import format_validation_error, tool_error_dict


class _Spec(BaseModel):
    index: int
    size: list[int]

    @field_validator("index")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("index must be >= 0")
        return v


def _error() -> ValidationError:
    with pytest.raises(ValidationError) as info:
        _Spec.model_validate({"index": -1, "size": "x" * 500})
    return info.value


def test_the_formatted_message_keeps_path_and_reason_only():
    text = format_validation_error(_error())
    assert "index: index must be >= 0" in text
    assert "size:" in text
    assert "errors.pydantic.dev" not in text
    assert "input_value" not in text and "x" * 50 not in text
    assert "Value error, " not in text, "pydantic's own prefix adds nothing for the reader"


def test_tool_error_dict_reports_a_validation_error_as_invalid_params():
    result = tool_error_dict(_error())
    assert result["error"] is True and result["error_type"] == "invalid_params"
    assert "errors.pydantic.dev" not in result["message"]


@pytest.mark.asyncio
async def test_argument_validation_on_a_validate_only_call_has_no_pydantic_link():
    """FastMCP validates the arguments before the tool body runs, so ``tool_error_dict``
    never sees this one; the server hook has to clean it up instead."""
    args = {"prompt": "x", "images": ["https://e/a.png"], "regions": [{"image_index": -1, "box": [0, 0, 1, 1]}], "validate_only": True}
    with pytest.raises(ToolError) as info:
        await server.mcp.call_tool("understand_vision", args)
    message = str(info.value)
    assert "errors.pydantic.dev" not in message and "input_value" not in message
    assert "regions.0.image_index" in message and "image_index=-1 must be >= 0" in message
    assert message.startswith("Error executing tool understand_vision:")
