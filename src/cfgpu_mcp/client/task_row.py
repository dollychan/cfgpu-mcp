"""Backend-agnostic task row contract, shared by every TaskRepository backend.

The canonical column set and the raw-row → dict decoding live here (not inside
either backend) so SQLite and Postgres return byte-for-byte identical row dicts
and a new column (e.g. a future ``tenant_id``) is declared in one place.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

# Canonical task columns in declaration order. Both backends' CREATE TABLE and
# every ``SELECT *`` depend on this set; add a column here first.
COLUMNS: tuple[str, ...] = (
    "id",
    "adapter_id",
    "status",
    "payload",
    "result",
    "error",
    "created_at",
    "updated_at",
    # The upstream's own task id, for async models only. Purely internal: it exists
    # solely to build the poll URL and never reaches a caller — see
    # request-id-durability.md D6/I6. NULL on rows written before that change, whose
    # ``id`` *is* the upstream id.
    "upstream_task_id",
    # The public model name (``adapter.model_name``) this task ran on. Derivable from
    # ``adapter_id`` through the registry, and denormalized here on purpose: it makes
    # per-model latency answerable in plain SQL (``updated_at - created_at`` over
    # terminal rows) with no YAML/registry join, and in the agent-facing name rather
    # than the internal adapter_id.
    "model_used",
)


def row_to_dict(row: Mapping[str, Any]) -> dict:
    """Decode a raw DB row into the task dict, parsing the JSON-text columns."""
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d
