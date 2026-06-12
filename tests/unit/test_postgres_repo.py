"""PostgresTaskRepository unit tests against a fake asyncpg pool.

No live Postgres needed: a small in-memory fake emulates the four queries so we
exercise the real SQL/param wiring and json (de)serialization, and confirm the
row dicts match the SQLite backend's shape.
"""

import json

import pytest

from cfgpu_mcp.client.postgres_repo import PostgresTaskRepository, _row_to_dict


class _FakeTxn:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakeConn:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def transaction(self):
        return _FakeTxn()

    async def execute(self, sql: str, *args):
        verb = sql.split(None, 1)[0].upper()
        if verb in ("CREATE", "SELECT"):  # DDL + pg_advisory_xact_lock are no-ops here
            return
        if verb == "INSERT":
            task_id, adapter_id, status, payload, created, updated = args
            self.rows[task_id] = {
                "id": task_id, "adapter_id": adapter_id, "status": status,
                "payload": payload, "result": None, "error": None,
                "created_at": created, "updated_at": updated,
            }
        elif verb == "UPDATE":
            status, result, error, updated, task_id = args
            self.rows[task_id].update(status=status, result=result, error=error, updated_at=updated)

    async def fetchrow(self, sql: str, *args):
        return self.rows.get(args[0])

    async def fetch(self, sql: str, *args):
        return [r for r in self.rows.values() if r["status"] in ("pending", "running")]


class _FakeAcquire:
    def __init__(self, conn): self.conn = conn
    async def __aenter__(self): return self.conn
    async def __aexit__(self, *a): return False


class _FakePool:
    def __init__(self, conn): self.conn = conn; self.closed = False
    def acquire(self): return _FakeAcquire(self.conn)
    async def close(self): self.closed = True


def _repo():
    conn = _FakeConn({})
    return PostgresTaskRepository(_FakePool(conn)), conn


def test_row_to_dict_deserializes_json_like_sqlite():
    row = {
        "id": "t1", "adapter_id": "wan-2-0", "status": "succeeded",
        "payload": json.dumps({"prompt": "x"}), "result": json.dumps({"urls": ["u"]}),
        "error": None, "created_at": 1.0, "updated_at": 2.0,
    }
    d = _row_to_dict(row)
    assert d["payload"] == {"prompt": "x"}
    assert d["result"] == {"urls": ["u"]}


@pytest.mark.asyncio
async def test_insert_get_round_trip():
    repo, _ = _repo()
    await repo.insert_task("t1", "wan-2-0", "pending", {"prompt": "x"})
    row = await repo.get_task("t1")
    assert row["status"] == "pending"
    assert row["payload"] == {"prompt": "x"}     # deserialized
    assert row["result"] is None
    assert await repo.get_task("missing") is None


@pytest.mark.asyncio
async def test_update_and_list_running():
    repo, _ = _repo()
    await repo.insert_task("t1", "wan-2-0", "pending", {"p": 1})
    await repo.insert_task("t2", "wan-2-0", "running", {"p": 2})
    assert {r["id"] for r in await repo.list_running_tasks()} == {"t1", "t2"}

    await repo.update_task("t1", "succeeded", result={"urls": ["v"]})
    row = await repo.get_task("t1")
    assert row["status"] == "succeeded"
    assert row["result"] == {"urls": ["v"]}
    assert {r["id"] for r in await repo.list_running_tasks()} == {"t2"}  # t1 excluded


@pytest.mark.asyncio
async def test_close_closes_pool():
    repo, _ = _repo()
    await repo.close()
    assert repo._pool.closed is True


@pytest.mark.asyncio
async def test_factory_routes_postgres_scheme(monkeypatch):
    import asyncpg

    from cfgpu_mcp.client.repository import create_task_repository

    captured = {}

    async def fake_create_pool(dsn, min_size, max_size):
        captured.update(dsn=dsn, min_size=min_size, max_size=max_size)
        return _FakePool(_FakeConn({}))

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    repo = await create_task_repository("postgresql://u:p@h:5432/db", pool_min=2, pool_max=7)
    assert isinstance(repo, PostgresTaskRepository)
    assert captured == {"dsn": "postgresql://u:p@h:5432/db", "min_size": 2, "max_size": 7}
