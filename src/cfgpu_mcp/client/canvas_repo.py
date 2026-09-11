"""Read-only MySQL access to the canvas_element table."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cfgpu_mcp.settings import CanvasDbSettings


_COLUMNS = "element_id, type, z_index, frame_id, payload_json, version"


class CanvasRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, settings: "CanvasDbSettings") -> "CanvasRepository":
        import aiomysql

        pool = await aiomysql.create_pool(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            db=settings.database,
            minsize=settings.pool_min,
            maxsize=settings.pool_max,
            cursorclass=aiomysql.DictCursor,
            autocommit=True,
        )
        return cls(pool)

    async def list_elements(
        self, project_id: str, types: list[str] | None, limit: int, offset: int
    ) -> list[dict]:
        clauses = ["project_id = %s", "is_delete = 0"]
        params: list[Any] = [project_id]
        if types:
            clauses.append("type IN (" + ", ".join("%s" for _ in types) + ")")
            params.extend(types)
        sql = (
            f"SELECT {_COLUMNS} FROM canvas_element WHERE {' AND '.join(clauses)} "
            "ORDER BY z_index, element_id LIMIT %s OFFSET %s"
        )
        params.extend((limit, offset))
        async with self._pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, tuple(params))
                return list(await cursor.fetchall())

    async def get_element(self, project_id: str, element_id: str) -> dict | None:
        sql = (
            f"SELECT {_COLUMNS} FROM canvas_element "
            "WHERE project_id = %s AND is_delete = 0 AND element_id = %s"
        )
        async with self._pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, (project_id, element_id))
                return await cursor.fetchone()

    async def close(self) -> None:
        self._pool.close()
        await self._pool.wait_closed()
