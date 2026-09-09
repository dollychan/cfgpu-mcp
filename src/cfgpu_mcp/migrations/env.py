"""Alembic environment for the cfgpu task store.

Two things here are not boilerplate:

**It is async, for both dialects.** This project talks to its databases through
``aiosqlite`` and ``asyncpg`` and declares no sync drivers, so the migration engine uses
the same ones (``sqlite+aiosqlite`` / ``postgresql+asyncpg``). ``asyncio.run`` below is
safe because ``ensure_schema`` calls Alembic from a worker thread — a thread with no
running loop of its own.

**Postgres migrations are serialized by an advisory lock.** Instances run
``upgrade head`` at startup, so several of them can arrive at the same DDL at the same
moment, and ``CREATE TABLE`` is not race-safe at the catalog level (two winners collide
inserting the table's row type into ``pg_type``). The lock is transaction-scoped and
taken *inside* Alembic's own transaction, so it covers the whole upgrade and releases on
commit; the losers then run each revision's guarded body as a no-op.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config

# No ORM models: revisions are explicit, hand-written operations. Autogenerate is
# deliberately unavailable — the schema is small, and a diff engine that has never seen
# the raw-SQL repositories would write revisions nobody reviewed.
target_metadata = None

# An arbitrary stable int64 tag for "cfgpu.tasks schema" — the same key the repository
# used before migrations existed, kept so a deploy that straddles both versions still
# serializes against itself.
_SCHEMA_LOCK_KEY = 0x0CF6_7A5C_0000_0001


def _url() -> str:
    """The database URL, in the order the three callers supply it.

    1. ``config.attributes`` — how the server passes it, already translated to an async
       driver. Not ``sqlalchemy.url``: a main option goes through ConfigParser
       interpolation, which mangles any password containing ``%``.
    2. ``$DATABASE_URL`` — how an operator runs it by hand. Taken raw and translated
       here, so the documented command works with the same DSN the server is configured
       with, rather than requiring one hand-edited to name a driver.
    3. ``sqlalchemy.url`` in alembic.ini — last, and normally empty.
    """
    url = config.attributes.get("db_url")
    if url:
        return url
    raw = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not raw:
        raise RuntimeError(
            "no database URL: set DATABASE_URL (or sqlalchemy.url in alembic.ini) "
            "before running alembic by hand"
        )
    from cfgpu_mcp.client.migrations import to_sqlalchemy_url

    translated = to_sqlalchemy_url(raw)
    if translated is None:
        raise RuntimeError(
            "an in-memory SQLite database has no schema history to migrate "
            "(it is created at head by cfgpu_mcp.client.db.open_db)"
        )
    return translated


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things in place; batch mode rewrites the table
        # instead. Not needed by the current revisions (both are additive) but the next
        # one that drops or retypes a column would silently fail without it.
        render_as_batch=True,
    )
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_SCHEMA_LOCK_KEY})")
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async_migrations())
