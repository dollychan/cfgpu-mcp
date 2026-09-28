from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from cfgpu_mcp.voice_catalog import (
    AGE_GROUPS,
    FILTERABLE_GENDERS,
    VoiceEntry,
    catalog_for_adapter,
    language_matches,
    load_language_vocabulary,
)

_MODELS_DIR = Path(__file__).parent.parent / "models"
_TASKS_PATH = Path(__file__).parent.parent / "capabilities" / "media_tasks.yaml"
_TASK_PARAMETERS_PATH = Path(__file__).parent.parent / "capabilities" / "task_parameters.yaml"
_TASK_TYPES = frozenset({"image", "video", "audio", "understand"})
# 4: voices come from models/*/voices.yaml; `language` became `languages` (codes) and
# `gender` became an explicit field instead of a tag inferred from provider wording.
# 5: catalogs are generated from the model_audio_voices export; rows gained `age`,
# `description` and `accent`.
_VOICE_CATALOG_VERSION = 5

# Query terms that are a gender, not a keyword. Matched as whole terms only, and turned
# into the same filter as ``gender`` — a substring match would let "male" hit every
# ``zh_female_*`` id, which is how the old catalog answered "male" with female voices.
_GENDER_QUERY_TERMS = {
    "男声": "male", "男": "male", "male": "male", "man": "male",
    "女声": "female", "女": "female", "female": "female", "woman": "female",
}


def _query_terms(query: str) -> list[str]:
    """Split an intent query while preserving ordinary no-space keyword searches."""
    return [term for term in re.split(r"\s+", query.casefold().strip()) if term]


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


