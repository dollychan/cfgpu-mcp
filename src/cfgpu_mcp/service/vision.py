from __future__ import annotations

from typing import Any

from cfgpu_mcp.errors import CFGPUError
from cfgpu_mcp.tool_registry import UnderstandVisionInput


async def understand_vision(
    prompt: str,
    model: str | list[str] = "auto",
    images: list[str] | None = None,
    video: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    return_metadata: bool = True,
    model_specific: dict | None = None,
) -> dict[str, Any]:
    from cfgpu_mcp.config import get_client, get_task_repository, get_registry
    from cfgpu_mcp.router import ModelRouter
    from cfgpu_mcp.task_manager import TaskManager

    req = UnderstandVisionInput(
        prompt=prompt,
        model=model,
        images=images,
        video=video,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        return_metadata=return_metadata,
        model_specific=model_specific,
    )

    registry = get_registry()
    router = ModelRouter(registry)
    adapter = router.resolve(req)

    client = get_client()
    repo = await get_task_repository()
    tm = TaskManager(client, repo)

    # Vision-understanding models are synchronous: create() POSTs and parses the
    # chat/completions response in one shot, so the result is ready immediately.
    try:
        task = await tm.create(adapter, req)
    except CFGPUError as e:
        e.adapter_id = adapter.adapter_id
        raise

    result = task.result or {}
    # The real per-model API request is always surfaced, regardless of return_metadata.
    payload = task.public_payload()
    out: dict[str, Any] = {"text": result.get("text", ""), "payload": payload}
    if return_metadata:
        out["model_used"] = result.get("model_used")
        out["usage"] = result.get("usage")
        if result.get("reasoning") is not None:
            out["reasoning"] = result["reasoning"]
    return out
