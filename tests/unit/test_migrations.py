"""Alembic owns the durable schema — these pin the three ways that can go wrong.

1. **Drift.** ``client/db.py`` still carries DDL, for in-memory databases Alembic cannot
   reach. Two places that describe one schema will diverge; the parity test is what turns
   that into a failing build instead of a production ``no such column``.
2. **Legacy stores.** Every existing deployment has the table already and no
   ``alembic_version`` row. ``upgrade head`` has to bring those forward without an
   operator deciding where they stand.
3. **Repeat runs.** Migrations execute on every start, on several instances at once.
"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.migrations import _upgrade, ensure_schema, to_sqlalchemy_url

_LEGACY_DDL = (
    "CREATE TABLE tasks (id TEXT PRIMARY KEY, adapter_id TEXT NOT NULL,"
    " status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, error TEXT,"
    " created_at REAL NOT NULL, updated_at REAL NOT NULL)"
)


async def _schema_of(path: str) -> tuple[set[str], set[str]]:
    """(columns, indexes) of the tasks table at ``path`` — the comparable shape."""
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        async with db.execute("PRAGMA table_info(tasks)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        async with db.execute("PRAGMA index_list(tasks)") as cur:
            idx = {r["name"] for r in await cur.fetchall() if not r["name"].startswith("sqlite_")}
        return cols, idx
    finally:
        await db.close()


async def _migrated(tmp_path, name: str = "tasks.db") -> str:
    path = str(tmp_path / name)
    await asyncio.to_thread(_upgrade, f"sqlite+aiosqlite:///{path}")
    return path


async def test_upgrade_from_nothing_builds_the_whole_schema(tmp_path):
    cols, idx = await _schema_of(await _migrated(tmp_path))
    assert cols == {
        "id", "adapter_id", "status", "payload", "result", "error",
        "created_at", "updated_at", "upstream_task_id", "model_used",
    }
    assert {"idx_tasks_status_created", "idx_tasks_upstream"} <= idx


async def test_upgrade_adopts_a_legacy_store_without_stamping(tmp_path):
    """The case every existing deployment is in: the table is there, Alembic's bookkeeping
    is not. The revisions are written guarded so this needs no operator judgement — and
    the row already in the table has to survive, which is the part a naive
    create-then-stamp gets wrong."""
    path = str(tmp_path / "legacy.db")
    db = await aiosqlite.connect(path)
    await db.execute(_LEGACY_DDL)
    await db.execute(
        "INSERT INTO tasks VALUES ('cfgpu-old-1','wan-2-0','pending','{}',NULL,NULL,1.0,2.0)"
    )
    await db.commit()
    await db.close()

    await asyncio.to_thread(_upgrade, f"sqlite+aiosqlite:///{path}")

    cols, _ = await _schema_of(path)
    assert {"upstream_task_id", "model_used"} <= cols
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM tasks") as cur:
        row = await cur.fetchone()
    await db.close()
    # The old row is intact and its new columns are NULL — which is exactly what
    # `poll()`'s `upstream_task_id or id` fallback is written for.
    assert row["id"] == "cfgpu-old-1" and row["upstream_task_id"] is None


async def test_upgrade_is_idempotent(tmp_path):
    """Instances restart independently; the second one through must find nothing to do."""
    path = await _migrated(tmp_path)
    before = await _schema_of(path)
    await asyncio.to_thread(_upgrade, f"sqlite+aiosqlite:///{path}")
    assert await _schema_of(path) == before


async def test_migrated_schema_matches_the_in_memory_bootstrap(tmp_path):
    """★ The drift guard. ``client/db.py``'s DDL exists only because Alembic cannot run
    against ``:memory:``; the moment it stops agreeing with the migration chain, tests
    and production are running different schemas."""
    migrated_cols, migrated_idx = await _schema_of(await _migrated(tmp_path))

    bootstrap_path = str(tmp_path / "bootstrap.db")
    db = await db_ops.open_db(bootstrap_path)
    await db.close()
    boot_cols, boot_idx = await _schema_of(bootstrap_path)

    assert boot_cols == migrated_cols
    # The bootstrap does not create the status index (it never did, on SQLite); what
    # matters is that it creates no index the migrations do not know about.
    assert boot_idx <= migrated_idx


async def test_memory_url_is_not_migrated():
    """``:memory:`` is per-connection, so migrating it would build a database nobody uses.
    ``ensure_schema`` must recognise that rather than half-work."""
    assert to_sqlalchemy_url("sqlite:///:memory:") is None
    await ensure_schema("sqlite:///:memory:")   # no-op, must not raise


def test_sqlite_urls_resolve_to_absolute_paths(tmp_path, monkeypatch):
    """A relative or ``~``-prefixed path must land in the same file the repository opens —
    Alembic runs from wherever the process was started, the repository from its own
    normalization, and the two disagreeing means migrating one database and using another."""
    monkeypatch.setenv("HOME", str(tmp_path))
    url = to_sqlalchemy_url("sqlite:///~/.cfgpu/tasks.db")
    assert url.startswith("sqlite+aiosqlite:///")
    assert str(tmp_path) in url and "~" not in url


@pytest.mark.parametrize(
    "dsn, expected",
    [
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgresql://u:p@h/db?sslmode=require", "postgresql+asyncpg://u:p@h/db?ssl=true"),
        ("postgresql://u:p@h/db?sslmode=disable", "postgresql+asyncpg://u:p@h/db"),
    ],
)
def test_postgres_dsn_is_translated_for_the_async_driver(dsn, expected):
    """SQLAlchemy hands query parameters to ``asyncpg.connect()`` as keyword arguments,
    where ``sslmode`` does not exist — so a DSN that works for the repository would raise
    a TypeError during migration. Translated, not dropped: a DSN that demands TLS keeps
    demanding it on the connection that alters the schema."""
    assert to_sqlalchemy_url(dsn) == expected


def test_unknown_scheme_is_rejected():
    with pytest.raises(ValueError, match="unsupported task_db scheme"):
        to_sqlalchemy_url("mysql://h/db")
