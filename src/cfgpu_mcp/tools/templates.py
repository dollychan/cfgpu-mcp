from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import template as template_service
from cfgpu_mcp.tool_registry import TemplateKind, TemplateMedia, split_structured

#: The client-facing display text rides structuredContent under this key. A host that
#: merges structuredContent into its rendered result (DeerFlow's MessageStreamMiddleware
#: does ``{**content, **structured}``) then shows it the same way it shows any tool's
#: prose result, while the model keeps reading the plain JSON in ``content``.
DISPLAY_KEY = "message"
_SUMMARY_MAX_CHARS = 120


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_search_message(result: dict[str, Any]) -> str:
    """Render a search result as one Markdown text for the end user.

    Only what a person needs to pick a template: title, a one-line summary and the
    example cover. Covers are third-party examples, so the credit and "reference only"
    travel with every image. ``available: false`` is a broken library, not an empty
    result, and says so.
    """
    if not result.get("available", True):
        return "模板库暂时不可用，请稍后再试。"
    cards = result.get("results") or []
    if not cards:
        return "没有找到匹配的模板。"
    blocks = []
    for i, card in enumerate(cards, 1):
        lines = [f"### {i}. {card.get('title') or card.get('id', '')}"]
        summary = card.get("summary") or ""
        if summary:
            lines.append(_clip(summary, _SUMMARY_MAX_CHARS))
        cover = card.get("cover")
        if cover and cover.get("url"):
            credit = cover.get("author") or cover.get("origin")
            note = f"（来源 {credit}，仅供参考）" if credit else "（仅供参考）"
            lines.append(f"示例：![示例]({cover['url']}){note}")
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    if result.get("next_cursor") is not None:
        text += f"\n\n共 {result.get('total', len(cards))} 个结果，以上为前 {len(cards)} 个。"
    return text


def present_search_result(result: Any) -> Any:
    """Attach the display text to structuredContent; the model-facing JSON is unchanged.

    Error dicts pass through untouched (``split_structured`` leaves them alone), and a
    rendering failure drops the display text rather than the result.
    """
    if not isinstance(result, dict) or result.get("error"):
        return result
    try:
        message = render_search_message(result)
    except Exception:  # noqa: BLE001 - display is optional, the result is not
        return result
    return split_structured({**result, DISPLAY_KEY: message}, structured_keys=(DISPLAY_KEY,))


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_templates(
        query: Optional[str] = None,
        kind: Optional[TemplateKind] = None,
        media: Optional[TemplateMedia] = None,
        genres: Optional[list[str]] = None,
        scenes: Optional[list[str]] = None,
        styles: Optional[list[str]] = None,
        model: Optional[str] = None,
        limit: int = 10,
        cursor: int = 0,
    ) -> dict:
        """Search the shared library of official prompt templates, worked cases and style directions; returns cards, so call get_template for the full content."""
        try:
            result = await template_service.search_templates(
                query=query,
                kind=kind,
                media=media,
                genres=genres,
                scenes=scenes,
                styles=styles,
                model=model,
                limit=limit,
                cursor=cursor,
            )
        except Exception as e:
            return tool_error_dict(e)
        return present_search_result(result)

    @mcp.tool()
    async def get_template(template_id: str, version: Optional[int] = None) -> dict:
        """Get one template's full content by id; fill each [placeholder] with the user's specifics and keep the user's own decisions."""
        try:
            return await template_service.get_template(template_id, version)
        except Exception as e:
            return tool_error_dict(e)
