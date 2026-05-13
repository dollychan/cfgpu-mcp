from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiosqlite

from cfgpu_mcp.client import db as db_ops
from cfgpu_mcp.client.cfgpu_client import CFGPUClient
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

_TERMINAL_STATUSES = {"succeeded", "failed", "completed"}


class Task:
    def __init__(self, row: dict) -> None:
        self.id: str = row["id"]
        self.adapter_id: str = row["adapter_id"]
        self.status: str = row["status"]
        self.payload: dict = row["payload"]
        self.result: dict | None = row.get("result")
        self.error: str | None = row.get("error")
        self.created_at: float = row["created_at"]
        self.updated_at: float = row["updated_at"]

    def to_dict(self) -> dict:
        return {
            "task_id": self.id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    def __init__(self, client: CFGPUClient, db: aiosqlite.Connection) -> None:
        self._client = client
        self._db = db

    # ── Create ───────────────────────────────────────────────────────────────

    async def create(
        self,
        adapter: "ModelAdapter",
        req: "GenerateImageInput | GenerateVideoInput",
    ) -> Task:
        payload = adapter.build_payload(req)
        task_id = str(uuid.uuid4())

        if not adapter.is_async:
            # Synchronous model: POST → parse response immediately
            resp = await self._client.post(adapter.endpoint, payload)
            result: NormalizedResult = adapter.parse_response(resp)
            await db_ops.insert_task(self._db, task_id, adapter.adapter_id, "succeeded", payload)
            await db_ops.update_task(self._db, task_id, "succeeded", result=result.to_dict(return_metadata=True))
            row = await db_ops.get_task(self._db, task_id)
            return Task(row)  # type: ignore[arg-type]

        # Async model: POST → get task_id from CFGPU → write pending
        resp = await self._client.post(adapter.endpoint, payload)
        cfgpu_task_id: str = adapter.extract_task_id(resp) or task_id
        await db_ops.insert_task(self._db, cfgpu_task_id, adapter.adapter_id, "pending", payload)
        row = await db_ops.get_task(self._db, cfgpu_task_id)
        return Task(row)  # type: ignore[arg-type]

    # ── Poll ─────────────────────────────────────────────────────────────────

    async def poll(self, task: Task, adapter: "ModelAdapter") -> Task:
        assert adapter.poll_endpoint, f"{adapter.adapter_id} has no poll_endpoint"
        path = adapter.poll_endpoint.replace("{task_id}", task.id)
        resp = await self._client.get(path)

        cfgpu_status: str = adapter.extract_status(resp)
        # Normalize CFGPU status to our internal vocabulary
        status_map = {
            "completed": "succeeded",
            "succeed": "succeeded",
            "succeeded": "succeeded",
            "failed": "failed",
            "error": "failed",
            "running": "running",
            "pending": "pending",
            "processing": "running",
        }
        status = status_map.get(cfgpu_status, "running")

        result_dict: dict | None = None
        error_msg: str | None = None

        if status == "succeeded":
            result: NormalizedResult = adapter.parse_response(resp)
            result_dict = result.to_dict(return_metadata=True)
        elif status == "failed":
            error_msg = resp.get("error", {}).get("message") or "Task failed"

        await db_ops.update_task(self._db, task.id, status, result=result_dict, error=error_msg)
        row = await db_ops.get_task(self._db, task.id)
        return Task(row)  # type: ignore[arg-type]

    # ── Wait ─────────────────────────────────────────────────────────────────

    async def wait(
        self,
        task: Task,
        adapter: "ModelAdapter",
        req: "GenerateImageInput | GenerateVideoInput",
        timeout: int | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> Task:
        # Synchronous model: already done
        if not adapter.is_async:
            return task

        effective_timeout = timeout if timeout is not None else adapter.estimate_poll_timeout(req)
        poll_cfg = adapter.poll_config
        interval = poll_cfg.base_interval if poll_cfg else 5.0
        max_interval = poll_cfg.max_interval if poll_cfg else 20.0
        backoff = poll_cfg.backoff_factor if poll_cfg else 1.3

        start = time.monotonic()

        while task.status not in _TERMINAL_STATUSES:
            await asyncio.sleep(interval)
            elapsed = int(time.monotonic() - start)

            if elapsed >= effective_timeout:
                raise CFGPUError.timeout(task.id, elapsed)

            task = await self.poll(task, adapter)

            if progress_callback:
                await progress_callback({
                    "status": task.status,
                    "elapsed": elapsed,
                    "timeout": effective_timeout,
                    "task_id": task.id,
                })

            interval = min(interval * backoff, max_interval)

        if task.status == "failed":
            raise CFGPUError(
                error_type="task_failed",
                user_message=task.error or "Task failed without error message",
                original={"task_id": task.id},
            )
        return task

    # ── Query ────────────────────────────────────────────────────────────────

    async def status(self, task_id: str) -> Task:
        row = await db_ops.get_task(self._db, task_id)
        if row is None:
            raise KeyError(f"Task {task_id!r} not found")
        return Task(row)

    async def list_running(self) -> list[Task]:
        rows = await db_ops.list_running_tasks(self._db)
        return [Task(r) for r in rows]
