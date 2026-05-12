from __future__ import annotations

import asyncio
import sys

import click

from cfgpu_mcp.cli.output import print_error, print_result, run_with_progress


def _run(coro) -> dict:
    async def _wrapper():
        try:
            return await coro
        finally:
            from cfgpu_mcp.config import close
            await close()

    return asyncio.run(_wrapper())


@click.group()
def task() -> None:
    """Query or wait for async generation tasks."""


@task.command("status")
@click.argument("task_id")
@click.option("--json", "json_mode", is_flag=True, help="Output raw JSON")
def status_cmd(task_id: str, json_mode: bool) -> None:
    """Show the current status of TASK_ID."""
    from cfgpu_mcp.service import task as svc

    try:
        result = _run(svc.get_status(task_id))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)


@task.command("wait")
@click.argument("task_id")
@click.option("--timeout", type=int, default=None,
              help="Max wait seconds (default: model estimate)")
@click.option("--json", "json_mode", is_flag=True, help="Output raw JSON")
def wait_cmd(task_id: str, timeout: int | None, json_mode: bool) -> None:
    """Wait for TASK_ID to complete and print the result URLs."""
    from cfgpu_mcp.service import task as svc

    async def _go():
        return await svc.wait_for_task(task_id, timeout=timeout)

    try:
        result = _run(run_with_progress(_go(), f"Waiting for {task_id[:8]}"))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)
