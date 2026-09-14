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

from cfgpu_mcp.client.migrations import ensure_schema
from cfgpu_mcp.client.repository import TaskRepository
# Shared, backend-agnostic row contract (same one the SQLite backend uses).
from cfgpu_mcp.client.task_row import row_to_dict as _row_to_dict

if TYPE_CHECKING:
    import asyncpg

# Non-terminal statuses list_running_tasks() must select — kept in step with db.py's
# _LIVE_STATUSES. The two pre-upstream ones are this server's own vocabulary: a row that
# has been written but whose upstream POST has not (provably) gone out yet. A query that
# cannot see them is a sweeper that can never converge a crashed submission.
_LIVE_STATUSES = ("submitting", "dispatching", "pending", "running")


class PostgresTaskRepository(TaskRepository):
    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, url: str, pool_min: int = 1, pool_max: int = 10) -> "PostgresTaskRepository":
        # The schema is Alembic's (``client/migrations.py``), not this class's. It used
        # to be created here by CREATE TABLE IF NOT EXISTS under an advisory lock; the
        # lock still exists, in ``migrations/env.py``, for the same reason — instances
        # start independently and can reach the same DDL at the same moment.
        await ensure_schema(url)
        import asyncpg

        pool = await asyncpg.create_pool(dsn=url, min_size=pool_min, max_size=pool_max)
        return cls(pool)

    async def insert_task(
        self, task_id: str, adapter_id: str, status: str, payload: dict,
        *, model_used: str | None = None,
    ) -> bool:
        """Insert, returning False when the id is already taken (nothing is written).

        ``ON CONFLICT DO NOTHING`` rather than a read-then-insert: this is a *shared*
        store, so two instances can be replaying the same request_id at the same
        instant, and only the database can settle which of them submits. asyncpg
        reports the row count in the command tag ("INSERT 0 1" / "INSERT 0 0").
        """
        now = time.time()
        async with self._pool.acquire() as con:
            tag = await con.execute(
                "INSERT INTO tasks (id, adapter_id, status, payload, created_at, updated_at, model_used)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (id) DO NOTHING",
                task_id, adapter_id, status, json.dumps(payload), now, now, model_used,
            )
        return str(tag).rsplit(" ", 1)[-1] == "1"

    async def update_task(
        self, task_id: str, status: str, result: dict | None = None, error: str | None = None,
        *, upstream_task_id: str | None = None, payload: dict | None = None,
    ) -> None:
        # COALESCE keeps "not given" distinct from "set to NULL" for the two optional
        # columns, so a status-only update never erases the upstream id or the payload.
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE tasks SET status=$1, result=$2, error=$3, updated_at=$4,"
                " upstream_task_id=COALESCE($5, upstream_task_id),"
                " payload=COALESCE($6, payload)"
                " WHERE id=$7",
                status,
                json.dumps(result) if result is not None else None,
                error,
                time.time(),
                upstream_task_id,
                json.dumps(payload) if payload is not None else None,
                task_id,
            )

    async def get_task(self, task_id: str) -> dict | None:
        """By primary key, falling back to the upstream id — see db.get_task."""
        async with self._pool.acquire() as con:
            row = await con.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            if row is None:
                row = await con.fetchrow("SELECT * FROM tasks WHERE upstream_task_id=$1", task_id)
            return _row_to_dict(row) if row else None

    async def list_running_tasks(self) -> list[dict]:
        async with self._pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM tasks WHERE status = ANY($1::text[]) ORDER BY created_at",
                list(_LIVE_STATUSES),
            )
            return [_row_to_dict(r) for r in rows]

    async def close(self) -> None:
        await self._pool.close()
