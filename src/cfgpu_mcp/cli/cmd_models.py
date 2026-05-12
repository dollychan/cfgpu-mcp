from __future__ import annotations

import asyncio
import sys

import click

from cfgpu_mcp.cli.output import print_error, print_json, print_models


def _run(coro):
    async def _wrapper():
        try:
            return await coro
        finally:
            from cfgpu_mcp.config import close
            await close()

    return asyncio.run(_wrapper())


@click.group()
def models() -> None:
    """Browse available CFGPU models."""


@models.command("list")
@click.option("--task-type", type=click.Choice(["image", "video"]),
              default=None, help="Filter by task type")
@click.option("--json", "json_mode", is_flag=True, help="Output raw JSON")
def list_cmd(task_type: str | None, json_mode: bool) -> None:
    """List available models with capabilities."""
    from cfgpu_mcp.service import model as svc

    try:
        result = _run(svc.list_models(task_type))
        print_models(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)


@models.command("card")
@click.argument("model_name")
def card_cmd(model_name: str) -> None:
    """Print the model card for MODEL_NAME (adapter_id or cfgpu_model_id)."""
    from cfgpu_mcp.service import model as svc

    try:
        card = _run(svc.get_model_card(model_name))
        print(card)
    except Exception as e:
        print_error(e)
        sys.exit(1)
