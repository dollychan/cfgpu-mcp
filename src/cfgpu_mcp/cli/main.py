from __future__ import annotations

import click

from cfgpu_mcp.cli.cmd_generate import generate
from cfgpu_mcp.cli.cmd_models import models
from cfgpu_mcp.cli.cmd_task import task


@click.group()
@click.version_option(package_name="cfgpu-mcp")
def cli() -> None:
    """CFGPU — image and video generation from the command line.

    \b
    Quick start:
      cfgpu generate image "a red panda in the snow"
      cfgpu generate video "waves crashing on a beach" --model wan-2.0-fast
      cfgpu models list
    """


cli.add_command(generate)
cli.add_command(task)
cli.add_command(models)
