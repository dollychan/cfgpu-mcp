# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable + dev deps + CLI)
pip install -e ".[dev,cli]"

# Run all unit tests
pytest tests/unit/

# Run a single test file
pytest tests/unit/test_adapter_registry.py -v

# Run a single test
pytest tests/unit/test_adapter_registry.py::test_extends_chain_resolves_python_class -v

# Run integration tests (requires CFGPU_API_TOKEN in environment)
CFGPU_API_TOKEN=sk-... pytest tests/integration/ -v

# Run the MCP server (stdio transport)
cfgpu-mcp
# or
python -m cfgpu_mcp.server
```

## CLI usage

```bash
# List models
cfgpu models list
cfgpu models list --task-type video

# Generate image (waits, prints URL to stdout; progress/metadata to stderr)
cfgpu generate image "a red panda in the snow"
cfgpu generate image "..." --model doubao-seedream-5-0-lite --resolution 2K --json

# Generate video (shows elapsed seconds while polling)
cfgpu generate video "waves on a beach" --model wan-2.0-fast -d 4 -r 480p
cfgpu generate video "..." --first-frame https://... --no-audio

# Async workflow
cfgpu generate video "..." --no-wait          # prints task_id immediately
cfgpu task status <task_id>
cfgpu task wait <task_id>

# Pipe-friendly: stdout = URL(s), stderr = progress/metadata
cfgpu generate image "..." | xargs open
```

## Architecture

The server has three deployment modes sharing the same service layer:
- **Mode A**: MCP stdio server (`server.py` → `tools/` → `service/`)
- **Mode B**: Agent direct (Anthropic SDK calls `agent/dispatcher.py` → same `service/`)
- **Mode C** (future): CLI wrapping `service/` directly

### Three model identifiers — keep them distinct

Every model has three identifiers that must not be mixed up:

| Name | Example | Used where |
|---|---|---|
| `adapter_id` | `wan-2.0-fast` | Directory name, registry keys, user-facing everywhere |
| `display_name` | `WAN 2.0 Fast` | `list_models()` output only |
| `cfgpu_model_id` | `wan-video-fast` | **Only** inside `build_payload()` |

### Model configs (`src/cfgpu_mcp/models/`)

Each model lives in its own directory with `adapter.yaml` and `card.md`. Variant models (e.g. `wan-2.0-fast`) use `extends: wan-2.0` in their YAML to inherit all fields — only differences need to be listed. Deep merge applies field-level for dict fields like `poll_config`.

**Critical**: `AdapterRegistry._merge_extends()` preserves `extends` in the merged dict so `_instantiate()` can follow the chain to find the parent's Python Adapter class. Removing this breaks variant model dispatch.

### Python Adapter registration (Method B)

`adapters/base.py` holds a global `_PYTHON_ADAPTERS: dict[str, type]`. The `@register_python_adapter` decorator populates it at import time, keyed by `cls.adapter_id`. `adapters/__init__.py` imports `wan_video` and `seedream` to trigger registration before the registry loads.

`_instantiate()` in the registry looks up `adapter_id` first, then follows `extends` to find the parent's class — this is how `wan-2.0-fast` reuses `WanVideoAdapter` with its own `cfgpu_model_id`.

### Tool schema single source of truth

`tool_registry.py` defines all Pydantic input models (`GenerateImageInput`, `GenerateVideoInput`, etc.) and `NormalizedResult`. These serve two purposes:
- `get_anthropic_tools()` exports Anthropic SDK-compatible JSON schemas for Mode B
- Service functions accept these models as typed inputs

MCP tool wrappers in `tools/` re-declare parameters explicitly (FastMCP limitation) but forward directly to `service/`.

### Sync vs. async models

`is_async: false` in adapter YAML (e.g. Seedream) means the API returns the result in the POST response — no polling. `TaskManager.create()` branches on `adapter.is_async`: sync models parse the POST response immediately and write `succeeded` to DB; async models write `pending` and require polling via `TaskManager.wait()`.

### Adding a new model

1. Create `src/cfgpu_mcp/models/<adapter-id>/adapter.yaml` (use `extends:` if similar to an existing model)
2. Create `src/cfgpu_mcp/models/<adapter-id>/card.md`
3. If the model needs custom `build_payload` / `parse_response` logic, create `src/cfgpu_mcp/adapters/<name>.py` with `@register_python_adapter` and import it in `adapters/__init__.py`; otherwise `GenericAdapter` is used automatically

### Key files

- `tool_registry.py` — Pydantic schemas + `get_anthropic_tools()` + `NormalizedResult`
- `adapters/base.py` — `ModelAdapter` ABC + `@register_python_adapter` decorator
- `adapters/registry.py` — YAML loading, `extends` merge, Python class resolution
- `config.py` — Singleton registry/client/DB; `load_registry()` respects `CFGPU_ENABLED_MODELS` env var
- `router.py` — Scores adapters for `model="auto"` requests; Chinese prompts bias toward Seedream
- `task_manager.py` — Sync/async dispatch, exponential backoff polling, DB persistence
- `agent/dispatcher.py` — `dispatch_tool(name, inputs)` entry point for Mode B (Anthropic SDK)
- `agent/openai_tools.py` — `get_openai_tools()` + `openai_dispatch_tool()` for OpenAI SDK
- `agent/langgraph_tools.py` — `get_langgraph_tools()` returning `StructuredTool` list for LangGraph

### Environment variables

- `CFGPU_API_TOKEN` — Required. Bearer token for CFGPU API
- `CFGPU_ENABLED_MODELS` — Comma-separated `adapter_id` or `cfgpu_model_id` list to restrict loaded models; omit to load all
- `CFGPU_BASE_URL` — Override API base URL (default in client)
- `CFGPU_DB_PATH` — SQLite path for task persistence (default `~/.cfgpu/tasks.db`)
