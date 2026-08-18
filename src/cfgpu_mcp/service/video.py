from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.task_manager import _ETA_KEY, Task
from cfgpu_mcp.tool_registry import GenerateVideoInput, lean_result, stamp_echo


def _handle(task: "Task", *, forced: bool) -> dict[str, Any]:
    """The submission receipt: task_id, status, and the upstream's ETA if it gave one.

    ``next_step`` is spelled out because this is the whole point of the async shape —
    a caller that walks away without polling has produced a video nobody will collect.
    ``forced`` is surfaced rather than silently swallowed: the caller asked to wait
    and did not get to, and a tool that quietly ignores an argument is worse than one
    that explains why.
    """
    out: dict[str, Any] = {"task_id": task.id, "status": task.status}
    eta = task.payload.get(_ETA_KEY) or {}
    out.update({k: v for k, v in eta.items() if v is not None})
    out["next_step"] = f"用 task_status('{task.id}') 查询进度与结果"
    if forced:
        out["note"] = (
            "该模型固定异步返回：它跑在单卡串行队列上，等待时间可达数十分钟，"
            "远超工具调用的连接上限。同步等待会在拿到结果前断连，反而丢掉 task_id。"
        )
    return out


async def generate_video(
    prompt: str = "",
    model: str | list[str] = "auto",
    first_frame: str | None = None,
    last_frame: str | None = None,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    duration_seconds: int | None = None,
    aspect_ratio: str = "adaptive",
    resolution: str = "720p",
    with_audio: bool = True,
    quality_tier: str = "balanced",
    watermark: bool | None = None,
    wait: bool = True,
    timeout: int | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
    request_id: str | None = None,
    caption: str | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager, validate_request

    req = GenerateVideoInput(
        prompt=prompt,
        model=model,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        with_audio=with_audio,
        quality_tier=quality_tier,
        watermark=watermark,
        wait=wait,
        timeout=timeout,
        return_metadata=return_metadata,
        model_specific=model_specific,
        request_id=request_id,
        caption=caption,
        validate_only=validate_only,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req)

    if validate_only:
        # Branches before the repository is acquired so an unsubmitted request leaves no
        # task row; see the same block in service/image.py for the full rationale. Note
        # this also precedes the force_async override below — a preflight reports
        # `is_async` and returns, it never has a submission to force.
        try:
            preflight = validate_request(adapter, req)
        except CFGPUError as e:
            e.model_id = adapter.model_name
            e.request_id = request_id
            raise
        return stamp_echo(preflight, request_id=request_id)

    repo = await get_task_repository()
    tm = TaskManager(client_for, repo)

    try:
        task = await tm.create(adapter, req)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = request_id
        raise

    # ★ force_async overrides the caller's `wait`. Not a preference — a correctness
    #   fix. This model's latency is dominated by an unbounded serial-GPU queue, so
    #   any blocking wait outlives the MCP client's own request timeout: the client
    #   gives up, the session is torn down, and the response carrying the task_id
    #   never arrives. The caller is then stuck waiting on a job that is running
    #   fine but that it can no longer name, poll, or cancel — which is precisely
    #   how 2026-08-14 played out. Returning the handle immediately is the only
    #   shape where the caller always keeps a way back to its own work.
    if adapter.force_async or not wait:
        return stamp_echo(
            _handle(task, forced=adapter.force_async and wait),
            request_id=request_id,
            caption=caption,
        )

    try:
        task = await tm.wait(task, adapter, req, timeout=timeout)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        e.request_id = request_id
        raise

    if task.result is None:
        return stamp_echo({"task_id": task.id, "status": task.status}, request_id=request_id, caption=caption)

    result = task.result
    # The real per-model API request is always surfaced, regardless of return_metadata.
    payload = task.public_payload()
    if not return_metadata:
        return stamp_echo(lean_result(result, payload), request_id=request_id, caption=caption)
    return stamp_echo({**result, "payload": payload}, request_id=request_id, caption=caption)
