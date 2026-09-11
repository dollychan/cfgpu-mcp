import json

import pytest

from cfgpu_mcp.context import reset_project_id, set_project_id


class _CanvasRepo:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.list_args = None
        self.resolve_args = None

    async def list_elements(self, project_id, types, limit, offset):
        self.list_args = (project_id, types, limit, offset)
        return self.rows

    async def get_element(self, project_id, element_id):
        self.resolve_args = (project_id, element_id)
        return self.row


def _row(*, kind="image", version=7, payload=None):
    return {
        "element_id": "element-1",
        "type": kind,
        "z_index": 2,
        "frame_id": "frame-1",
        "version": version,
        "payload_json": payload if payload is not None else {
            "fileName": "reference.png",
            "status": "ready",
            "x": 10,
            "y": 20,
            "width": 640,
            "height": 480,
            "visible": True,
            "ossKey": "agent-artifacts/p1/reference.png",
            "projectId": "private-project",
            "fileId": "private-file",
            "url": "https://private.example/signed",
            "prompt": "private prompt",
            "createdAt": "private timestamp",
        },
    }


@pytest.mark.asyncio
async def test_list_projects_rows_to_safe_compact_elements_and_fetches_one_extra():
    from cfgpu_mcp.service.canvas import inspect_canvas

    repo = _CanvasRepo(rows=[_row(payload=json.dumps(_row()["payload_json"])), _row(payload=b'{"fileName":"second.png"}')])
    token = set_project_id("project-1")
    try:
        result = await inspect_canvas("list_elements", page=3, types=["image"], repository=repo)
    finally:
        reset_project_id(token)

    assert repo.list_args == ("project-1", ["image"], 21, 40)
    assert result == {
        "elements": [
            {
                "id": "element-1", "type": "image", "version": 7, "zIndex": 2,
                "frameId": "frame-1", "layout": {"x": 10, "y": 20, "width": 640, "height": 480, "visible": True},
                "fileName": "reference.png", "status": "ready", "resolvable": True,
            },
            {
                "id": "element-1", "type": "image", "version": 7, "zIndex": 2,
                "frameId": "frame-1", "fileName": "second.png", "resolvable": False,
            },
        ],
        "page": 3,
        "pageSize": 20,
        "hasMore": False,
    }
    serialized = json.dumps(result)
    for secret in ("private-project", "private-file", "private.example", "private prompt", "private timestamp", "ossKey"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_list_excludes_non_scalar_or_wrongly_typed_layout_values():
    """Nested canvas payload values must not cross the model-facing projection."""
    from cfgpu_mcp.service.canvas import inspect_canvas

    repo = _CanvasRepo(rows=[_row(payload={
        "x": True, "y": [20], "width": 640, "height": "480", "visible": True,
        "locked": 1, "rotation": 0.5, "opacity": False,
    })])
    token = set_project_id("project-1")
    try:
        result = await inspect_canvas("list_elements", repository=repo)
    finally:
        reset_project_id(token)

    assert result["elements"][0]["layout"] == {"width": 640, "visible": True, "rotation": 0.5}


@pytest.mark.asyncio
async def test_resolve_requires_current_version_and_an_allowlisted_object_key():
    from cfgpu_mcp.service.canvas import CanvasAccessError, inspect_canvas

    repo = _CanvasRepo(row=_row(kind="ai_video", version=7))
    token = set_project_id("project-1")
    try:
        result = await inspect_canvas("resolve_element", element_id="element-1", version=7, repository=repo)
        with pytest.raises(CanvasAccessError, match="version changed"):
            await inspect_canvas("resolve_element", element_id="element-1", version=8, repository=repo)
        repo.row = _row(payload={"ossKey": "other/private.mp4"})
        with pytest.raises(CanvasAccessError, match="usable object"):
            await inspect_canvas("resolve_element", element_id="element-1", version=7, repository=repo)
    finally:
        reset_project_id(token)

    assert repo.resolve_args == ("project-1", "element-1")
    assert result == {"id": "element-1", "type": "ai_video", "version": 7, "urls": ["agent-artifacts/p1/reference.png"], "label": "reference.png"}


@pytest.mark.asyncio
async def test_repository_uses_parameterized_project_scoped_sql():
    from cfgpu_mcp.client.canvas_repo import CanvasRepository

    calls = []

    class Cursor:
        async def execute(self, query, params):
            calls.append((query, params))

        async def fetchall(self):
            return []

        async def fetchone(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Pool:
        def acquire(self):
            return Connection()

    repo = CanvasRepository(Pool())
    await repo.list_elements("project-1", ["image", "video"], 21, 40)
    await repo.get_element("project-1", "element-1")

    list_sql, list_params = calls[0]
    assert "canvas_element" in list_sql
    assert "project_id = %s AND is_delete = 0" in list_sql
    assert "ORDER BY z_index, element_id" in list_sql
    assert "LIMIT %s OFFSET %s" in list_sql
    assert list_params == ("project-1", "image", "video", 21, 40)
    resolve_sql, resolve_params = calls[1]
    assert "element_id = %s" in resolve_sql
    assert resolve_params == ("project-1", "element-1")


def test_canvas_db_expands_each_required_field_and_names_the_missing_field(monkeypatch):
    from cfgpu_mcp.settings import _parse_canvas_db

    monkeypatch.setenv("CANVAS_HOST", "db.internal")
    monkeypatch.setenv("CANVAS_PORT", "3307")
    monkeypatch.setenv("CANVAS_DATABASE", "canvas")
    monkeypatch.setenv("CANVAS_USER", "reader")
    monkeypatch.setenv("CANVAS_PASSWORD", "secret")
    canvas_db = _parse_canvas_db({
        "host": "$CANVAS_HOST", "port": "$CANVAS_PORT", "database": "$CANVAS_DATABASE",
        "user": "$CANVAS_USER", "password": "$CANVAS_PASSWORD",
    })
    assert canvas_db.host == "db.internal"
    assert canvas_db.port == 3307
    assert canvas_db.pool_min == 1
    assert canvas_db.pool_max == 5

    monkeypatch.delenv("CANVAS_PASSWORD")
    with pytest.raises(ValueError, match=r"canvas_db.password.*CANVAS_PASSWORD"):
        _parse_canvas_db({"host": "db", "port": "3306", "database": "canvas", "user": "reader", "password": "$CANVAS_PASSWORD"})


@pytest.mark.asyncio
async def test_http_middleware_binds_and_resets_project_context():
    from cfgpu_mcp.context import get_project_id
    from cfgpu_mcp.http_app import RequestContextMiddleware

    seen = []

    async def inner(scope, receive, send):
        seen.append(get_project_id())

    async def receive():
        return {}

    async def send(message):
        return None

    await RequestContextMiddleware(inner)(
        {"type": "http", "headers": [(b"x-cfdream-project-id", b"project-1")]}, receive, send
    )
    assert seen == ["project-1"]
    assert get_project_id() is None


def test_inspect_canvas_is_registered_with_only_flat_model_arguments(monkeypatch):
    from cfgpu_mcp.settings import Settings

    monkeypatch.setattr("cfgpu_mcp.config.get_settings", lambda: Settings())
    from cfgpu_mcp import server

    tool = server.mcp._tool_manager.get_tool("inspect_canvas")
    assert tool is not None
    assert set(tool.parameters["properties"]) == {"action", "page", "types", "element_id", "version"}
    assert "project_id" not in tool.parameters["properties"]


def test_inspect_canvas_description_guides_positional_lookup(monkeypatch):
    from cfgpu_mcp.settings import Settings

    monkeypatch.setattr("cfgpu_mcp.config.get_settings", lambda: Settings())
    from cfgpu_mcp import server

    tool = server.mcp._tool_manager.get_tool("inspect_canvas")
    description = tool.description or ""
    for guidance in (
        "by canvas position",
        "hasMore",
        "layout.y",
        "layout.x",
        "resolvable=true",
        "presentation or vision tool",
        "rather than guess",
    ):
        assert guidance in description
