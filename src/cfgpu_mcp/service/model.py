from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MODELS_DIR = Path(__file__).parent.parent / "models"


def _split_sections(markdown: str) -> dict[str, str]:
    """Split markdown into {section_title: section_content} by ## headings."""
    sections: dict[str, str] = {}
    current_title = "__preamble__"
    current_lines: list[str] = []
    for line in markdown.splitlines(keepends=True):
        if line.startswith("## "):
            sections[current_title] = "".join(current_lines)
            current_title = line.rstrip()
            current_lines = [line]
        else:
            current_lines.append(line)
    sections[current_title] = "".join(current_lines)
    return sections


def _merge_cards(base_md: str, variant_md: str) -> str:
    """Merge variant card into base card: same-title sections override, new sections append."""
    base_sections = _split_sections(base_md)
    variant_sections = _split_sections(variant_md)

    # Strip YAML frontmatter from variant preamble (--- ... ---)
    variant_preamble = variant_sections.get("__preamble__", "")
    variant_preamble = re.sub(r"^---.*?---\s*", "", variant_preamble, flags=re.DOTALL).lstrip()

    merged: dict[str, str] = {}
    for title, content in base_sections.items():
        if title == "__preamble__":
            # Keep base preamble (title line), replace with variant's if present
            merged[title] = variant_preamble if variant_preamble else content
        elif title in variant_sections:
            merged[title] = variant_sections[title]
        else:
            merged[title] = content

    # Append variant-only sections
    for title, content in variant_sections.items():
        if title not in merged and title != "__preamble__":
            merged[title] = content

    return "".join(merged.values())


async def list_models(task_type: str | None = None) -> list[dict[str, Any]]:
    from cfgpu_mcp.config import get_registry
    registry = get_registry()
    adapters = registry.list_all(task_type=task_type)
    return [
        {
            "adapter_id":     a.adapter_id,
            "display_name":   a.display_name,
            "cfgpu_model_id": a.cfgpu_model_id,
            "task_type":      a.task_type,
            "capabilities":   sorted(a.capabilities),
            "cost_tier":      a.cost_tier,
            "speed_tier":     a.speed_tier,
            "is_async":       a.is_async,
        }
        for a in adapters
    ]


async def get_model_card(model_name: str) -> str:
    from cfgpu_mcp.config import get_registry

    registry = get_registry()
    adapter = registry.get(model_name)
    model_path = _MODELS_DIR / adapter.adapter_id

    card_file = model_path / "card.md"
    if not card_file.exists():
        return f"# {adapter.display_name}\n\nNo model card available."

    card_text = card_file.read_text()

    if adapter.card_base:
        base_card_file = _MODELS_DIR / adapter.card_base / "card.md"
        if base_card_file.exists():
            return _merge_cards(base_card_file.read_text(), card_text)

    return card_text
