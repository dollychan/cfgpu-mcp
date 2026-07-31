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

# Understand images/video (vision-language; prints text answer to stdout)
cfgpu understand "describe this image" --model qwen-3-6-plus -i https://...
cfgpu understand "list the timeline of events" --video https://...

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

### Four model identifiers — keep them distinct

Every model has four identifiers that must not be mixed up:

| Name | Example | Used where |
|---|---|---|
| `adapter_id` | `wan-2-0-fast` | Directory name, registry keys — internal only, never exposed |
| `display_name` | `WAN 2.0 Fast` | `list_models()` output only |
| `cfgpu_model_id` | `wan-video-fast` | **Only** inside `build_payload()` — internal only, never exposed |
| `model_name` | `wan-video-fast` | The **only** public identifier: the `model` tool param/enum, `list_models()`'s `model_id`, `NormalizedResult.model_used`, and `CFGPUError.model_id` all use it |

`AdapterRegistry.get()` resolves a key against `model_name` first, then falls back to `adapter_id` / `cfgpu_model_id` / `display_name`, so existing callers using the internal ids keep working — but nothing produced by the server (schemas, `list_models`, `model_used`, error `model_id`) ever emits `adapter_id` or `cfgpu_model_id`. `TaskManager` unconditionally stamps `result.model_used = adapter.model_name` after `parse_response()` — adapters often set `model_used` from the upstream response's echoed `"model"` field, which is actually `cfgpu_model_id`, so the override is not a fallback, it always runs.

### Model configs (`src/cfgpu_mcp/models/`)

Each model lives in its own directory with `adapter.yaml` and `card.md`. Variant models (e.g. `wan-2-0-fast`) use `extends: wan-2-0` in their YAML to inherit all fields — only differences need to be listed. Deep merge applies field-level for dict fields like `poll_config`.

**Critical**: `AdapterRegistry._merge_extends()` preserves `extends` in the merged dict so `_instantiate()` can follow the chain to find the parent's Python Adapter class. Removing this breaks variant model dispatch.

### Python Adapter registration (Method B)

`adapters/base.py` holds a global `_PYTHON_ADAPTERS: dict[str, type]`. The `@register_python_adapter` decorator populates it at import time, keyed by `cls.adapter_id`. `adapters/__init__.py` imports `seedance_video` and `seedream` to trigger registration before the registry loads.

`_instantiate()` in the registry looks up `adapter_id` first, then follows `extends` to find the parent's class — this is how `wan-2-0-fast` reuses `SeedanceVideoAdapter` with its own `cfgpu_model_id`. The Seedance family (`wan-2-0`, `wan-2-0-fast`, `doubao-seedance-2-0`, `doubao-seedance-2-0-fast`, `doubao-seedance-1-5-pro`) all share `SeedanceVideoAdapter` (registered under `wan-2-0`) — Seedance 2.0 is API-identical to WAN 2.0.

### Tool schema single source of truth

`tool_registry.py` defines all Pydantic input models (`GenerateImageInput`, `GenerateVideoInput`, etc.) and `NormalizedResult`. These serve two purposes:
- `get_anthropic_tools()` exports Anthropic SDK-compatible JSON schemas for Mode B
- Service functions accept these models as typed inputs

MCP tool wrappers in `tools/` re-declare parameters explicitly (FastMCP limitation) but forward directly to `service/`.

### Sync vs. async models

`is_async: false` in adapter YAML (e.g. Seedream) means the API returns the result in the POST response — no polling. `TaskManager.create()` branches on `adapter.is_async`: sync models parse the POST response immediately and write `succeeded` to DB; async models write `pending` and require polling via `TaskManager.wait()`.

**`request_id` correlation echo**: `generate_*` accepts an optional caller-supplied `request_id` — a call-time handle to join an async artifact/error (returned later by `task_status`/`task_wait`, on a *different* tool_call) back to the originating request. It's needed because `task_id` only exists after the POST returns and sync models have none. It rides the stored `payload` under the reserved `_request_id` key (`_stash_internal()`, stripped by `public_payload()` so it never reaches upstream), and is echoed by the **service layer** (`stamp_request_id()` / `CFGPUError.request_id`) — so all three modes (MCP, dispatcher, CLI) carry it, unlike the MCP-only `annotate_artifact`/`split_structured`. Always "add only when set" (`setdefault`); `understand_vision` (always sync, single-call) has no gap and omits it.

### Task types — media generation vs. understanding

