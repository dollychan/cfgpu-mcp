from __future__ import annotations

import asyncio
import json
import sys

import click

from cfgpu_mcp.cli.output import print_error, print_result, run_with_progress


def _parse_model_specific(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise click.BadParameter("must be a JSON object")
        return parsed
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"invalid JSON: {e}")


def _run(coro) -> dict:
    async def _wrapper():
        try:
            return await coro
        finally:
            from cfgpu_mcp.config import close
            await close()

    return asyncio.run(_wrapper())


@click.command("understand")
@click.argument("prompt")
@click.option("--model", "-m", default="auto", show_default=True,
              help="adapter_id, cfgpu_model_id, or 'auto'")
@click.option("--image", "-i", "images", multiple=True, metavar="URL",
              help="Image URL to analyze (repeat for multiple)")
@click.option("--video", default=None, metavar="URL",
              help="Public video URL to understand")
@click.option("--system", "system_prompt", default=None, metavar="TEXT",
              help="System prompt (default: 'You are a helpful assistant.')")
@click.option("--max-tokens", type=int, default=None,
              help="Maximum output tokens (default: model's default)")
@click.option("--temperature", type=float, default=None,
              help="Sampling temperature (default: model's default)")
@click.option("--metadata", is_flag=True,
              help="Include token usage in output")
@click.option("--json", "json_mode", is_flag=True,
              help="Output raw JSON")
@click.option("--model-specific", default=None, metavar="JSON",
              help='Extra API params as JSON object, e.g. \'{"top_p":0.8}\'')
def understand(
    prompt, model, images, video, system_prompt, max_tokens, temperature,
    metadata, json_mode, model_specific,
) -> None:
    """Understand/reason over images or video from PROMPT (prints text to stdout)."""
    from cfgpu_mcp.service import vision as svc
    extra = _parse_model_specific(model_specific)

    async def _go():
        return await svc.understand_vision(
            prompt=prompt,
            model=model,
            images=list(images) or None,
            video=video,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            return_metadata=metadata,
            model_specific=extra,
        )

    try:
        result = _run(run_with_progress(_go(), "Understanding"))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)
