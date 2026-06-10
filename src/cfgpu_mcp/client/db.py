from __future__ import annotations

import json
import os
import time
from pathlib import Path

import aiosqlite

_DEFAULT_DB_PATH = "~/.cfgpu/tasks.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    adapter_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
)
"""


def _db_path() -> Path:
    raw = os.getenv("CFGPU_DB_PATH", _DEFAULT_DB_PATH)
    return _resolve_path(raw)


def _resolve_path(raw: str) -> Path:
    """Expand ``~`` and ensure the parent dir exists (``:memory:`` passes through)."""
    if raw == ":memory:":
        return Path(raw)
    p = Path(raw).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def open_db(path: str | Path) -> aiosqlite.Connection:
    """Open an aiosqlite connection at ``path``, WAL-enabled, with the schema applied."""
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(_CREATE_TABLE)
    await db.commit()
    return db


async def get_db() -> aiosqlite.Connection:
    return await open_db(_db_path())


async def insert_task(
    db: aiosqlite.Connection,
    task_id: str,
    adapter_id: str,
    status: str,
    payload: dict,
) -> None:
    now = time.time()
    await db.execute(
        "INSERT INTO tasks (id, adapter_id, status, payload, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (task_id, adapter_id, status, json.dumps(payload), now, now),
    )
    await db.commit()


async def update_task(
    db: aiosqlite.Connection,
    task_id: str,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE id=?",
        (
            status,
            json.dumps(result) if result is not None else None,
            error,
            time.time(),
            task_id,
        ),
    )
    await db.commit()


async def get_task(db: aiosqlite.Connection, task_id: str) -> dict | None:
    async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None


async def list_running_tasks(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT * FROM tasks WHERE status IN ('pending', 'running') ORDER BY created_at"
    ) as cur:
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d
