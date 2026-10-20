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


def _cover_base_url() -> Optional[str]:
    from cfgpu_mcp.config import get_settings

    base = get_settings().public_http.public_base_url
    return base.rstrip("/") if base else None


def _present_cover(item: dict[str, Any], base: Optional[str]) -> None:
    """Turn the stored cover path into a public URL, or drop it when none can be built.

    Covers are third-party examples: the credit and ``usage: reference-only`` travel
    with the URL so the caller can show them, and a caller must not feed the image to
    generation as if the library granted it.
    """
    cover = item.get("cover")
    if not cover:
        return
    if not base:
        item["cover"] = None
        return
    item["cover"] = {
        "url": f"{base}/{cover['path']}",
        "usage": cover["usage"],
        "origin": cover["origin"],
        "author": cover.get("author"),
        "source_url": cover.get("source_url"),
    }


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
    result = index.search(
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
    base = _cover_base_url()
    for card in result["results"]:
        _present_cover(card, base)
    return result


async def get_template(template_id: str, version: Optional[int] = None) -> dict[str, Any]:
    try:
        index = get_index()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(exc)
    data = index.get(template_id, version)
    _present_cover(data, _cover_base_url())
    return data
