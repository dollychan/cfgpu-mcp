from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

#: How many *consecutive* retryable poll failures ``wait()`` absorbs before giving up.
#:
#: A poll failing is not the task failing — the job keeps running upstream whatever
#: happens on this socket. Aborting on the first one turns any brief upstream hiccup
#: (a co-located gateway that stops answering while it uploads an artifact, a proxy
#: restart, one slow response past ``http_timeout``) into a lost 20-minute video that
#: the caller cannot even resume, because with ``wait=True`` they never received the
#: task_id. So transient poll errors are absorbed and the *poll deadline* is the real
#: bound. This ceiling only exists so a genuinely dead upstream doesn't hold the caller
#: for the full timeout; either way the error raised carries the task_id.
MAX_CONSECUTIVE_POLL_FAILURES = 5

#: Hard ceiling on how long a single tool call may block, whatever the adapter's
#: ``poll_config.default_timeout`` says and whatever the caller passes as ``timeout``.
#:
#: ★ The bound that matters is not ours — it is the MCP client's own request timeout,
#: which is typically 60s and never minutes. Blocking past it does not buy patience;
#: it destroys the call: the client gives up, the session is torn down, and the
#: ``CFGPUError.timeout`` we would have raised (task_id and all) is never delivered.
#: The caller is then left waiting on a job it can no longer name, resume, or cancel.
#: That is exactly what happened on 2026-08-14 — see the incident note in
#: models/cfdream-minimax-h3/adapter.yaml.
#:
#: 10 minutes is already far past any client's patience; it exists only so a
#: misconfigured ``default_timeout`` cannot reintroduce an unbounded wait. Models
#: whose real latency approaches this should set ``force_async: true`` instead of
#: raising it — waiting longer is not the fix, not waiting is.
MAX_WAIT_SECONDS = 600

#: Resolves the client for a given adapter's upstream — see ``config.client_for``.
ClientResolver = Callable[["ModelAdapter"], CFGPUClient]

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

# Reserved stored-payload keys holding the caller's own echo fields — the ``request_id``
# correlation handle plus the ``caption`` artifact description and the ``label`` artifact
# name (see tool_registry.stamp_echo). Stashed here — alongside _ASPECT_RATIO_KEY — so
# task_status/task_wait can recover and echo them from the DB row without a schema/column
# change. This is what makes them survive the async hop with no state on the caller's
# side: both are supplied on generate but the artifact only exists at task_wait. Like the
# aspect-ratio echo they are internal-only: create() POSTs the clean build_payload()
# output and only augments the stored copy, and public_payload() strips them, so they
# never reach the upstream API.
_REQUEST_ID_KEY = "_request_id"
_CAPTION_KEY = "_caption"
_LABEL_KEY = "_label"

# The echo keys as one tuple, because ``public_payload`` has to strip every one of them
# and that list is the thing that grows: adding a fourth echo field and forgetting the
# strip would leak an internal key into the payload every caller sees, silently.
_ECHO_PAYLOAD_KEYS = (_REQUEST_ID_KEY, _CAPTION_KEY, _LABEL_KEY)

# The upstream's own ETA for this submission, captured from the POST response by
# adapter.extract_eta(). Stashed like the keys above so the force-async return path
# and later task_status calls can both surface it.
#
# ★ Deliberately the *upstream's* number, never one computed here. The dominant term
# is queue depth on a single serial GPU, which only the gateway can see; a duplicate
# formula on this side would drift from the one that actually schedules the work.
_ETA_KEY = "_eta"


def _stash_internal(payload: dict, req: Any, *, aspect_ratio: bool) -> dict:
    """Return a copy of ``payload`` augmented with the reserved internal keys.

    Adds the caller's ``request_id`` / ``caption`` / ``label`` (when supplied) so
    task_status/task_wait can echo them, and — for async tasks (``aspect_ratio=True``) —
    the requested aspect_ratio echo that poll() falls back to. Returns ``payload``
    unchanged when nothing needs stashing.
    """
    extra: dict[str, Any] = {}
    if aspect_ratio:
        extra[_ASPECT_RATIO_KEY] = getattr(req, "aspect_ratio", None)
    request_id = getattr(req, "request_id", None)
    if request_id:
        extra[_REQUEST_ID_KEY] = request_id
    caption = getattr(req, "caption", None)
    if caption:
        extra[_CAPTION_KEY] = caption
    label = getattr(req, "label", None)
    if label:
        extra[_LABEL_KEY] = label
    return {**payload, **extra} if extra else payload

