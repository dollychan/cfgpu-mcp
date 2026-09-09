"""Schema migration entry point — Alembic, run at connect time.

**Who owns what.** Alembic owns every durable store: the file-backed SQLite database and
Postgres. It is the only thing that may change their schema, and it is the only place a
schema change is written down. The hand-written DDL that remains in ``client/db.py``
covers exactly one case Alembic structurally cannot reach — an in-memory SQLite database,
where every connection *is* its own database, so a migration run over a second connection
would migrate a different (and immediately discarded) store. ``:memory:`` has no history
to migrate and no deployment to break; it is created at head and used for the length of a
test. ``test_migrations.py`` pins the two against each other so they cannot drift.

**Why at connect time rather than as a deploy step.** That is what this server already
did — ``CREATE TABLE IF NOT EXISTS`` on every start — and the instances are restarted
independently by systemd with no coordinating step to hang a manual ``upgrade`` off. The
concurrency that implies is handled in ``migrations/env.py`` by a Postgres advisory lock.
Running it by hand still works and is the right move for a large migration:
``alembic -c alembic.ini upgrade head`` from the repo root, with ``DATABASE_URL`` set.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

#: libpq query parameters that a DSN may carry for asyncpg's own parser but that
#: SQLAlchemy hands to ``asyncpg.connect()`` as keyword arguments, where they do not
#: exist. Translated rather than dropped where they mean something, so a TLS-requiring
#: DSN keeps requiring TLS through the migration connection too.
_SSLMODE_REQUIRES_TLS = {"require", "verify-ca", "verify-full"}


def to_sqlalchemy_url(url: str) -> str | None:
    """Translate a repository URL into one SQLAlchemy can open, or None for ``:memory:``.

    None is the "not migratable, and does not need to be" answer — see the module
    docstring. Everything else is pinned to the async driver this project already
    depends on, so migrations need no extra DBAPI.
    """
    scheme = urlparse(url).scheme or "sqlite"
    if scheme == "sqlite":
        from cfgpu_mcp.client.db import _resolve_path
        from cfgpu_mcp.client.repository import _sqlite_path

        raw = _sqlite_path(url)
        if str(raw) == ":memory:":
            return None
        return f"sqlite+aiosqlite:///{_resolve_path(raw).resolve()}"
    if scheme.startswith("postgres"):
        return _postgres_url(url)
    raise ValueError(f"unsupported task_db scheme: {scheme!r} (url={url!r})")


def _postgres_url(url: str) -> str:
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    rewritten: list[tuple[str, str]] = []
    for key, value in query:
        if key == "sslmode":
            if value in _SSLMODE_REQUIRES_TLS:
                rewritten.append(("ssl", "true"))
            # disable/allow/prefer: asyncpg's default is already "no TLS unless asked",
            # so dropping them preserves the meaning rather than losing it.
            continue
        rewritten.append((key, value))
    return urlunparse(parts._replace(scheme="postgresql+asyncpg", query=urlencode(rewritten)))


def _upgrade(sa_url: str) -> None:
    """Blocking ``alembic upgrade head``. Always called on a worker thread."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    # Passed as an attribute, not a main option: ConfigParser interpolation would choke
    # on a password containing '%'.
    cfg.attributes["db_url"] = sa_url
    command.upgrade(cfg, "head")


async def ensure_schema(url: str) -> None:
    """Bring the store at ``url`` to the head revision. No-op for ``:memory:``.

    Run in a thread because Alembic is synchronous and ``env.py`` drives an async engine
    with ``asyncio.run`` — which needs a thread that has no loop of its own. Doing it
    inline would both block the event loop and raise.
    """
    sa_url = to_sqlalchemy_url(url)
    if sa_url is None:
        return
    logger.debug("applying task-store migrations")
    await asyncio.to_thread(_upgrade, sa_url)
