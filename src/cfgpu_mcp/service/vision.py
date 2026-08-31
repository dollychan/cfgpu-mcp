from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import RegionSpec, UnderstandVisionInput


async def understand_vision(
    prompt: str,
    model: str | list[str] = "auto",
    images: list[str] | None = None,
    video: str | None = None,
    regions: list[RegionSpec] | list[dict] | None = None,
    image_refs: list[str] | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    from cfgpu_mcp.config import client_for, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager, validate_request

    req = UnderstandVisionInput(
        prompt=prompt,
        model=model,
        images=images,
        video=video,
        regions=regions,
        image_refs=image_refs,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        return_metadata=return_metadata,
        model_specific=model_specific,
        validate_only=validate_only,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req, for_validation=validate_only)

    if validate_only:
        # Same branch, same position as the three generate_* services: before the
        # repository is acquired, so a request that is never submitted leaves no task
        # row, and errors are stamped exactly as on the billed path.
        #
        # **This tool carries the flag for a different reason than they do.** There is
        # no approval card in front of a vision call — it is synchronous and cheap. What
        # it lacked was a *preflight lane at all*: a host that runs one (DeerFlow's
        # `PreflightMiddleware`) sends `validate_only=True` as an ordinary tool argument,
        # and an undeclared argument is not an error — FastMCP's arg model inherits
        # pydantic's `extra="ignore"` and its `model_dump_one_level` walks declared
        # fields only, so the flag was dropped in silence and the "dry run" was a real,
        # billed vision call whose result was thrown away. A host cannot detect that from
        # the outside: the response of an ignored preflight is a perfectly normal answer.
        # Declaring the field is what makes the silence impossible.
        try:
            preflight = validate_request(adapter, req)
        except CFGPUError as e:
            e.model_id = adapter.model_name
            raise
        # No `stamp_echo`: understand_vision carries none of the three echo handles
        # (request_id / caption / label) — it is single-call and returns text, not an
        # artifact to correlate, describe or name.
        return preflight

    repo = await get_task_repository()
    tm = TaskManager(client_for, repo)

    # Vision-understanding models are synchronous: create() POSTs and parses the
    # chat/completions response in one shot, so the result is ready immediately.
    try:
        task = await tm.create(adapter, req)
    except CFGPUError as e:
        e.model_id = adapter.model_name
        raise

    result = task.result or {}
    # Chat-completion-shaped result: the answer is message.content (plus
    # reasoning_content for Thinking models). The real per-model API request is
    # always surfaced under "payload", regardless of return_metadata.
    payload = task.public_payload()
    out: dict[str, Any] = {
        "id": result.get("id"),
        "model": result.get("model"),
        "message": result.get("message"),
        "payload": payload,
    }
    if return_metadata:
        out["usage"] = result.get("usage")
    return out
