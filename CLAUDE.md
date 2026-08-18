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

# Run integration tests — these submit REAL, BILLED generations, so they are
# opt-in by an explicit switch (a credential in .env is not consent to spend).
# Also needs a reachable DATABASE_URL: every generate path writes a task row.
CFGPU_RUN_INTEGRATION=1 pytest tests/integration/ -v

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

`_instantiate()` in the registry looks up `adapter_id` first, then follows `extends` to find the parent's class — this is how `wan-2-0-fast` reuses `SeedanceVideoAdapter` with its own `cfgpu_model_id`. The Seedance family (`wan-2-0`, `wan-2-0-fast`, `doubao-seedance-2-0`, `doubao-seedance-2-0-fast`, `doubao-seedance-2-0-mini`, `doubao-seedance-2-5`, `doubao-seedance-1-5-pro`) all share `SeedanceVideoAdapter` (registered under `wan-2-0`) — Seedance 2.0 is API-identical to WAN 2.0, and 2.5 differs in scale only (30s single-shot, up to 50 reference materials, multilingual audio), not in payload shape.

Per-model video duration ceilings and resolution value sets are declarative, both enforced by `ModelAdapter.supports()` so an unsupported request fails locally and `model="auto"` routes around it:

- `max_duration_seconds` — `GenerateVideoInput`'s validator allows the fleet-wide widest range (4–30, set by Seedance 2.5); each `adapter.yaml` declares its real limit (default 15, the pre-2.5 global cap, so no existing model changed).
- `resolutions` — the allowed `resolution` values (Seedance 2.5 / 2.0 fast / 2.0 mini are `[480p, 720p]`; Seedance 2.0 is `[480p, 720p, 1080p]`). `None` (the default, and what every other model still has) means no local restriction. Without it the upstream failure is `the parameter resolution specified in the request is not valid for model X in i2v`. Note this is separate from the older, scenario-dependent `wan-2-0-fast` carve-out in `SeedanceVideoAdapter.supports()`, which bars 1080p only for text-to-video.

### Tool schema single source of truth

`tool_registry.py` defines all Pydantic input models (`GenerateImageInput`, `GenerateVideoInput`, etc.) and `NormalizedResult`. These serve two purposes:
- `get_anthropic_tools()` exports Anthropic SDK-compatible JSON schemas for Mode B
- Service functions accept these models as typed inputs

MCP tool wrappers in `tools/` re-declare parameters explicitly (FastMCP limitation) but forward directly to `service/`.

Because FastMCP builds the Mode A schema from those bare signatures, anything carried by the Pydantic `Field` has to be injected back in at import time in `server.py`. There are three such injectors, all sourcing from `tool_registry`, all applied to `tool.parameters` in place: `_inject_param_descriptions` (per-param docs, via `get_field_descriptions`), `_inject_model_enums` (registry-driven `model` enum, via the shared `apply_model_enum`), and `_inject_media_annotations` (material slots, via `apply_media_annotations`). Mode B / OpenAI / LangGraph build from `model_json_schema()` and get descriptions and `json_schema_extra` for free — they call `apply_model_enum` only, because the enum is registry-driven and cannot be declared statically on the model.

### Material slots (`x-cfgpu-media`)

Eight parameters across four tools carry a reference to a media file rather than a scalar: `generate_image.reference_images`; `generate_video.{first_frame, last_frame, reference_images, reference_videos, reference_audios}`; `understand_vision.{images, video}`. Each is declared with `media_field(...)` instead of `Field(...)`, which attaches a machine-readable `x-cfgpu-media` annotation — `role` (image/video/audio), `arity` (one/many), `accepts` (closed vocabulary: `https_url`, `asset_url`), and `slot` (the semantic half of the description) — and **generates** the description as `slot` + a wire clause derived from `accepts`, so prose and annotation cannot drift.

**The annotation carries permissive facts only** — what *may* appear in a slot, never what is forbidden. Those four keys hold for the whole tool, and erring permissive costs nothing worse than a call some model rejects, which is the status quo. Caps and mutual exclusions are the opposite shape: they are per-model while this schema is per-tool, so a `max_items` / `excludes` / `requires` key would be a union presented as a universal, and any host that enforced it would reject calls that are legal on the model actually in use. Such limits stay in the prose as model-relative guidance pointing at `get_model_card` / `list_models`, with cfgpu's own validation as the enforcement point. `test_annotation_carries_permissive_facts_only` pins the key set, so adding one means answering this first.

