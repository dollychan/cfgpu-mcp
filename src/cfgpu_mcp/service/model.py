from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_MODELS_DIR = Path(__file__).parent.parent / "models"
_TASKS_PATH = Path(__file__).parent.parent / "capabilities" / "media_tasks.yaml"
_TASK_TYPES = frozenset({"image", "video", "audio", "understand"})


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
            "model_id":       a.model_name,
            "display_name":   a.display_name,
            "task_type":      a.task_type,
            "capabilities":   sorted(a.capabilities),
            "cost_tier":      a.cost_tier,
            "speed_tier":     a.speed_tier,
            "is_async":       a.is_async,
        }
        for a in adapters
    ]


def _load_task_catalog() -> tuple[int, dict[str, dict[str, str]]]:
    """Read and minimally validate the agent-facing canonical task vocabulary."""
    raw = yaml.safe_load(_TASKS_PATH.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("media_tasks.yaml must declare schema_version: 1")
    tasks = raw.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError("media_tasks.yaml must contain a non-empty tasks mapping")

    required = {"media_type", "name", "description", "prompt_guidance"}
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not isinstance(task, dict):
            raise ValueError("media_tasks.yaml contains an invalid task entry")
        if set(task) != required or not all(isinstance(task[key], str) and task[key] for key in required):
            raise ValueError(f"media_tasks.yaml task {task_id!r} has an invalid schema")
    return raw["schema_version"], tasks


def _load_profile(adapter_id: str, task_catalog: dict[str, dict[str, str]]) -> list[str]:
    """Read one model profile without ever consulting its card or adapter aliases."""
    profile_path = _MODELS_DIR / adapter_id / "profile.yaml"
    raw = yaml.safe_load(profile_path.read_text()) if profile_path.exists() else None
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "tasks"}:
        raise ValueError(f"model profile for {adapter_id!r} has an invalid schema")
    tasks = raw.get("tasks")
    if raw["schema_version"] != 1 or not isinstance(tasks, list) or not tasks:
        raise ValueError(f"model profile for {adapter_id!r} has an invalid task list")
    if any(not isinstance(task_id, str) for task_id in tasks) or len(tasks) != len(set(tasks)):
        raise ValueError(f"model profile for {adapter_id!r} has duplicate or invalid task IDs")
    unknown = set(tasks) - set(task_catalog)
    if unknown:
        raise ValueError(f"model profile for {adapter_id!r} references unknown tasks: {sorted(unknown)}")
    return tasks


async def list_model_profiles(
    media_type: str | None = None,
    required_tasks: list[str] | None = None,
    match: str = "all",
) -> dict[str, Any]:
    """List the agent-facing model catalog with canonical tasks only.

    This is intentionally separate from ``list_models``: the latter remains an
    operations/debugging API and contains adapter capabilities.  The profile catalog
    never reads model cards and never returns adapter capability names, payload fields,
    or parameter constraints.
    """
    if media_type is not None and media_type not in _TASK_TYPES:
        raise ValueError(f"unknown media_type {media_type!r}; expected one of {sorted(_TASK_TYPES)}")
    if match not in {"all", "any"}:
        raise ValueError("match must be 'all' or 'any'")

    catalog_version, task_catalog = _load_task_catalog()
    requested_tasks = set(required_tasks or [])
    unknown = requested_tasks - set(task_catalog)
    if unknown:
        raise ValueError(f"unknown canonical task IDs: {sorted(unknown)}")

    from cfgpu_mcp.config import get_registry

    models: list[dict[str, Any]] = []
    for adapter in sorted(get_registry().list_all(task_type=media_type), key=lambda item: item.model_name):
        tasks = _load_profile(adapter.adapter_id, task_catalog)
        declared_media_types = {
            task_catalog[profile_task]["media_type"]
            for profile_task in tasks
            if task_catalog[profile_task]["media_type"] != "cross_media"
        }
        if declared_media_types != {adapter.task_type}:
            raise ValueError(
                f"model profile for {adapter.adapter_id!r} does not match its task_type {adapter.task_type!r}"
            )
        supported_tasks = set(tasks)
        if requested_tasks and (
            not requested_tasks.issubset(supported_tasks)
            if match == "all"
            else not requested_tasks & supported_tasks
        ):
            continue
        models.append(
            {
                "model_id": adapter.model_name,
                "display_name": adapter.display_name,
                "task_type": adapter.task_type,
                "tasks": tasks,
                "cost_tier": adapter.cost_tier,
                "speed_tier": adapter.speed_tier,
            }
        )

    visible_task_ids = (
        requested_tasks
        if requested_tasks
        else {profile_task for model in models for profile_task in model["tasks"]}
    )
    return {
        "catalog_version": catalog_version,
        "task_catalog": {
            canonical_task: task_catalog[canonical_task]
            for canonical_task in sorted(visible_task_ids)
        },
        "models": models,
    }


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
