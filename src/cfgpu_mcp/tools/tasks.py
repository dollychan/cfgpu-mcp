from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.tool_registry import annotate_artifact, split_structured


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def task_status(task_id: str) -> dict:
        """Query the status of a generation task.

        task_id — the id returned by generate_image / generate_video / generate_audio.
        **If that call supplied a `request_id`, the two are the same value.** So when a
        generate_* call did not come back with a result (it timed out, the connection
        dropped, or the server restarted), pass its `request_id` here to find out what
        actually happened to it. Do that before resubmitting: the generation may already
        have run and been charged for.

        Beyond the usual `pending` / `running` / `succeeded`, two statuses describe a
        submission that has not reached the upstream service yet:

        - `submitting` — the request has provably not been sent. Nothing was charged;
          submitting the same job again is safe.
        - `dispatching` — the request may have been sent and its answer lost. It may
          already have been charged. Do **not** resubmit the same job; keep querying
          this id, or tell the user it needs checking.
        """
        try:
            return split_structured(
                annotate_artifact(await task_service.get_status(task_id)),
                # inline_media for the same reason generate_audio splits it out: a
                # sync inline-media task (MiniMax speech) is reachable here via
                # generate_audio(wait=False), and its base64 blob must ride
                # structuredContent rather than enter the model context.
                structured_keys=("usage", "payload", "inline_media"),
            )
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def task_wait(task_id: str, timeout: Optional[int] = None) -> dict:
        """Wait for a generation task to complete and return the result.

        task_id — as for task_status: the id generate_* returned, which is the same
        value as that call's `request_id` when it supplied one.
        """
        try:
            return split_structured(
                annotate_artifact(await task_service.wait_for_task(task_id, timeout)),
                # inline_media for the same reason generate_audio splits it out: a
                # sync inline-media task (MiniMax speech) is reachable here via
                # generate_audio(wait=False), and its base64 blob must ride
                # structuredContent rather than enter the model context.
                structured_keys=("usage", "payload", "inline_media"),
            )
        except Exception as e:
            return tool_error_dict(e)