This exists for hosts that maintain their own reference layer. DeerFlow/cf-dream, for instance, hands the model a short `material id` (`m3`) and resolves it to a freshly-signed URL at call time; it needs to know **which** parameters to re-describe in that dialect (otherwise the schema says "URL" while its ledger says "id", and the model mixes the two formats) and **which** values to resolve on the way out (otherwise it inspects every string argument by shape, which misfires on ordinary values like `16/9` or `black-forest-labs/FLUX.1-dev`). Read the slot list with `get_media_slots(tool_name)`; rebuild a description as `spec["slot"] + <your own wire clause>`.

The annotation states the **wire contract only** — what this server can actually fetch — and deliberately says nothing about host dialects. `accepts: asset_url` is claimed on all five `generate_video` slots and nowhere else: the Seedance / WAN request schema takes a 素材 ID on `image_url.url`, `video_url.url` and `audio_url.url` alike (`models/wan-2-0/card.md` — only the image row spells the `asset://` scheme out, the other two say 素材 ID, so grepping for the scheme under-reports it). Widen it only against a model card. Base64 is not advertised anywhere, even where an upstream model tolerates it. Adding a wire form means adding it to `_WIRE_FORMS` first — `media_field` rejects unknown values. Tests: `tests/unit/test_media_annotations.py` (inventory, arity-vs-type agreement, permissive-only key set, description composition, presence on both the Mode A and Mode B schemas).

### Sync vs. async models

`is_async: false` in adapter YAML (e.g. Seedream) means the API returns the result in the POST response — no polling. `TaskManager.create()` branches on `adapter.is_async`: sync models parse the POST response immediately and write `succeeded` to DB; async models write `pending` and require polling via `TaskManager.wait()`.

**Caller-supplied echo fields** (`request_id`, `caption`): `generate_*` accepts two optional handles that this server stores and hands back but never interprets. Both ride the stored `payload` under reserved keys (`_request_id` / `_caption` via `_stash_internal()`, stripped by `public_payload()` so neither reaches upstream) and are echoed by the **service layer** (`stamp_echo()`) — so all three modes (MCP, dispatcher, CLI) carry them, unlike the MCP-only `annotate_artifact`/`split_structured`. Always "add only when set" (`setdefault`); `understand_vision` (always sync, single-call, text result) has neither.

- **`request_id`** is a *correlation* handle: it joins an async artifact/error (returned later by `task_status`/`task_wait`, on a *different* tool_call) back to the originating request. Needed because `task_id` only exists after the POST returns and sync models have none.
- **`caption`** is a *label* for the artifact — for callers that keep their own asset ledger (DeerFlow/cf-dream registers each generated artifact as a **material** the user later refers to by a short id). Without a label supplied at call time, the ledger entry is nameless and the caller has to spend a second tool round trip naming it after the fact; riding the stored payload is what carries it across the two-phase `generate(wait=False) → task_wait` hop with no state on the caller's side. Truncated at `CAPTION_MAX_CHARS` (200) rather than rejected — a caption cannot affect the generated media, so failing the call over the length of a cosmetic label would cost the caller a turn for nothing.

The two diverge on **failures**: `CFGPUError` carries `request_id` (`CFGPUError.request_id`) but not `caption` — a failed call produced no artifact to label. `test_failed_task_carries_request_id_but_not_caption` pins that asymmetry.

### Task types — media generation vs. understanding

`task_type` is one of `image` / `video` / `audio` / `understand`. The first three are **media generation**: `parse_response` returns `NormalizedResult.urls` and/or `inline_media`, and both the synchronous create path and asynchronous poll path enforce "succeeded but no artifact = failure". `understand` (vision-language: image/video understanding & reasoning, e.g. `qwen-3-6-plus` via `QwenVisionAdapter`) is **text-returning**: it speaks the OpenAI-compatible `/model/v1/chat/completions` API and fills `NormalizedResult.message` (the assistant `{role, content[, reasoning_content]}`) + `response_id`, with empty `urls`. It is always synchronous and is deliberately exempt from the media-artifact guard. The router isolates candidates by `task_type`, so an `understand` request never selects a media model. Its tool (`understand_vision`) returns a `CallToolResult` split into a lean LLM-facing `content` (`{id, model, message}`, where `message` is the hoisted answer text) and a client-facing `structuredContent` (`{reasoning_content, usage, payload}`); see "MCP content vs structuredContent split" below. It carries no `artifact` flag.

