from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cfgpu_mcp.errors import tool_error_dict
from cfgpu_mcp.service import task as task_service
from cfgpu_mcp.tool_registry import annotate_artifact, split_structured


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def task_status(request_id: str) -> dict:
        """Query the status of a generation task.

        request_id — the value you passed to generate_image / generate_video /
        generate_audio. It **is** that task's key, so when a generate_* call did not come
        back with a result (it timed out, the connection dropped, or the server
        restarted), this finds out what actually happened to it. Do that before
        resubmitting: the generation may already have run and been charged for. A call
        that supplied no request_id got a server-generated id back as `task_id` — pass
        that same value here.

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
                annotate_artifact(await task_service.get_status(request_id)),
                # inline_media for the same reason generate_audio splits it out: a
                # sync inline-media task (MiniMax speech) is reachable here via
                # generate_audio(wait=False), and its base64 blob must ride
                # structuredContent rather than enter the model context.
                structured_keys=("usage", "payload", "inline_media"),
            )
        except Exception as e:
            return tool_error_dict(e)

    @mcp.tool()
    async def task_wait(request_id: str, timeout: Optional[int] = None) -> dict:
        """Wait for a generation task to complete and return the result.

        request_id — as for task_status: the value you passed to generate_*, or the
        `task_id` it returned when you passed none.
        """
        try:
            return split_structured(
                annotate_artifact(await task_service.wait_for_task(request_id, timeout)),
                # inline_media for the same reason generate_audio splits it out: a
                # sync inline-media task (MiniMax speech) is reachable here via
                # generate_audio(wait=False), and its base64 blob must ride
                # structuredContent rather than enter the model context.
                structured_keys=("usage", "payload", "inline_media"),
            )
        except Exception as e:
            return tool_error_dict(e)
