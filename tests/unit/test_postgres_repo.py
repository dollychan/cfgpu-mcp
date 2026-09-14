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
        if verb in ("CREATE", "ALTER", "SELECT"):  # DDL + pg_advisory_xact_lock are no-ops here
            return
        if verb == "INSERT":
            task_id, adapter_id, status, payload, created, updated, model_used = args
            # Emulates ON CONFLICT DO NOTHING, including the command tag asyncpg
            # returns — that tag is the only thing telling create() whether this was a
            # first submission or a replay, so a fake that always claimed success would
            # hide exactly the bug this backend exists to avoid.
            if task_id in self.rows:
                return "INSERT 0 0"
            self.rows[task_id] = {
                "id": task_id, "adapter_id": adapter_id, "status": status,
                "payload": payload, "result": None, "error": None,
                "created_at": created, "updated_at": updated,
                "upstream_task_id": None, "model_used": model_used,
            }
            return "INSERT 0 1"
        elif verb == "UPDATE":
            status, result, error, updated, upstream_task_id, payload, task_id = args
            row = self.rows[task_id]
            row.update(status=status, result=result, error=error, updated_at=updated)
            # COALESCE: None means "not given", never "set to NULL".
            if upstream_task_id is not None:
                row["upstream_task_id"] = upstream_task_id
            if payload is not None:
                row["payload"] = payload

    async def fetchrow(self, sql: str, *args):
        if "upstream_task_id=" in sql:
            return next((r for r in self.rows.values() if r["upstream_task_id"] == args[0]), None)
        return self.rows.get(args[0])

    async def fetch(self, sql: str, *args):
        return [r for r in self.rows.values() if r["status"] in args[0]]


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
        "upstream_task_id": "cfgpu-9", "model_used": "wan-video",
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

    from cfgpu_mcp.client import postgres_repo
    from cfgpu_mcp.client.repository import create_task_repository

    captured = {}
    order: list[str] = []

    async def fake_create_pool(dsn, min_size, max_size):
        order.append("pool")
        captured.update(dsn=dsn, min_size=min_size, max_size=max_size)
        return _FakePool(_FakeConn({}))

    async def fake_ensure_schema(url):
        order.append("migrate")

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(postgres_repo, "ensure_schema", fake_ensure_schema)
    repo = await create_task_repository("postgresql://u:p@h:5432/db", pool_min=2, pool_max=7)
    assert isinstance(repo, PostgresTaskRepository)
    assert captured == {"dsn": "postgresql://u:p@h:5432/db", "min_size": 2, "max_size": 7}
    # Migrations run first, and against the caller's own DSN — the repository must never
    # be usable at a schema revision behind the code that is about to query it.
    assert order == ["migrate", "pool"]


@pytest.mark.asyncio
async def test_insert_reports_the_conflict_instead_of_raising():
    """A repeated primary key is the dedup signal, not an error — and on a *shared*
    store it is the only arbiter: two instances can be replaying the same request_id at
    the same instant, and read-then-insert would let both through."""
    repo, _ = _repo()
    assert await repo.insert_task("t1", "wan-2-0", "submitting", {"p": 1}) is True
    assert await repo.insert_task("t1", "wan-2-0", "submitting", {"p": 2}) is False
    assert (await repo.get_task("t1"))["payload"] == {"p": 1}


@pytest.mark.asyncio
async def test_get_task_falls_back_to_the_upstream_id():
    """The second hop keeps a task_id handed out before the key became the caller's
    request_id resolvable — those rows are never backfilled."""
    repo, _ = _repo()
    await repo.insert_task("req-1", "wan-2-0", "submitting", {})
    await repo.update_task("req-1", "pending", upstream_task_id="cfgpu-9")

    assert (await repo.get_task("cfgpu-9"))["id"] == "req-1"


@pytest.mark.asyncio
async def test_status_only_update_keeps_the_upstream_id_and_payload():
    """COALESCE, not overwrite: a later `update_task(id, "succeeded", result=...)` must
    not erase the handle the poll URL is built from."""
    repo, _ = _repo()
    await repo.insert_task("req-1", "wan-2-0", "submitting", {"p": 1})
    await repo.update_task("req-1", "pending", upstream_task_id="cfgpu-9")
    await repo.update_task("req-1", "succeeded", result={"urls": ["u"]})

    row = await repo.get_task("req-1")
    assert row["upstream_task_id"] == "cfgpu-9"
    assert row["payload"] == {"p": 1}


@pytest.mark.asyncio
async def test_list_running_includes_the_pre_upstream_states():
    """`submitting` / `dispatching` rows are the ones a crash leaves behind; a query
    that cannot see them is a sweeper that can never converge them."""
    repo, _ = _repo()
    await repo.insert_task("a", "m", "submitting", {})
    await repo.insert_task("b", "m", "dispatching", {})
    await repo.insert_task("c", "m", "succeeded", {})

    assert {r["id"] for r in await repo.list_running_tasks()} == {"a", "b"}
