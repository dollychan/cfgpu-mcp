"""Service layer for the official template library (``search_templates`` / ``get_template``).

Shared by every exposure (MCP, Mode B dispatcher, LangGraph tools). Read-only and
identity-free: nothing here knows who is asking (D-IR5).
"""

from __future__ import annotations

from typing import Any, Optional

from cfgpu_mcp.template_library import get_index

_KINDS = ("case", "style", "template")
_MEDIA = ("image", "video", "audio")


def _public_model_name(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    from cfgpu_mcp.config import get_registry

    try:
        return get_registry().get(model).model_name
    except KeyError as exc:
        raise ValueError(f"unknown model {model!r}; omit model to search without a model preference") from exc


def _unavailable(exc: Exception) -> dict[str, Any]:
    # "Unavailable" is not an empty result: a caller must not conclude nothing matched.
    return {"available": False, "reason": f"template library could not be loaded: {exc}", "results": []}


async def search_templates(
    query: Optional[str] = None,
    kind: Optional[str] = None,
    media: Optional[str] = None,
    genres: Optional[list[str]] = None,
    scenes: Optional[list[str]] = None,
    styles: Optional[list[str]] = None,
    model: Optional[str] = None,
    limit: int = 10,
    cursor: int = 0,
) -> dict[str, Any]:
    from cfgpu_mcp.tool_registry import fold_choice

    # Mode B / LangGraph callers reach here without the schema, so fold spelling here too.
    kind = fold_choice(kind, _KINDS)
    media = fold_choice(media, _MEDIA)
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"kind must be one of {list(_KINDS)}")
    if media is not None and media not in _MEDIA:
        raise ValueError(f"media must be one of {list(_MEDIA)}")
    model_name = _public_model_name(model)
    try:
        index = get_index()
    except Exception as exc:  # noqa: BLE001 - reported to the caller as unavailable
        return _unavailable(exc)
    return index.search(
        query=query,
        kind=kind,
        media=media,
        genres=genres,
        scenes=scenes,
        styles=styles,
        model=model_name,
        limit=limit,
        cursor=cursor,
    )


async def get_template(template_id: str, version: Optional[int] = None) -> dict[str, Any]:
    try:
        index = get_index()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(exc)
    return index.get(template_id, version)
