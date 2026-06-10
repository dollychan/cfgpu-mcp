"""TaskRepository — pluggable persistence for task state.

The single source of truth for task records, abstracted so the storage backend
is selected by a URL scheme (see ``create_task_repository``):

- ``sqlite:///path``        → SqliteTaskRepository  (single instance / stdio / CLI)
- ``postgresql://...``      → PostgresTaskRepository (multi-instance HTTP; TODO)

The interface mirrors the free functions in ``client/db.py`` minus the leading
``db`` argument; SqliteTaskRepository simply delegates to them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import aiosqlite

from cfgpu_mcp.client import db as db_ops


class TaskRepository(ABC):
    @abstractmethod
    async def insert_task(self, task_id: str, adapter_id: str, status: str, payload: dict) -> None: ...

    @abstractmethod
    async def update_task(self, task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> dict | None: ...

    @abstractmethod
    async def list_running_tasks(self) -> list[dict]: ...

    @abstractmethod
    async def close(self) -> None: ...


class SqliteTaskRepository(TaskRepository):
    """SQLite-backed repository. Wraps an aiosqlite connection.

    Construct via ``await SqliteTaskRepository.connect(url)`` for production, or
    pass an existing connection directly (tests / in-memory DBs).
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

    @classmethod
    async def connect(cls, url: str) -> "SqliteTaskRepository":
        # open_db normalizes the path (~ expansion, parent mkdir, :memory: passthrough).
        return cls(await db_ops.open_db(_sqlite_path(url)))

    async def insert_task(self, task_id: str, adapter_id: str, status: str, payload: dict) -> None:
        await db_ops.insert_task(self._db, task_id, adapter_id, status, payload)

    async def update_task(self, task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        await db_ops.update_task(self._db, task_id, status, result=result, error=error)

    async def get_task(self, task_id: str) -> dict | None:
        return await db_ops.get_task(self._db, task_id)

    async def list_running_tasks(self) -> list[dict]:
        return await db_ops.list_running_tasks(self._db)

    async def close(self) -> None:
        await self._db.close()


def _sqlite_path(url: str) -> str:
    """Extract a filesystem path from a ``sqlite://`` URL.

    ``sqlite:///~/.cfgpu/tasks.db`` → ``~/.cfgpu/tasks.db`` (home-relative)
    ``sqlite:////abs/path``         → ``/abs/path``        (absolute)
    ``sqlite:///:memory:``          → ``:memory:``
    A bare path with no scheme is returned as-is.

    Stripping exactly one leading slash yields the right path for both the
    3-slash (home-relative) and 4-slash (absolute) forms; open_db's expanduser
    handles ``~``.
    """
    if "://" not in url:
        return url
    rest = url[len("sqlite://"):]
    return rest[1:] if rest.startswith("/") else rest


async def create_task_repository(url: str, *, pool_min: int = 1, pool_max: int = 10) -> TaskRepository:
    """Instantiate the repository backend named by the URL scheme.

    ``pool_min`` / ``pool_max`` apply to the Postgres connection pool; SQLite
    ignores them.
    """
    scheme = urlparse(url).scheme or "sqlite"
    if scheme == "sqlite":
        return await SqliteTaskRepository.connect(url)
    if scheme.startswith("postgres"):
        from cfgpu_mcp.client.postgres_repo import PostgresTaskRepository

        return await PostgresTaskRepository.connect(url, pool_min=pool_min, pool_max=pool_max)
    raise ValueError(f"unsupported task_db scheme: {scheme!r} (url={url!r})")