def _load_task_parameter_contracts(
    task_catalog: dict[str, dict[str, str]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load executable task templates keyed by public model ID and canonical task.

    A profile answers *which* model can perform a task. This adjacent contract answers
    *how to invoke it*, so an agent does not infer a billed request from card prose.
    """
    raw = yaml.safe_load(_TASK_PARAMETERS_PATH.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("task_parameters.yaml must declare schema_version: 1")
    models = raw.get("models")
    if not isinstance(models, dict):
        raise ValueError("task_parameters.yaml must contain a models mapping")
    for model_id, contracts in models.items():
        if not isinstance(model_id, str) or not isinstance(contracts, dict):
            raise ValueError("task_parameters.yaml contains an invalid model entry")
        unknown = set(contracts) - set(task_catalog)
        if unknown:
            raise ValueError(
                f"task_parameters.yaml model {model_id!r} references unknown tasks: {sorted(unknown)}"
            )
        if any(not isinstance(contract, dict) for contract in contracts.values()):
            raise ValueError(f"task_parameters.yaml model {model_id!r} has an invalid task contract")
    return models


async def list_model_profiles(
    media_type: str | None = None,
    required_tasks: list[str] | None = None,
    match: str = "all",
) -> dict[str, Any]:
    """List the agent-facing model catalog with canonical tasks only.

    This is intentionally separate from ``list_models``: the latter remains an
    operations/debugging API and contains adapter capabilities. When callers request
    canonical tasks, matching models additionally expose compact ``task_parameters``
    templates, preventing agents from guessing model-specific API switches from card
    prose.
    """
    if media_type is not None and media_type not in _TASK_TYPES:
        raise ValueError(f"unknown media_type {media_type!r}; expected one of {sorted(_TASK_TYPES)}")
    if match not in {"all", "any"}:
        raise ValueError("match must be 'all' or 'any'")

    catalog_version, task_catalog = _load_task_catalog()
    parameter_contracts = _load_task_parameter_contracts(task_catalog)
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
        model = {
            "model_id": adapter.model_name,
            "display_name": adapter.display_name,
            "task_type": adapter.task_type,
            "tasks": tasks,
            "cost_tier": adapter.cost_tier,
            "speed_tier": adapter.speed_tier,
        }
        if requested_tasks:
            contracts = parameter_contracts.get(adapter.model_name, {})
            matched_contracts = {
                task_id: contracts[task_id]
                for task_id in sorted(requested_tasks & set(contracts))
            }
            if matched_contracts:
                model["task_parameters"] = matched_contracts
        models.append(model)

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


async def list_voice_profiles(
    model_ids: list[str] | None = None,
    language: str | None = None,
    query: str | None = None,
    limit: int = 20,
    cursor: int = 0,
    gender: str | None = None,
    age: str | None = None,
) -> dict[str, Any]:
    """List compact selectable system voices from each model's ``voices.yaml``.

    The ``voice`` value is intentionally returned verbatim: copy it directly into
    ``generate_audio(voice=...)``. ``label`` is display-only and is never a valid
    value for that parameter.

    ``language``, ``gender`` and ``age`` are exact filters over declared fields:
    ``language`` accepts any spelling in ``capabilities/voice_languages.yaml`` and matches
    by subtag prefix (``English`` → ``en`` covers ``en-US``); a voice whose gender is
    ``unknown`` never satisfies a gender filter. ``query`` is space-separated terms that
    must all match the handle, label, tags, description, accent, or language names; a term
    that is a gender word (``男声`` / ``female``) acts as the gender filter instead.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if cursor < 0:
        raise ValueError("cursor must be >= 0")
    if gender is not None and gender not in FILTERABLE_GENDERS:
        raise ValueError(f"gender must be one of {list(FILTERABLE_GENDERS)}")
    if age is not None and age not in AGE_GROUPS:
        raise ValueError(f"age must be one of {list(AGE_GROUPS)}")

    vocabulary = load_language_vocabulary()
    language_code: str | None = None
    if language:
        language_code = vocabulary.resolve(language)
        if language_code is None:
            known = ", ".join(entry.name for entry in vocabulary.languages.values())
            raise ValueError(f"unknown language {language!r}; use a name or code such as: {known}")

    text_terms: list[str] = []
    for term in _query_terms(query) if query else []:
        term_gender = _GENDER_QUERY_TERMS.get(term)
        if term_gender is None:
            text_terms.append(term)
        elif gender not in (None, term_gender):
            raise ValueError(f"query term {term!r} contradicts gender={gender!r}")
        else:
            gender = term_gender

    from cfgpu_mcp.config import get_registry

    audio_adapters = sorted(get_registry().list_all(task_type="audio"), key=lambda item: item.model_name)
    by_model_id = {adapter.model_name: adapter for adapter in audio_adapters}
    requested_model_ids = set(model_ids or by_model_id)
    unknown_models = requested_model_ids - set(by_model_id)
    if unknown_models:
        raise ValueError(f"unknown audio model_ids: {sorted(unknown_models)}")

    # One row per voice handle, listing every model that accepts it (MiniMax HD and
    # Turbo share one catalog through ``extends``).
    grouped: dict[str, dict[str, Any]] = {}
    without_catalog: list[str] = []
    for adapter in audio_adapters:
        if adapter.model_name not in requested_model_ids:
            continue
        catalog = catalog_for_adapter(adapter.adapter_id)
        if catalog is None:
            without_catalog.append(adapter.model_name)
            continue
        for entry in catalog.voices:
            row = grouped.setdefault(entry.voice, {"entry": entry, "model_ids": []})
            row["model_ids"].append(adapter.model_name)
    if model_ids and without_catalog:
        # Named explicitly, an empty answer would read as "no voice matched".
        raise ValueError(
            f"audio model_ids without a system voice catalog: {without_catalog}; "
            "omit voice when calling generate_audio with them"
        )

    def language_spellings(codes: list[str]) -> list[str]:
        return [
            spelling
            for code in codes
            for spelling in (vocabulary.languages[code].name, *vocabulary.languages[code].aliases)
        ]

    def matches(entry: VoiceEntry) -> bool:
        if language_code and not language_matches(entry.languages, language_code):
            return False
        if gender and entry.gender != gender:
            return False
        if age and entry.age != age:
            return False
        if not text_terms:
            return True
        searchable = " ".join(
            [
                entry.voice,
                entry.label,
                *entry.tags,
                entry.description or "",
                entry.accent or "",
                *language_spellings(entry.languages),
            ]
        ).casefold()
        return all(term in searchable for term in text_terms)

    # Round-robin across compatibility groups so one large catalog (MiniMax lists 327
    # voices) cannot fill every first page; each group keeps its catalog's own order.
    voice_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in grouped.values():
        entry: VoiceEntry = row["entry"]
        if not matches(entry):
            continue
        voice = {
            "voice": entry.voice,
            "label": entry.label,
            "languages": entry.languages,
            "gender": entry.gender,
            "age": entry.age,
            "tags": entry.tags,
            "model_ids": row["model_ids"],
        }
        # Present only when the catalog has them, so rows stay compact.
        if entry.accent:
            voice["accent"] = entry.accent
        if entry.description:
            voice["description"] = entry.description
        if entry.default:
            voice["default"] = True
        voice_groups.setdefault(tuple(row["model_ids"]), []).append(voice)

    voices: list[dict[str, Any]] = []
    for position in range(max((len(group) for group in voice_groups.values()), default=0)):
        for signature in sorted(voice_groups):
            group = voice_groups[signature]
            if position < len(group):
                voices.append(group[position])
    page = voices[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(voices) else None
    return {
        "catalog_version": _VOICE_CATALOG_VERSION,
        "voices": page,
        "total": len(voices),
        "next_cursor": next_cursor,
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
