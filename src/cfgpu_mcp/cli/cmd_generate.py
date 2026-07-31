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


@click.group()
def generate() -> None:
    """Generate images, videos, or audio."""


@generate.command("image")
@click.argument("prompt")
@click.option("--model", "-m", default="auto", show_default=True,
              help="model_name (see `cfgpu models list`), or 'auto'")
@click.option("--aspect-ratio", "-a",
              type=click.Choice(["1:1", "16:9", "9:16", "4:3", "3:4"]),
              default="1:1", show_default=True)
@click.option("--resolution", "-r",
              type=click.Choice(["2K", "3K", "4K"]),
              default="2K", show_default=True)
@click.option("--reference-images", multiple=True, metavar="URL",
              help="Reference image URL (repeat for multiple)")
@click.option("-n", "n", type=int, default=1, show_default=True,
              help="Number of group images to generate (1-15; doubao-seedream-* only)")
@click.option("--quality-tier", "-q",
              type=click.Choice(["fast", "balanced", "best"]),
              default="balanced", show_default=True)
@click.option("--watermark/--no-watermark", default=None,
              help="Add/remove watermark (default: model's own default)")
@click.option("--no-wait", is_flag=True,
              help="Return task_id immediately without waiting for completion")
@click.option("--timeout", type=int, default=None,
              help="Max wait seconds (default: model estimate)")
@click.option("--metadata", is_flag=True,
              help="Include seed, model_used, usage in output")
@click.option("--json", "json_mode", is_flag=True,
              help="Output raw JSON")
@click.option("--model-specific", default=None, metavar="JSON",
              help='Extra API params as JSON object, e.g. \'{"tools":[{"type":"web_search"}]}\'')
def image_cmd(
    prompt, model, aspect_ratio, resolution, reference_images, n,
    quality_tier, watermark, no_wait, timeout, metadata, json_mode, model_specific,
) -> None:
    """Generate an image from PROMPT."""
    from cfgpu_mcp.service import image as svc
    extra = _parse_model_specific(model_specific)

    async def _go():
        return await svc.generate_image(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            reference_images=list(reference_images) or None,
            n=n,
            quality_tier=quality_tier,
            watermark=watermark,
            wait=not no_wait,
            timeout=timeout,
            return_metadata=metadata,
            model_specific=extra,
        )

    try:
        if no_wait:
            result = _run(_go())
        else:
            result = _run(run_with_progress(_go(), "Generating image"))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)


@generate.command("video")
@click.argument("prompt")
@click.option("--model", "-m", default="auto", show_default=True,
              help="model_name (see `cfgpu models list`), or 'auto'")
@click.option("--first-frame", default=None, metavar="URL",
              help="First frame image URL")
@click.option("--last-frame", default=None, metavar="URL",
              help="Last frame image URL (use with --first-frame)")
@click.option("--reference-images", multiple=True, metavar="URL",
              help="Reference image URL (mutually exclusive with --first-frame)")
@click.option("--reference-videos", multiple=True, metavar="URL",
              help="Reference video URL (repeat for multiple, max 3)")
@click.option("--reference-audios", multiple=True, metavar="URL",
              help="Reference audio URL (repeat for multiple, max 3)")
@click.option("--duration", "-d", "duration_seconds", type=int, default=5,
              show_default=True, help="Duration in seconds (4-15; -1 = smart/auto, WAN 2.0 & Seedance only)")
@click.option("--aspect-ratio", "-a",
              type=click.Choice(["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"]),
              default="adaptive", show_default=True)
@click.option("--resolution", "-r",
              type=click.Choice(["480p", "720p", "1080p"]),
              default="720p", show_default=True)
@click.option("--no-audio", is_flag=True,
              help="Disable audio generation")
@click.option("--quality-tier", "-q",
              type=click.Choice(["fast", "balanced", "best"]),
              default="balanced", show_default=True)
@click.option("--watermark/--no-watermark", default=None,
              help="Add/remove watermark (default: model's own default)")
@click.option("--no-wait", is_flag=True,
              help="Return task_id immediately without waiting for completion")
