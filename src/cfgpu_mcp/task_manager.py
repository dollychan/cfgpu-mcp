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

# Echoed aspect_ratio prefers the value the upstream actually returned (some
# APIs, e.g. WAN, report the resolved ratio in their response — important when
# the request asked for "adaptive"). The adapter's parse_response() sets it when
# present; only when it doesn't do we fall back to the *requested* ratio.
#
# That request echo isn't part of the upstream response, so async models (which
# finalize their result in poll(), with no access to the original request) can't
# recover it from the API. We stash it in the *stored* payload under this
# reserved key so poll() — including the task_status re-poll path that reads the
# row back from the DB — can fall back to it. This never reaches the upstream
# API: create() POSTs the clean payload and only augments the copy handed to the
# repository. payload is read back internally only (never re-POSTed), so the
# extra key is inert.
_ASPECT_RATIO_KEY = "_requested_aspect_ratio"

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


def _extract_error_message(resp: dict) -> str | None:
    """Best-effort failure reason from a poll response, tolerant of shape.

    Upstreams disagree on where the reason lives: WAN video carries a top-level
    ``error`` that is ``null`` on success and a dict on failure; gpt-image-2 /
    nano image tasks nest the reason under ``data.error_msg`` (e.g. an Azure
    OpenAI safety-system rejection). Returns None when the upstream genuinely
    gives nothing — callers supply a fallback. Deliberately ignores the
    top-level ``message`` field because the image API sets it to "success" (the
    query succeeded) even for failed tasks.
    """
    data = resp.get("data")
    for container in (resp, data if isinstance(data, dict) else None):
        if not container:
            continue
        err = container.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg")
            if msg:
                return str(msg)
        elif isinstance(err, str) and err.strip():
            return err.strip()
        for key in ("error_msg", "fail_reason", "failure_reason", "reason"):
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _now_row(
    task_id: str,
    adapter_id: str,
    status: str,
    payload: dict,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> dict:
    """Build an in-memory task row (created_at == updated_at == now).

    Lets create()/poll() return a Task from fields already in hand instead of a
    read-back round-trip. Timestamps aren't surfaced to callers, so a sub-millisecond
    drift from the persisted row is immaterial.
    """
    now = time.time()
    return {
        "id": task_id, "adapter_id": adapter_id, "status": status,
        "payload": payload, "result": result, "error": error,
        "created_at": now, "updated_at": now,
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

    def public_payload(self) -> dict:
        """The concrete upstream API request body for this task.

        ``payload`` is exactly what ``build_payload`` produced and POSTed to the
        model's specific CFGPU endpoint — i.e. the real per-model API request, not
        the unified tool schema. The reserved ``_requested_aspect_ratio`` key (see
        ``_ASPECT_RATIO_KEY``) is an internal echo we stash for async re-polling and
        is never part of the real request, so it is stripped here.
        """
        return {k: v for k, v in self.payload.items() if k != _ASPECT_RATIO_KEY}


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
            if not result.aspect_ratio:  # adapter didn't echo ratio → fall back to request
                result.aspect_ratio = getattr(req, "aspect_ratio", None)  # audio reqs have none
            result_dict = result.to_dict(return_metadata=True)
            await self._repo.insert_task(task_id, adapter.adapter_id, "succeeded", payload)
            await self._repo.update_task(task_id, "succeeded", result=result_dict)
            # Every field is known here — build the Task in memory instead of re-reading.
            return Task(_now_row(task_id, adapter.adapter_id, "succeeded", payload, result=result_dict))

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
        stored_payload = {**payload, _ASPECT_RATIO_KEY: getattr(req, "aspect_ratio", None)}
        await self._repo.insert_task(cfgpu_task_id, adapter.adapter_id, "pending", stored_payload)
        return Task(_now_row(cfgpu_task_id, adapter.adapter_id, "pending", stored_payload))

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
            if not result.aspect_ratio:  # adapter didn't echo ratio → fall back to request
                result.aspect_ratio = task.payload.get(_ASPECT_RATIO_KEY)
            result_dict = result.to_dict(return_metadata=True)
            if not result_dict.get("urls"):
                # Upstream reports success but yields no artifact URL — treat as a
                # terminal failure so it converges instead of re-polling forever.
                status = "failed"
                result_dict = None
                error_msg = "Task reported success but returned no artifact URLs"
        elif status == "failed":
            error_msg = _extract_error_message(resp) or (
                "Task failed (upstream reported no error detail)"
            )

        await self._repo.update_task(task.id, status, result=result_dict, error=error_msg)
        # Fields are all in scope — avoid a read-back round-trip; preserve created_at.
        return Task({
            "id": task.id, "adapter_id": task.adapter_id, "status": status,
            "payload": task.payload, "result": result_dict, "error": error_msg,
            "created_at": task.created_at, "updated_at": time.time(),
        })

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
