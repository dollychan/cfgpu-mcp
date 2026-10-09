"""The canonical task vocabulary (``capabilities/media_tasks.yaml``).

The one vocabulary for what a model can do. Each ``adapter.yaml`` lists its tasks from
here under ``tasks:``; the same list drives the code paths that depend on a task
(region editing, 组图, layer decomposition, transparent background, routing
preferences) and the agent-facing ``list_model_profiles``. There used to be a second,
adapter-internal ``capabilities`` vocabulary beside it; the two drifted (a word with
two meanings, a task promised to agents that no gate checked), so it was retired and
``from_config`` refuses the key.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

TASKS_PATH = Path(__file__).parent / "capabilities" / "media_tasks.yaml"
_REQUIRED_KEYS = frozenset({"media_type", "name", "description", "prompt_guidance"})


@lru_cache(maxsize=1)
def load_task_catalog() -> tuple[int, dict[str, dict[str, Any]]]:
    """Read and validate the vocabulary. Cached: it is package data, not config."""
    raw = yaml.safe_load(TASKS_PATH.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("media_tasks.yaml must declare schema_version: 1")
    if not isinstance(raw.get("catalog_version"), int):
        raise ValueError("media_tasks.yaml must declare an integer catalog_version")
    tasks = raw.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError("media_tasks.yaml must contain a non-empty tasks mapping")
    for task_id, task in tasks.items():
        if not isinstance(task_id, str) or not isinstance(task, dict):
            raise ValueError("media_tasks.yaml contains an invalid task entry")
        if not _REQUIRED_KEYS <= set(task) <= _REQUIRED_KEYS | {"requires"} or not all(
            isinstance(task[key], str) and task[key] for key in _REQUIRED_KEYS
        ):
            raise ValueError(f"media_tasks.yaml task {task_id!r} has an invalid schema")
        if "requires" in task:
            _validate_task_requires(task_id, task)
    return raw["catalog_version"], tasks


def _validate_task_requires(task_id: str, task: dict[str, Any]) -> None:
    """``requires`` names the material a task cannot be performed without.

    It is checked against every model's ``inputs:`` by the contract tests, so a model
    cannot claim a task it has no slot for — the drift that let ``reference_to_video``
    promise audio on models that refuse every audio track. Video only, because only
    video models declare ``inputs:``.
    """
    from cfgpu_mcp.adapters.inputs import AUDIO_ROLES, VIDEO_INPUT_SLOTS

    requires = task["requires"]
    if task["media_type"] != "video" or not isinstance(requires, dict) or not requires:
        raise ValueError(f"media_tasks.yaml task {task_id!r}: requires is a non-empty mapping, video tasks only")
    if set(requires) - {"all", "any", "audio_role"}:
        raise ValueError(f"media_tasks.yaml task {task_id!r}: requires takes only all / any / audio_role")
    for key in ("all", "any"):
        slots = requires.get(key, [])
        if not isinstance(slots, list) or not set(slots) <= set(VIDEO_INPUT_SLOTS):
            raise ValueError(f"media_tasks.yaml task {task_id!r}: requires.{key} must list input slots")
    role = requires.get("audio_role")
    if role is not None and (role not in AUDIO_ROLES or "reference_audios" not in requires.get("all", [])):
        raise ValueError(
            f"media_tasks.yaml task {task_id!r}: requires.audio_role must be one of "
            f"{list(AUDIO_ROLES)} and needs reference_audios in requires.all"
        )


def parse_tasks(adapter_id: str, task_type: str, raw: Any) -> tuple[str, ...]:
    """Validate an adapter's ``tasks:`` list against the vocabulary and its task_type.

    Order is kept — it is the order ``list_model_profiles`` shows.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(t, str) for t in raw):
        raise ValueError(f"{adapter_id}: tasks must be a list of canonical task IDs")
    if len(raw) != len(set(raw)):
        raise ValueError(f"{adapter_id}: tasks has duplicates")
    catalog = load_task_catalog()[1]
    unknown = [t for t in raw if t not in catalog]
    if unknown:
        raise ValueError(
            f"{adapter_id}: tasks references unknown canonical tasks {unknown} "
            "(capabilities/media_tasks.yaml)"
        )
    foreign = [t for t in raw if catalog[t]["media_type"] != task_type]
    if foreign:
        raise ValueError(f"{adapter_id}: tasks {foreign} do not belong to a {task_type} model")
    return tuple(raw)