@click.option("--timeout", type=int, default=None,
              help="Max wait seconds (default: model estimate)")
@click.option("--metadata", is_flag=True,
              help="Include seed, model_used, usage in output")
@click.option("--json", "json_mode", is_flag=True,
              help="Output raw JSON")
@click.option("--model-specific", default=None, metavar="JSON",
              help='Extra API params as JSON object')
def video_cmd(
    prompt, model, first_frame, last_frame, reference_images, reference_videos,
    reference_audios, duration_seconds, aspect_ratio, resolution, no_audio,
    quality_tier, watermark, no_wait, timeout, metadata, json_mode, model_specific,
) -> None:
    """Generate a video from PROMPT."""
    from cfgpu_mcp.service import video as svc
    extra = _parse_model_specific(model_specific)

    async def _go():
        return await svc.generate_video(
            prompt=prompt,
            model=model,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_images=list(reference_images) or None,
            reference_videos=list(reference_videos) or None,
            reference_audios=list(reference_audios) or None,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            with_audio=not no_audio,
            quality_tier=quality_tier,
            watermark=watermark,
            wait=not no_wait,
            timeout=timeout,
            return_metadata=metadata,
            model_specific=extra,
        )

    try:
        if no_wait:
            result = _run(_go())
        else:
            result = _run(run_with_progress(_go(), "Generating video"))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)


@generate.command("audio")
@click.argument("text")
@click.option("--model", "-m", default="auto", show_default=True,
              help="model_name (see `cfgpu models list`), or 'auto'")
@click.option("--voice", default=None, metavar="VOICE_ID",
              help="Voice/speaker id (default: model's own default)")
@click.option("--format", "audio_format",
              type=click.Choice(["mp3", "wav", "pcm", "flac"]),
              default="mp3", show_default=True)
@click.option("--sample-rate", type=int, default=None,
              help="Output sample rate in Hz (default: model's default)")
@click.option("--bitrate", type=int, default=None,
              help="Output bitrate in bps (MiniMax only)")
@click.option("--speed", type=float, default=1.0, show_default=True,
              help="Speech speed multiplier (MiniMax only)")
@click.option("--volume", type=float, default=1.0, show_default=True,
              help="Speech volume multiplier (MiniMax only)")
@click.option("--pitch", type=int, default=0, show_default=True,
              help="Speech pitch offset (MiniMax only)")
@click.option("--emotion", default=None, metavar="EMOTION",
              help="Emotion hint, e.g. happy/sad/angry (MiniMax only)")
@click.option("--quality-tier", "-q",
              type=click.Choice(["fast", "balanced", "best"]),
              default="balanced", show_default=True)
@click.option("--no-wait", is_flag=True,
              help="Return task_id immediately without waiting for completion")
@click.option("--timeout", type=int, default=None,
              help="Max wait seconds (default: model estimate)")
@click.option("--metadata", is_flag=True,
              help="Include model_used, usage in output")
@click.option("--json", "json_mode", is_flag=True,
              help="Output raw JSON")
@click.option("--model-specific", default=None, metavar="JSON",
              help='Extra API params as JSON object')
def audio_cmd(
    text, model, voice, audio_format, sample_rate, bitrate, speed, volume, pitch,
    emotion, quality_tier, no_wait, timeout, metadata, json_mode, model_specific,
) -> None:
    """Generate speech audio from TEXT (text-to-speech)."""
    from cfgpu_mcp.service import audio as svc
    extra = _parse_model_specific(model_specific)

    async def _go():
        return await svc.generate_audio(
            text=text,
            model=model,
            voice=voice,
            audio_format=audio_format,
            sample_rate=sample_rate,
            bitrate=bitrate,
            speed=speed,
            volume=volume,
            pitch=pitch,
            emotion=emotion,
            quality_tier=quality_tier,
            wait=not no_wait,
            timeout=timeout,
            return_metadata=metadata,
            model_specific=extra,
        )

    try:
        if no_wait:
            result = _run(_go())
        else:
            result = _run(run_with_progress(_go(), "Generating audio"))
        print_result(result, json_mode)
    except Exception as e:
        print_error(e, json_mode)
        sys.exit(1)