### MCP content vs structuredContent split

`tool_registry.split_structured()` (MCP tool layer, alongside `annotate_artifact()`) routes client-only fields out of the LLM-facing tool result. `langchain-mcp-adapters` maps an MCP `CallToolResult`'s text `content` → `ToolMessage.content` (enters the model context) and its `structuredContent` → `ToolMessage.artifact` (a side channel never shown to the model). Without the split, the whole result is one JSON text block that, when large (e.g. the echoed request `payload` plus a Thinking model's `reasoning_content`), gets truncated downstream and collapses into an opaque string. So:

- `generate_image` / `generate_video` / `generate_audio` and `task_status` / `task_wait` (which on success return the same flat `urls` + `payload` shape) move `usage` + `payload` to `structuredContent`; `content` keeps `urls` / `expires_at` / `task_id` / `model_used` / `seed` / `artifact` / `status`.
- `understand_vision` first reshapes the nested chat `message` (`reshape_vision_result()`: hoist `message.content` → top-level `message`, split `message.reasoning_content` → sibling `reasoning_content`), then moves `reasoning_content` + `usage` + `payload` to `structuredContent`.

`annotate_artifact()` also stamps a terminal `status` hint (`"Success. URLs already generated; …"`) next to `artifact: true`. This matters downstream: DeerFlow's MaterialsMiddleware rewrites `urls` out of `content`, so without an in-content done signal the model can't tell from what it sees that generation finished and keeps polling `task_status`/`task_wait`. The hint stays in `content` (LLM-facing); the split never touches it. In-flight / error results keep their raw `status` untouched.

`-> dict` tool annotations produce no `outputSchema`, so FastMCP (`convert_result`) and the lowlevel server pass a returned `CallToolResult` through verbatim with no output validation. Error dicts (`error` truthy) and non-dict results pass through `split_structured` unchanged, so the model still sees the full failure reason. The split is **MCP-tool-layer only** — Mode B (agent dispatcher) and Mode C (CLI) call the service layer directly and are unaffected.

### Upstream error translation (`card_hint`)

`CFGPUError.to_tool_result_dict()` appends "请调用 get_model_card …" for the types in `_CARD_HINT_TYPES`. That sentence assumes the card can answer the question — pass `card_hint=False` when it can't, and state the concrete remedy in `user_message` instead. The flag only ever **suppresses**; a type with no hint never gains one.

The MiniMax speech adapter is the worked example (`adapters/audio_tts.py`). MiniMax returns parameter rejections inside an HTTP-200 body whose `status_msg` names the offending field and stops there (`invalid params: voice_setting emotion`) — that says *what* broke and nothing about what to do, so the caller retries with another guess. `_minimax_remedy()` maps `(status_code, status_msg)` to an actionable sentence appended after the verbatim upstream wording, and the two production codes need **opposite** advice:

