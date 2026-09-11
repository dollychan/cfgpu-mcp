from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.service import canvas as canvas_service


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def inspect_canvas(
        action: str,
        page: Optional[int] = None,
        types: Optional[list[str]] = None,
        element_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> dict:
        """Inspect media elements on the current project's canvas.

        Use this tool when the user asks what is on the canvas or identifies content
        by canvas position, such as row/column, left/right, top/bottom, or first/last.
        Do not claim that the canvas layout is unavailable before calling it.

        First use ``action="list_elements"``. ``page`` is one-based, ``types``
        optionally filters element types, and further pages should be read when
        ``hasMore`` is true. For positional lookup, use ``layout.y`` to group and
        order rows top-to-bottom, then ``layout.x`` to order each row left-to-right.

        Use ``action="resolve_element"`` only for ``resolvable=true`` elements, with
        the exact ``element_id`` and ``version`` returned by the list result. Pass the
        resolved media to a downstream presentation or vision tool as needed. If
        layout data is missing or ambiguous, report that rather than guess.
        """
        try:
            return await canvas_service.inspect_canvas(
                action, page, types, element_id, version
            )
        except canvas_service.CanvasAccessError as exc:
            return {"error": True, "message": str(exc)}
        except Exception:
            return {"error": True, "message": "canvas request failed"}
