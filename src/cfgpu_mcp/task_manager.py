from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from cfgpu_mcp.client.cfgpu_client import CFGPUClient
from cfgpu_mcp.client.repository import TaskRepository
from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import NormalizedResult

if TYPE_CHECKING:
    from cfgpu_mcp.adapters.base import ModelAdapter
    from cfgpu_mcp.tool_registry import GenerateImageInput, GenerateVideoInput

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Internal (already normalized via _STATUS_MAP) terminal statuses. Raw API
# values like "completed" never reach here — they map to "succeeded" first.
_TERMINAL_STATUSES = {"succeeded", "failed"}

_STATUS_MAP = {
    "completed": "succeeded",
    "succeed": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "error": "failed",
    "running": "running",
    "pending": "pending",
    "processing": "running",
}


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
    def __init__(self, client: CFGPUClient, repo: TaskRepository) -> None:
        self._client = client
        self._repo = repo

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
            if not result.model_used:
                result.model_used = adapter.cfgpu_model_id
            await self._repo.insert_task(task_id, adapter.adapter_id, "succeeded", payload)
            await self._repo.update_task(task_id, "succeeded", result=result.to_dict(return_metadata=True))
            row = await self._repo.get_task(task_id)
            return Task(row)  # type: ignore[arg-type]

        # Async model: POST → get task_id from CFGPU → write pending
        resp = await self._client.post(adapter.endpoint, payload)
        cfgpu_task_id = adapter.extract_task_id(resp)
        if not cfgpu_task_id:
            # Without a real task_id we'd poll a bogus URL until timeout and
            # report a misleading "timeout" error. Fail loudly with the raw
            # response so the response-shape change is diagnosable.
            raise CFGPUError(
                error_type="unknown",
                user_message=(
                    "提交任务成功但未能从响应中解析出 task_id，可能是 API 响应结构变化。"
                ),
                original={"adapter_id": adapter.adapter_id, "response": resp},
            )
        await self._repo.insert_task(cfgpu_task_id, adapter.adapter_id, "pending", payload)
        row = await self._repo.get_task(cfgpu_task_id)
        return Task(row)  # type: ignore[arg-type]

    # ── Poll ─────────────────────────────────────────────────────────────────

    async def poll(self, task: Task, adapter: "ModelAdapter") -> Task:
        assert adapter.poll_endpoint, f"{adapter.adapter_id} has no poll_endpoint"
        path = adapter.poll_endpoint.replace("{task_id}", task.id)
        resp = await self._client.get(path)

        cfgpu_status: str = adapter.extract_status(resp)
        status = _STATUS_MAP.get(cfgpu_status, "running")

        result_dict: dict | None = None
        error_msg: str | None = None

        if status == "succeeded":
            result: NormalizedResult = adapter.parse_response(resp)
            if not result.model_used:
                result.model_used = adapter.cfgpu_model_id
            result_dict = result.to_dict(return_metadata=True)
        elif status == "failed":
            error_msg = resp.get("error", {}).get("message") or "Task failed"

        await self._repo.update_task(task.id, status, result=result_dict, error=error_msg)
        row = await self._repo.get_task(task.id)
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
        row = await self._repo.get_task(task_id)
        if row is None:
            raise KeyError(f"Task {task_id!r} not found")
        return Task(row)

    async def list_running(self) -> list[Task]:
        rows = await self._repo.list_running_tasks()
        return [Task(r) for r in rows]