# Internal (already normalized via _STATUS_MAP) terminal statuses. Raw API
# values like "completed" never reach here — they map to "succeeded" first.
_TERMINAL_STATUSES = {"succeeded", "failed"}

# Only media-producing tasks require a URL or inline blob. Vision understanding is
# synchronous too, but its artifact is a text message and must not hit this guard.
_MEDIA_TASK_TYPES = {"image", "video", "audio"}


def _has_media_artifact(result: dict[str, Any]) -> bool:
    """Return whether a normalized media result contains either artifact shape."""
    return bool(result.get("urls") or result.get("inline_media"))


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


_RESPONSE_EXCERPT_CHARS = 400


def _truncate_json(resp: dict) -> str:
    """Compact JSON excerpt of an upstream response, for error messages.

    A "couldn't parse the response" failure is undiagnosable without seeing what
    actually came back — the raw body otherwise lives only in ``original``, which
    the tool layer does not surface, and in DEBUG logs nobody has enabled. Bounded
    so a large body can't flood the model's context.
    """
    try:
        text = json.dumps(resp, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(resp)
    if len(text) > _RESPONSE_EXCERPT_CHARS:
        text = text[:_RESPONSE_EXCERPT_CHARS] + "…"
    return text


def _dashscope_output_reason(resp: dict) -> str | None:
    """Failure reason from a DashScope-shaped ``output`` envelope.

    HappyHorse and the 万相 video family nest the *task record* under ``output``
    and carry the reason on ``output.code`` / ``output.message`` (both ``null``
    on success). Read here rather than through the generic walk below because
    the key is ``message``: at the top level that word is unusable (the image
    API sets it to "success" even for a failed task), but inside ``output`` it
    describes the task itself, not the query.

    Both halves are kept when both are present — the code (e.g.
    ``InvalidParameter.DataInspection``, a content-moderation rejection) is
    often the only part that says *why*, while the message can be generic.
    """
    output = resp.get("output")
    if not isinstance(output, dict):
        return None
    parts = [
        val.strip()
        for val in (output.get("code"), output.get("message"))
        if isinstance(val, str) and val.strip()
    ]
    return ": ".join(parts) or None


def _extract_error_message(resp: dict) -> str | None:
    """Best-effort failure reason from a poll response, tolerant of shape.

    Upstreams disagree on where the reason lives: WAN video carries a top-level
    ``error`` that is ``null`` on success and a dict on failure; Submodel nests
    it under ``task.error``; gpt-image-2 / nano image tasks nest the reason under
    ``data.error_msg`` (e.g. an Azure OpenAI safety-system rejection); the
    DashScope-shaped video APIs use ``output.code`` / ``output.message``. Returns
    None when the upstream genuinely gives nothing — callers supply a fallback.
    Deliberately ignores the top-level ``message`` field because the image API
    sets it to "success" (the query succeeded) even for failed tasks.
    """
    data = resp.get("data")
    task = resp.get("task")
    for container in (
        resp,
        data if isinstance(data, dict) else None,
        task if isinstance(task, dict) else None,
    ):
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
    return _dashscope_output_reason(resp)


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
        the unified tool schema. The reserved internal keys (``_requested_aspect_ratio``
        for async re-polling, ``_request_id`` / ``_caption`` / ``_label`` for the
        caller's echo fields) are never part of the real request, so they are stripped
        here.
        """
        return {
            k: v for k, v in self.payload.items()
            if k not in (_ASPECT_RATIO_KEY, *_ECHO_PAYLOAD_KEYS)
        }


def single_client(client: CFGPUClient) -> ClientResolver:
    """Adapt one client into a resolver, for single-upstream callers and tests."""
    return lambda _adapter: client


def _corrected_args(
    adapter: "ModelAdapter",
    req: Any,
    adapter_corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The tool-argument overrides a caller should apply before submitting for real.

    Expressed in *tool* parameter names, so a caller merges it over the original
    arguments (``{**args, **corrected_args}``) and needs no per-model knowledge to do
    so. ``payload`` cannot serve this purpose: it speaks the upstream dialect
    (``cfgpu_model_id``, ``video_length``, ``resolution_name``) and would have to be
    translated back.

    Adapter-provided safe fallbacks (for example an unsupported resolution mapped to
    the nearest supported tier) are written first. A delegated model choice is also
    pinned so the approval and billed calls use the same concrete model.

    An explicitly named model is **never** rewritten, not even normalized to
    ``model_name``. ``AdapterRegistry.get`` already resolves adapter_id /
    cfgpu_model_id / display_name to the same adapter, so rewriting would change the
    name on the card without changing what runs — the one edit with cost and no effect.

    Pinning does trade away ``auto``'s failover: a model that becomes unavailable
    between preflight and submission now fails hard instead of routing on to the next
    candidate. That is the intended trade — an approval only means something if it
    names what runs — and the failure is loud and retryable.
    """
    corrected = dict(adapter_corrections or {})
    if isinstance(req.model, list) or req.model == "auto":
        corrected["model"] = adapter.model_name
    return corrected


def validate_request(
    adapter: "ModelAdapter",
    req: Any,
) -> dict[str, Any]:
    """Run a request to the edge of the upstream POST and report what it would send.

    This is the ``validate_only`` path (see ``tool_registry.validate_only_field``). By
    the time it is called the Pydantic validators and model resolution have run. Here
    the chosen adapter applies safe enum fallbacks, runs ``supports()`` against the
    effective request, and finally calls ``build_payload()``. The returned payload and
    ``corrected_args`` therefore describe the same request.

    Deliberately synchronous and free of both collaborators ``create()`` needs: it
    performs no IO, so it needs no client, and it writes no task row, so it needs no
    repository. That is not an optimization — a task that was never submitted must not
    appear in the task table, or ``task_status`` gains rows for work nobody started.
    Callers therefore branch to it *before* acquiring the repository.

    Raises whatever ``build_payload`` raises, unchanged; the service layer stamps
    ``model_id`` / ``request_id`` onto it exactly as it does for the billed path.
    """
    adapter_corrections = adapter.validation_corrections(req)
    effective_req = req.model_copy(update=adapter_corrections) if adapter_corrections else req
    ok, reason = adapter.supports(effective_req)
    if not ok:
        raise CFGPUError(
            error_type="invalid_params",
            user_message=reason,
            original={"model": req.model},
            model_id=adapter.model_name,
        )
    payload = adapter.build_payload(effective_req)
    return {
        "validated": True,
        # The concrete model, so `model="auto"` reports what routing actually picked —
        # this is what an approval card must show, since "auto" names nothing a human
        # can weigh. `model_name` is the only public identifier (never adapter_id).
        "model_used": adapter.model_name,
        "task_type": adapter.task_type,
        # Both carried because they change what approving this means: an async task
        # returns a handle to poll rather than a result, and the tiers are the closest
        # thing to a cost signal available before the call.
        "is_async": adapter.is_async,
        "cost_tier": adapter.cost_tier,
        "speed_tier": adapter.speed_tier,
        # What to change before submitting for real; empty when nothing needs changing.
        # `model_used` reports, this one instructs — a caller merging blindly gets the
        # right call, and never has to decide for itself whether a rewrite is allowed.
        "corrected_args": _corrected_args(adapter, req, adapter_corrections),
        # The exact upstream request. Routed to structuredContent by the MCP layer's
        # `split_structured`, like every other `payload`, so it stays out of the model's
        # context while remaining available to the host rendering the approval.
        "payload": payload,
    }


class TaskManager:
    def __init__(self, client_for: ClientResolver, repo: TaskRepository) -> None:
        """``client_for`` resolves the upstream client from the adapter in hand.

        It is a *function of the adapter* rather than a client chosen up front
        because of the polling path: ``task_status`` / ``task_wait`` start from a
        bare task_id and only learn which adapter — hence which provider, hence
        which base_url and credential — owns it after reading the DB row, which
        happens inside this class. A client picked before that read would already
        be the wrong one for any model not served by CFGPU itself.

        Callers with a single upstream wrap it with ``single_client``. Accepting
        both shapes here instead would mean sniffing "is this a client or a
        factory?", which a test double satisfies both ways.
        """
        self._client_for = client_for
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
            resp = await self._client_for(adapter).post(adapter.endpoint, payload)
            result: NormalizedResult = adapter.parse_response(resp)
            # Always stamp the public model_name — adapters may set model_used from the
            # upstream response's echoed "model" field, which is the internal
            # cfgpu_model_id and must never reach the caller.
            result.model_used = adapter.model_name
            if not result.aspect_ratio:  # adapter didn't echo ratio → fall back to request
                result.aspect_ratio = getattr(req, "aspect_ratio", None)  # audio reqs have none
            result_dict = result.to_dict(return_metadata=True)
            if (
                adapter.task_type in _MEDIA_TASK_TYPES
                and not _has_media_artifact(result_dict)
            ):
                # Keep the synchronous path under the same success invariant as
                # poll(): a media generation call cannot succeed without media. This
                # also catches an upstream HTTP-200 business error whose response
                # envelope an adapter does not yet recognise.
                raise CFGPUError(
                    error_type="task_failed",
                    user_message=(
                        "同步媒体 API 返回成功，但没有返回任何产物 URL 或内联媒体。"
                        f"上游响应（截断）：{_truncate_json(resp)}"
                    ),
                    original={"adapter_id": adapter.adapter_id, "response": resp},
                    retryable=False,
                )
            stored_payload = _stash_internal(payload, req, aspect_ratio=False)
            await self._repo.insert_task(task_id, adapter.adapter_id, "succeeded", stored_payload)
            await self._repo.update_task(task_id, "succeeded", result=result_dict)
            # Every field is known here — build the Task in memory instead of re-reading.
            return Task(_now_row(task_id, adapter.adapter_id, "succeeded", stored_payload, result=result_dict))

        # Async model: POST → get task_id from CFGPU → write pending
        resp = await self._client_for(adapter).post(adapter.endpoint, payload)
        cfgpu_task_id = adapter.extract_task_id(resp)
        if not cfgpu_task_id:
            # Without a real task_id we'd poll a bogus URL until timeout and
            # report a misleading "timeout" error. Fail loudly with the raw
            # response so the response-shape change is diagnosable.
            raise CFGPUError(
                error_type="unknown",
                user_message=(
                    "提交任务成功但未能从响应中解析出 task_id，可能是 API 响应结构变化。"
                    f"响应原文（截断）：{_truncate_json(resp)}"
                ),
                original={"adapter_id": adapter.adapter_id, "response": resp},
            )
        stored_payload = _stash_internal(payload, req, aspect_ratio=True)
        # Validated, not trusted: the stored payload is json.dumps()'d straight into
        # the repository, so anything unserializable here fails the *insert* and loses
        # the task outright — the caller would be told the submission failed while the
        # job runs on upstream, unreachable. An ETA is never worth that, so keep only
        # plain numbers and drop the rest silently.
        eta = adapter.extract_eta(resp)
        if isinstance(eta, dict):
            clean = {k: v for k, v in eta.items() if isinstance(v, int | float)}
            if clean:
                stored_payload = {**stored_payload, _ETA_KEY: clean}
        await self._repo.insert_task(cfgpu_task_id, adapter.adapter_id, "pending", stored_payload)
        return Task(_now_row(cfgpu_task_id, adapter.adapter_id, "pending", stored_payload))

    # ── Poll ─────────────────────────────────────────────────────────────────

    async def poll(self, task: Task, adapter: "ModelAdapter") -> Task:
        assert adapter.poll_endpoint, f"{adapter.adapter_id} has no poll_endpoint"
        path = adapter.poll_endpoint.replace("{task_id}", task.id)
        resp = await self._client_for(adapter).get(path)

        cfgpu_status: str = adapter.extract_status(resp)
        status = _STATUS_MAP.get(cfgpu_status, "running")

        result_dict: dict | None = None
        error_msg: str | None = None

        if status == "succeeded":
            result: NormalizedResult = adapter.parse_response(resp)
            # Always stamp the public model_name — see the same override in create().
            result.model_used = adapter.model_name
            if not result.aspect_ratio:  # adapter didn't echo ratio → fall back to request
                result.aspect_ratio = task.payload.get(_ASPECT_RATIO_KEY)
            result_dict = result.to_dict(return_metadata=True)
            if not _has_media_artifact(result_dict):
                # Upstream reports success but yields no artifact at all — treat as a
                # terminal failure so it converges instead of re-polling forever.
                # Both artifact shapes count: some providers return media inline
                # (base64 blob, no URL) instead of a downloadable link, so keep this
                # check in step with annotate_artifact()'s urls-or-inline_media test.
                status = "failed"
                result_dict = None
                error_msg = "Task reported success but returned no artifact URLs or inline media"
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

        requested = timeout if timeout is not None else adapter.estimate_poll_timeout(req)
        # Clamped even when the caller named the number explicitly: a wait that
        # outlives the MCP client's own timeout cannot deliver its result, so
        # honouring a larger value would only convert a returnable answer into a
        # dropped connection. See MAX_WAIT_SECONDS.
        effective_timeout = min(requested, MAX_WAIT_SECONDS)
        if requested > effective_timeout:
            logger.info(
                "任务 %s 的等待预算 %ds 收敛到上限 %ds；超出部分请改用 task_status 轮询",
                task.id, requested, effective_timeout,
            )
        poll_cfg = adapter.poll_config
        interval = poll_cfg.base_interval if poll_cfg else 5.0
        max_interval = poll_cfg.max_interval if poll_cfg else 20.0
        backoff = poll_cfg.backoff_factor if poll_cfg else 1.3

        start = time.monotonic()
        consecutive_failures = 0

        while task.status not in _TERMINAL_STATUSES:
            await asyncio.sleep(interval)
            elapsed = int(time.monotonic() - start)

            if elapsed >= effective_timeout:
                raise CFGPUError.timeout(task.id, elapsed)

            try:
                task = await self.poll(task, adapter)
            except CFGPUError as e:
                # A failed poll says nothing about the task — it is still running
                # upstream. Absorb the transient ones (see
                # MAX_CONSECUTIVE_POLL_FAILURES) rather than destroying a job that
                # is fine. Non-retryable ones (4xx: bad token, unknown task) will
                # not fix themselves by asking again, so they still abort — but
                # with the task_id attached, which is the caller's only way back
                # to the artifact on a wait=True call.
                e.original.setdefault("task_id", task.id)
                if not e.retryable:
                    raise
                consecutive_failures += 1
                logger.warning(
                    "轮询任务 %s 失败（连续第 %d/%d 次，已等待 %ds）：%s",
                    task.id, consecutive_failures, MAX_CONSECUTIVE_POLL_FAILURES,
                    elapsed, e.user_message,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    raise CFGPUError(
                        error_type=e.error_type,
                        user_message=(
                            f"连续 {consecutive_failures} 次查询任务状态都失败，放弃等待。"
                            f"任务 {task.id} 很可能仍在上游运行，可用 task_status 查询。"
                            f"最后一次的原因：{e.user_message}"
                        ),
                        original={"task_id": task.id, "elapsed": elapsed, "last_error": e.original},
                        retryable=True,
                    ) from e
                interval = min(interval * backoff, max_interval)
                continue
            consecutive_failures = 0

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
