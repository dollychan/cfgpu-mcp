"""Safe model-facing projection and resolution for canvas elements."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from cfgpu_mcp.context import get_project_id

if TYPE_CHECKING:
    from cfgpu_mcp.client.canvas_repo import CanvasRepository


_PAGE_SIZE = 20
_MEDIA_TYPES = frozenset({"image", "video", "audio", "ai_image", "ai_video"})
_KEY_PREFIXES = (
    "agent-artifacts/", "agent/", "compose/", "generation/", "icon/", "materials/", "skills/", "upload/",
)
_NUMERIC_LAYOUT_FIELDS = ("x", "y", "width", "height", "rotation", "opacity")
_BOOLEAN_LAYOUT_FIELDS = ("visible", "locked")


class CanvasAccessError(RuntimeError):
    """A short, safe error message suitable for an MCP tool result."""


def _payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise CanvasAccessError("canvas payload is malformed") from exc
        if isinstance(parsed, dict):
            return parsed
    raise CanvasAccessError("canvas payload is malformed")


def _key_is_allowed(payload: dict) -> bool:
    key = payload.get("ossKey")
    return isinstance(key, str) and key.startswith(_KEY_PREFIXES)


def _compact(row: dict) -> dict:
    payload = _payload(row.get("payload_json"))
    item = {
        "id": row["element_id"],
        "type": row["type"],
        "version": row["version"],
        "zIndex": row["z_index"],
        "frameId": row["frame_id"],
    }
    layout = {
        key: payload[key]
        for key in _NUMERIC_LAYOUT_FIELDS
        if isinstance(payload.get(key), (int, float)) and not isinstance(payload[key], bool)
    }
    layout.update({key: payload[key] for key in _BOOLEAN_LAYOUT_FIELDS if isinstance(payload.get(key), bool)})
    if layout:
        item["layout"] = layout
    for key in ("fileName", "status"):
        if isinstance(payload.get(key), (str, int, float, bool)):
            item[key] = payload[key]
    item["resolvable"] = row["type"] in _MEDIA_TYPES and _key_is_allowed(payload)
    return item


async def inspect_canvas(
    action: str,
    page: int | None = None,
    types: list[str] | None = None,
    element_id: str | None = None,
    version: int | None = None,
    *,
    repository: "CanvasRepository | Any | None" = None,
) -> dict:
    project_id = get_project_id()
    if not project_id:
        raise CanvasAccessError("current project is unavailable")
    if repository is None:
        from cfgpu_mcp.config import get_canvas_repository

        repository = await get_canvas_repository()
    if action == "list_elements":
        if page is None:
            page = 1
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise CanvasAccessError("page must be an integer greater than or equal to 1")
        normalized_types = None
        if types is not None:
            if not isinstance(types, list) or not all(isinstance(item, str) and item.strip() for item in types):
                raise CanvasAccessError("types must be a list of element types")
            normalized_types = [item.strip() for item in types]
        try:
            rows = await repository.list_elements(project_id, normalized_types, _PAGE_SIZE + 1, (page - 1) * _PAGE_SIZE)
            elements = [_compact(row) for row in rows[:_PAGE_SIZE]]
        except CanvasAccessError:
            raise
        except Exception as exc:
            raise CanvasAccessError("canvas database is unavailable") from exc
        return {"elements": elements, "page": page, "pageSize": _PAGE_SIZE, "hasMore": len(rows) > _PAGE_SIZE}
    if action == "resolve_element":
        if not isinstance(element_id, str) or not element_id.strip():
            raise CanvasAccessError("resolve_element requires an element_id")
        if isinstance(version, bool) or not isinstance(version, int):
            raise CanvasAccessError("resolve_element requires a version")
        try:
            row = await repository.get_element(project_id, element_id.strip())
        except Exception as exc:
            raise CanvasAccessError("canvas database is unavailable") from exc
        if row is None:
            raise CanvasAccessError("element was not found; call list_elements again")
        if row.get("version") != version:
            raise CanvasAccessError("element version changed; call list_elements again")
        payload = _payload(row.get("payload_json"))
        if row.get("type") not in _MEDIA_TYPES:
            raise CanvasAccessError("element type cannot be resolved")
        if not _key_is_allowed(payload):
            raise CanvasAccessError("element has no usable object key")
        result = {"id": row["element_id"], "type": row["type"], "version": row["version"], "urls": [payload["ossKey"]]}
        if isinstance(payload.get("fileName"), str) and payload["fileName"].strip():
            result["label"] = payload["fileName"].strip()
        return result
    raise CanvasAccessError("action must be list_elements or resolve_element")
