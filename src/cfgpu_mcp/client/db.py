from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite

from cfgpu_mcp.client.task_row import row_to_dict as _row_to_dict

# Non-terminal statuses a sweeper / list_running_tasks must be able to see. The two
# pre-upstream ones are cfgpu-mcp's own vocabulary (see request-id-durability.md D4);
# omitting them here is how a crashed submission becomes a row nothing ever converges.
_LIVE_STATUSES = ("submitting", "dispatching", "pending", "running")

# ── Schema ───────────────────────────────────────────────────────────────────────
#
# ★ This DDL is **not** the schema's source of truth. Alembic is
# (``cfgpu_mcp/migrations/``), for every durable store. What is left here is the
# bootstrap for an in-memory database, which Alembic structurally cannot reach: with
# ``:memory:`` each connection is its own database, so a migration run over a second
# connection would build a different one and throw it away. Such a database has no
# history to migrate and no deployment to break, so it is created at head directly.
#
# It stays byte-compatible with the migration chain's end state by test, not by hope:
# ``test_migrations.py`` builds a database each way and compares the two. Adding a
# column here without a matching revision (or the reverse) fails there.
#
# Columns must match cfgpu_mcp.client.task_row.COLUMNS.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    adapter_id       TEXT NOT NULL,
    status           TEXT NOT NULL,
    payload          TEXT NOT NULL,
    result           TEXT,
    error            TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    upstream_task_id TEXT,
    model_used       TEXT
)
"""

#: Columns added after the table's first release, as ``(name, type)``. Needed even in
#: the in-memory bootstrap, because a connection may be handed to this module against a
#: table someone else created (tests do exactly that with the older DDL).
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("upstream_task_id", "TEXT"),
    ("model_used", "TEXT"),
)

#: Drives the second lookup hop (``get_task`` falling back to the upstream id), which
#: is what keeps a task_id handed out before this change resolvable.
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_tasks_upstream ON tasks(upstream_task_id)"


def _resolve_path(raw: str | Path) -> Path:
    """Expand ``~`` and ensure the parent dir exists (``:memory:`` passes through)."""
    if str(raw) == ":memory:":
        return Path(raw)
    p = Path(raw).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def migrate_schema(db: aiosqlite.Connection) -> None:
    """Add any column in ``_ADDED_COLUMNS`` the live table is missing.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so the existence check is a
    ``PRAGMA table_info`` read. Additive only — no column is ever dropped or retyped,
    and existing rows keep NULL, which every reader treats as "not applicable" rather
    than as a value (see ``TaskManager.poll``'s ``upstream_task_id or id``).
    """
    async with db.execute("PRAGMA table_info(tasks)") as cur:
        existing = {row["name"] for row in await cur.fetchall()}
    for name, sql_type in _ADDED_COLUMNS:
        if name not in existing:
            await db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {sql_type}")
    await db.commit()


async def open_db(path: str | Path) -> aiosqlite.Connection:
    """Open an aiosqlite connection at ``path`` (``~`` expanded, parent dir created),
    WAL-enabled, with the schema applied.

    The DDL run here is idempotent, so for a file-backed database — where Alembic has
    already brought the schema to head by the time this is called — it is a no-op. For
    ``:memory:`` it is the whole schema. See the note above ``_CREATE_TABLE``.
    """
    db = await aiosqlite.connect(str(_resolve_path(path)))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(_CREATE_TABLE)
    await db.commit()
    await migrate_schema(db)
    await db.execute(_CREATE_INDEX)
    await db.commit()
    return db


async def insert_task(
    db: aiosqlite.Connection,
    task_id: str,
    adapter_id: str,
    status: str,
    payload: dict,
    *,
    model_used: str | None = None,
) -> bool:
    """Insert a task row, returning whether it was actually inserted.

    ``INSERT OR IGNORE`` rather than a plain insert: with the caller's ``request_id``
    as the primary key, a conflict is not a programming error but the design's dedup
    signal — the same submission arriving twice (a checkpoint replay, a host resend).
    The boolean is what lets ``TaskManager.create`` tell the two apart without a
    read-before-write race; see request-id-durability.md D2.
    """
    now = time.time()
    cur = await db.execute(
        "INSERT OR IGNORE INTO tasks (id, adapter_id, status, payload, created_at, updated_at, model_used)"
        " VALUES (?,?,?,?,?,?,?)",
        (task_id, adapter_id, status, json.dumps(payload), now, now, model_used),
    )
    await db.commit()
    return cur.rowcount == 1


async def update_task(
    db: aiosqlite.Connection,
    task_id: str,
    status: str,
    result: dict | None = None,
    error: str | None = None,
    *,
    upstream_task_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Advance a task row. ``upstream_task_id`` / ``payload`` are written only when
    given — passing None leaves the stored value alone, unlike ``result`` / ``error``
    which are always overwritten (a status change invalidates both)."""
    sets = ["status=?", "result=?", "error=?", "updated_at=?"]
    args: list = [
        status,
        json.dumps(result) if result is not None else None,
        error,
        time.time(),
    ]
    if upstream_task_id is not None:
        sets.append("upstream_task_id=?")
        args.append(upstream_task_id)
    if payload is not None:
        sets.append("payload=?")
        args.append(json.dumps(payload))
    args.append(task_id)
    await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", tuple(args))
    await db.commit()


async def get_task(db: aiosqlite.Connection, task_id: str) -> dict | None:
    """Look a task up by primary key, falling back to the upstream id.

    The second hop exists for rows written before the primary key became the caller's
    ``request_id``: those carry the upstream id *as* their key, and any caller still
    holding one must keep resolving. It runs only on a miss, so the common path is a
    single indexed read.
    """
    async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        async with db.execute("SELECT * FROM tasks WHERE upstream_task_id=?", (task_id,)) as cur:
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_running_tasks(db: aiosqlite.Connection) -> list[dict]:
    placeholders = ",".join("?" * len(_LIVE_STATUSES))
    async with db.execute(
        f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at",
        _LIVE_STATUSES,
    ) as cur:
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]