`task_type` is one of `image` / `video` / `audio` / `understand`. The first three are **media generation**: `parse_response` returns `NormalizedResult.urls` and the pipeline's "succeeded but no urls = failure" guard applies (async path only). `understand` (vision-language: image/video understanding & reasoning, e.g. `qwen-3-6-plus` via `QwenVisionAdapter`) is **text-returning**: it speaks the OpenAI-compatible `/model/v1/chat/completions` API and fills `NormalizedResult.message` (the assistant `{role, content[, reasoning_content]}`) + `response_id`, with empty `urls`. It is always synchronous. The router isolates candidates by `task_type`, so an `understand` request never selects a media model. Its tool (`understand_vision`) returns a `CallToolResult` split into a lean LLM-facing `content` (`{id, model, message}`, where `message` is the hoisted answer text) and a client-facing `structuredContent` (`{reasoning_content, usage, payload}`); see "MCP content vs structuredContent split" below. It carries no `artifact` flag.

### MCP content vs structuredContent split

`tool_registry.split_structured()` (MCP tool layer, alongside `annotate_artifact()`) routes client-only fields out of the LLM-facing tool result. `langchain-mcp-adapters` maps an MCP `CallToolResult`'s text `content` → `ToolMessage.content` (enters the model context) and its `structuredContent` → `ToolMessage.artifact` (a side channel never shown to the model). Without the split, the whole result is one JSON text block that, when large (e.g. the echoed request `payload` plus a Thinking model's `reasoning_content`), gets truncated downstream and collapses into an opaque string. So:

- `generate_image` / `generate_video` / `generate_audio` and `task_status` / `task_wait` (which on success return the same flat `urls` + `payload` shape) move `usage` + `payload` to `structuredContent`; `content` keeps `urls` / `expires_at` / `task_id` / `model_used` / `seed` / `artifact` / `status`.
- `understand_vision` first reshapes the nested chat `message` (`reshape_vision_result()`: hoist `message.content` → top-level `message`, split `message.reasoning_content` → sibling `reasoning_content`), then moves `reasoning_content` + `usage` + `payload` to `structuredContent`.

`annotate_artifact()` also stamps a terminal `status` hint (`"Success. URLs already generated; …"`) next to `artifact: true`. This matters downstream: DeerFlow's MaterialsMiddleware rewrites `urls` out of `content`, so without an in-content done signal the model can't tell from what it sees that generation finished and keeps polling `task_status`/`task_wait`. The hint stays in `content` (LLM-facing); the split never touches it. In-flight / error results keep their raw `status` untouched.

`-> dict` tool annotations produce no `outputSchema`, so FastMCP (`convert_result`) and the lowlevel server pass a returned `CallToolResult` through verbatim with no output validation. Error dicts (`error` truthy) and non-dict results pass through `split_structured` unchanged, so the model still sees the full failure reason. The split is **MCP-tool-layer only** — Mode B (agent dispatcher) and Mode C (CLI) call the service layer directly and are unaffected.

### Adding a new model

1. Create `src/cfgpu_mcp/models/<adapter-id>/adapter.yaml` (use `extends:` if similar to an existing model)
2. Create `src/cfgpu_mcp/models/<adapter-id>/card.md`
3. If the model needs custom `build_payload` / `parse_response` logic, create `src/cfgpu_mcp/adapters/<name>.py` with `@register_python_adapter` and import it in `adapters/__init__.py`; otherwise `GenericAdapter` is used automatically

### Key files

- `tool_registry.py` — Pydantic schemas + `get_anthropic_tools()` + `NormalizedResult`
- `adapters/base.py` — `ModelAdapter` ABC + `@register_python_adapter` decorator
- `adapters/registry.py` — YAML loading, `extends` merge, Python class resolution
- `config.py` — Singleton registry/client/DB; `load_registry()` reads `enabled_models` from config.yaml
- `router.py` — Scores adapters for `model="auto"` requests; Chinese prompts bias toward Seedream
- `task_manager.py` — Sync/async dispatch, exponential backoff polling, DB persistence
- `agent/dispatcher.py` — `dispatch_tool(name, inputs)` entry point for Mode B (Anthropic SDK)
- `agent/openai_tools.py` — `get_openai_tools()` + `openai_dispatch_tool()` for OpenAI SDK
- `agent/langgraph_tools.py` — `get_langgraph_tools()` returning `StructuredTool` list for LangGraph

### Configuration

Non-secret config lives **only** in `config.yaml` (single source — see `config.example.yaml`): `transport`, `http.*`, `cfgpu_api.*` (base_url, http_timeout, connect_timeout), `task_db.*`, `enabled_models`. There are no per-field environment overrides. `task_db.url` may reference the environment via `$VAR` / `${VAR}` (e.g. `url: $DATABASE_URL`).

Environment variables (no config.yaml equivalent):

- `CFGPU_API_TOKEN` — Required. Bearer token for CFGPU API (the only secret; never in config.yaml). In stdio it's the token; in streamable-http it's the fallback when a request omits `Authorization`.
- `CFGPU_CONFIG` — Path to config.yaml (default `./config.yaml`)
- `CFGPU_DOTENV` — Path to a `.env` auto-loaded at startup (default `./.env`)
- `CFGPU_LOG_LEVEL` / `CFGPU_DRY_RUN` / `CFGPU_LOG_RESPONSES` — Logging/debug toggles