- **2054** (`voice id not exist`) — an authoritative list exists (the card's `系统音色列表`), so the remedy points at it and names the two mistakes behind most of these: reusing a seed-tts speaker (`*_uranus_bigtts` / `saturn_*`) on MiniMax, and "normalising" an id that legitimately contains a trailing space / full-width bracket / irregular casing. Card hint **kept**.
- **2013** (`voice_setting emotion`) — no authoritative list exists anywhere: the card documents the field but never enumerates its values. Pointing at the card sends the caller after something that isn't written down and implies a correct value is discoverable, which invites the very retry loop this is meant to stop. Card hint **suppressed**; the remedy is to omit the field (auto-inference is the design) or carry the emotion via the card-documented inline text markers, which cannot fail this way.

Both are reclassified from `task_failed` to `invalid_params` (`_MINIMAX_CALLER_FIXABLE_CODES`) — `task_failed` reads as "generation failed" and invites a retry that can never succeed. Codes outside the table keep their previous classification; guessing at an unknown code is worse than the status quo. The upstream wording is always quoted first and `original` is never rewritten, so a report stays joinable with MiniMax's own logs.

### Adding a new model

1. Create `src/cfgpu_mcp/models/<adapter-id>/adapter.yaml` (use `extends:` if similar to an existing model)
2. Create `src/cfgpu_mcp/models/<adapter-id>/card.md`
3. If the model needs custom `build_payload` / `parse_response` logic, create `src/cfgpu_mcp/adapters/<name>.py` with `@register_python_adapter` and import it in `adapters/__init__.py`; otherwise `GenericAdapter` is used automatically

### Key files

- `tool_registry.py` — Pydantic schemas + `get_anthropic_tools()` + `NormalizedResult`
- `adapters/base.py` — `ModelAdapter` ABC + `@register_python_adapter` decorator
- `adapters/registry.py` — YAML loading, `extends` merge, Python class resolution
- `config.py` — Singleton registry/client/DB; `load_registry()` reads `disabled_models` from config.yaml
- `router.py` — Scores adapters for `model="auto"` requests; Chinese prompts bias toward Seedream
- `task_manager.py` — Sync/async dispatch, exponential backoff polling, DB persistence
- `agent/dispatcher.py` — `dispatch_tool(name, inputs)` entry point for Mode B (Anthropic SDK)
- `agent/openai_tools.py` — `get_openai_tools()` + `openai_dispatch_tool()` for OpenAI SDK
- `agent/langgraph_tools.py` — `get_langgraph_tools()` returning `StructuredTool` list for LangGraph

### Configuration

Non-secret config lives **only** in `config.yaml` (single source — see `config.example.yaml`): `transport`, `http.*`, `cfgpu_api.*` (base_url, http_timeout, connect_timeout), `task_db.*`, `disabled_models`, `disabled_tools`. There are no per-field environment overrides. `task_db.url` may reference the environment via `$VAR` / `${VAR}` (e.g. `url: $DATABASE_URL`).

### Trimming what's exposed — `disabled_models` / `disabled_tools`

Both are config.yaml **blocklists**; omitted / null / `[]` all mean "nothing disabled". Blocklists rather than allowlists because the fleet only grows: a config naming what to *drop* stays correct when a model is added, while a whitelist silently hides every new model until someone extends it. The old `enabled_models` whitelist is gone — a leftover **non-empty** one raises at `load_settings()` (`_reject_enabled_models`), since the two mean opposite things and ignoring it would load exactly the models it was written to exclude; a leftover empty one only warns, so no deployment breaks on upgrade.

- **`disabled_models`** filters in `AdapterRegistry._is_enabled()` at load time, so a blocked model leaves `list_models`, the tools' `model` enum, and `model="auto"` routing at once — the same mechanism as `_has_provider()`. Names may be `model_name` / `adapter_id` / `cfgpu_model_id` (whatever `registry.get()` resolves). `get_registry()` / `load_registry()` still take an `enabled_models` allowlist for embedders — config.yaml has only the blocklist; both may be given and the blocklist wins. Disabling a model that others `extends:` is safe: `_merge_extends()` reads the raw YAML configs, not the registered adapters (`test_disabled_parent_still_supplies_extends_fields`).
- **`disabled_tools`** runs in `server._apply_disabled_tools()` at import time, after `register()` and *before* the three schema injectors. It calls `ToolManager.remove_tool()` — unregistered, not hidden from `tools/list`, so calling a disabled name returns "unknown tool". An unknown name raises at startup: the field exists to make a tool *not* exposed, and a typo passing silently is exactly the failure it was written to prevent. Mode A only; Mode B / C reach the service layer directly and filter with `get_anthropic_tools(tools=[...])`.

Environment variables (no config.yaml equivalent):

- `CFGPU_API_TOKEN` — Required. Bearer token for CFGPU API (the only secret; never in config.yaml). In stdio it's the token; in streamable-http it's the fallback when a request omits `Authorization`.
- `CFGPU_CONFIG` — Path to config.yaml (default `./config.yaml`)
- `CFGPU_DOTENV` — Path to a `.env` auto-loaded at startup (default `./.env`)
- `CFGPU_LOG_LEVEL` / `CFGPU_DRY_RUN` / `CFGPU_LOG_RESPONSES` — Logging/debug toggles
