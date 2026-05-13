import pytest
from cfgpu_mcp.service.model import _split_sections, _merge_cards


BASE_CARD = """# WAN 2.0

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan-video` |
| 成本档位 | 3/5 |

## 参数说明

参数文档内容。

## 约束与限制

时长 4-15 秒。
"""

VARIANT_CARD = """---
card_base: wan-2-0
---

# WAN 2.0 Fast

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan-video-fast` |
| 成本档位 | 2/5 |

## 与 WAN 2.0 的关系

速度更快，成本更低。
"""


def test_split_sections_finds_all_sections():
    sections = _split_sections(BASE_CARD)
    assert "## 基本信息" in sections
    assert "## 参数说明" in sections
    assert "## 约束与限制" in sections


def test_merged_contains_base_params_section():
    merged = _merge_cards(BASE_CARD, VARIANT_CARD)
    assert "参数文档内容" in merged


def test_variant_section_overrides_base():
    merged = _merge_cards(BASE_CARD, VARIANT_CARD)
    assert "wan-video-fast" in merged
    assert "2/5" in merged
    # Base value replaced
    assert "3/5" not in merged


def test_variant_only_section_appended():
    merged = _merge_cards(BASE_CARD, VARIANT_CARD)
    assert "与 WAN 2.0 的关系" in merged
    assert "速度更快" in merged


def test_base_section_kept_when_variant_does_not_override():
    merged = _merge_cards(BASE_CARD, VARIANT_CARD)
    assert "约束与限制" in merged
    assert "时长 4-15 秒" in merged


def test_no_card_base_returns_card_as_is():
    plain_card = "# Plain Model\n\n## 参数\n\nsome params\n"
    merged = _merge_cards(plain_card, plain_card)
    assert "Plain Model" in merged
