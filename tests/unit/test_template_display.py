"""search_templates' client-facing display text (structuredContent ``message``).

The model keeps reading the plain JSON in ``content``; the rendered Markdown rides
structuredContent so a host can show it without the model ever paying for it.
"""

from __future__ import annotations

import json

from mcp.types import CallToolResult

from cfgpu_mcp.tools.templates import DISPLAY_KEY, present_search_result, render_search_message

_COVER = {
    "url": "https://assets.example.com/covers/case-a/1.jpg",
    "usage": "reference-only",
    "origin": "someone/repo case 1",
    "author": "@someone",
    "source_url": "https://x.example/1",
}


def _result(cards, **extra):
    base = {"available": True, "total": len(cards), "next_cursor": None, "results": cards}
    base.update(extra)
    return base


def _card(title="茶叶棚拍", summary="极简棚拍，柔光。", cover=_COVER, **extra):
    return {"id": "case-a", "version": 1, "title": title, "summary": summary, "cover": cover, **extra}


def test_display_rides_structured_content_and_content_stays_plain_json():
    result = _result([_card(), _card(title="第二个", cover=None)])
    out = present_search_result(result)

    assert isinstance(out, CallToolResult)
    content = json.loads(out.content[0].text)
    assert DISPLAY_KEY not in content  # the model never pays for the display text
    assert content["results"] == result["results"]
    message = out.structuredContent[DISPLAY_KEY]
    assert "### 1. 茶叶棚拍" in message and "### 2. 第二个" in message
    assert f"![示例]({_COVER['url']})" in message


def test_every_cover_carries_its_credit_and_reference_only():
    message = render_search_message(_result([_card()]))
    assert "@someone" in message and "仅供参考" in message


def test_card_without_cover_has_no_example_line():
    assert "示例" not in render_search_message(_result([_card(cover=None)]))


def test_long_summary_is_clipped_to_one_line():
    message = render_search_message(_result([_card(summary="长" * 300 + "\n第二行")]))
    assert "…" in message and "第二行" not in message


def test_more_results_are_announced():
    message = render_search_message(_result([_card()], total=12, next_cursor=1))
    assert "共 12 个结果" in message


def test_unavailable_is_not_reported_as_empty():
    unavailable = {"available": False, "reason": "boom", "results": []}
    assert "不可用" in render_search_message(unavailable)
    assert "没有找到" in render_search_message(_result([]))


def test_error_dict_passes_through_untouched():
    err = {"error": True, "message": "bad kind"}
    assert present_search_result(err) is err


def test_render_failure_drops_only_the_display(monkeypatch):
    import cfgpu_mcp.tools.templates as mod

    monkeypatch.setattr(mod, "render_search_message", lambda r: 1 / 0)
    result = _result([_card()])
    assert present_search_result(result) is result
