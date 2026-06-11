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
)


def row_to_dict(row: Mapping[str, Any]) -> dict:
    """Decode a raw DB row into the task dict, parsing the JSON-text columns."""
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d
