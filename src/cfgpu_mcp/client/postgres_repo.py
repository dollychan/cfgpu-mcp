"""PostgreSQL-backed TaskRepository (asyncpg connection pool).

The shared task store for multi-instance horizontal scaling: every server
instance points at the same Postgres and any instance can serve any request.

JSON columns (``payload`` / ``result``) are stored as ``text`` and (de)serialized
with ``json`` so the row dicts returned here are byte-for-byte identical to the
SQLite backend — callers (Task, _present) stay backend-agnostic.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from cfgpu_mcp.client.repository import TaskRepository
# Shared, backend-agnostic row contract (same one the SQLite backend uses).
from cfgpu_mcp.client.task_row import row_to_dict as _row_to_dict

if TYPE_CHECKING:
    import asyncpg

# Columns must match cfgpu_mcp.client.task_row.COLUMNS (and db.py's DDL).
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    adapter_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
)
"""

# Drives list_running_tasks(); also speeds a future background reconciler.
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at)"


class PostgresTaskRepository(TaskRepository):
    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, url: str, pool_min: int = 1, pool_max: int = 10) -> "PostgresTaskRepository":
        import asyncpg

        pool = await asyncpg.create_pool(dsn=url, min_size=pool_min, max_size=pool_max)
        repo = cls(pool)
        await repo._init_schema()
        return repo

    async def _init_schema(self) -> None:
        async with self._pool.acquire() as con:
            await con.execute(_CREATE_TABLE)
            await con.execute(_CREATE_INDEX)

    async def insert_task(self, task_id: str, adapter_id: str, status: str, payload: dict) -> None:
        now = time.time()
        async with self._pool.acquire() as con:
            await con.execute(
                "INSERT INTO tasks (id, adapter_id, status, payload, created_at, updated_at)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                task_id, adapter_id, status, json.dumps(payload), now, now,
            )

    async def update_task(self, task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE tasks SET status=$1, result=$2, error=$3, updated_at=$4 WHERE id=$5",
                status,
                json.dumps(result) if result is not None else None,
                error,
                time.time(),
                task_id,
            )

    async def get_task(self, task_id: str) -> dict | None:
        async with self._pool.acquire() as con:
            row = await con.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            return _row_to_dict(row) if row else None

    async def list_running_tasks(self) -> list[dict]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM tasks WHERE status IN ('pending', 'running') ORDER BY created_at"
            )
            return [_row_to_dict(r) for r in rows]

    async def close(self) -> None:
        await self._pool.close()
