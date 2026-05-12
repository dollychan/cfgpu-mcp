from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def print_result(data: dict, json_mode: bool = False) -> None:
    if json_mode:
        print_json(data)
        return

    if "urls" in data:
        urls = data["urls"]
        for url in urls:
            print(url)
        if not urls:
            print("(no URLs returned)", file=sys.stderr)
        if data.get("expires_at"):
            print(f"expires: {data['expires_at']}", file=sys.stderr)
        for key in ("task_id", "model_used", "seed", "cost_tokens"):
            if data.get(key) is not None:
                print(f"{key}: {data[key]}", file=sys.stderr)
        return

    # task-only response (no-wait or task status)
    if "task_id" in data:
        print(f"task_id: {data['task_id']}")
        print(f"status:  {data['status']}")
        if data.get("result"):
            result = data["result"]
            for url in result.get("urls", []):
                print(url)
        if data.get("error"):
            print(f"error:   {data['error']}", file=sys.stderr)
        return

    # generic fallback
    print_json(data)


def print_models(models: list[dict], json_mode: bool = False) -> None:
    if json_mode:
        print_json(models)
        return

    col_w = 28
    header = f"{'ADAPTER ID':<{col_w}}  {'TYPE':<6}  {'COST':<5}  {'SPEED':<6}  DISPLAY NAME"
    print(header)
    print("-" * len(header))
    for m in models:
        print(
            f"{m['adapter_id']:<{col_w}}"
            f"  {m['task_type']:<6}"
            f"  {m['cost_tier']:<5}"
            f"  {m['speed_tier']:<6}"
            f"  {m['display_name']}"
        )


def print_error(err: Exception, json_mode: bool = False) -> None:
    from cfgpu_mcp.errors import CFGPUError
    if isinstance(err, CFGPUError):
        if json_mode:
            print(
                json.dumps({"error": err.error_type, "message": err.user_message}, ensure_ascii=False),
                file=sys.stderr,
            )
        else:
            print(f"Error [{err.error_type}]: {err.user_message}", file=sys.stderr)
    else:
        print(f"Error: {err}", file=sys.stderr)


async def run_with_progress(coro, label: str) -> Any:
    """Run an async coroutine while printing elapsed seconds to stderr."""
    fut = asyncio.ensure_future(coro)
    t0 = time.monotonic()
    is_tty = sys.stderr.isatty()
    try:
        while not fut.done():
            elapsed = int(time.monotonic() - t0)
            if is_tty:
                print(f"\r{label}... {elapsed}s", end="", file=sys.stderr, flush=True)
            await asyncio.sleep(1)
    finally:
        if is_tty:
            print(file=sys.stderr)  # newline after progress
    return await fut
