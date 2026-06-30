---
name: cfgpu-mcp
description: Use the cfgpu MCP server — the full tool surface for generating images, videos, and speech, understanding images/video, listing models, and polling async tasks. Covers every tool's parameters, the result shapes, and the complete error catalogue with fixes. Use when calling any cfgpu/cfdream MCP tool, when a cfgpu tool call returns an error, or when deciding which generation tool/model to use.
---

# Using the cfgpu MCP server

The **cfgpu MCP server** exposes one unified tool surface for media generation, vision understanding, and task management. This skill is the entry point: it describes every tool, the result/error shapes shared by all of them, and how to recover from failures. For deep per-model parameter detail there are dedicated skills (`seedance-video`, `wanxiang-video`, `happyhorse-video`, `minimax-speech`, `seed-tts`) and `get_model_card`.

> **Tool naming.** Hosts may namespace tools (e.g. `mcp__cfgpu__generate_image`). Use whatever prefix your environment exposes — parameters are identical.

## The eight tools

| Tool | Task type | Sync/async | Returns |
|---|---|---|---|
| `generate_image` | image | depends on model | `urls` (image files) |
| `generate_video` | video | always async | `urls` (mp4) |
| `generate_audio` | audio | depends on model | `urls` (audio file) |
| `understand_vision` | understand | always **sync** | `message` (text answer) |
| `task_status` | — | — | task status snapshot |
| `task_wait` | — | — | blocks, then final result |
| `list_models` | — | — | model catalogue |
| `get_model_card` | — | — | one model's full doc |

### Pick the tool by intent

- **Make an image** → `generate_image` (text-to-image, optional `reference_images`, `n` for 组图 on `doubao-seedream-*`).
- **Make a video** → `generate_video` (text/image/reference-to-video, edit, extend). See `seedance-video` / `wanxiang-video` / `happyhorse-video`.
- **Make speech (TTS)** → `generate_audio`. See `minimax-speech` (sync, fine control) / `seed-tts` (async, Chinese character voices).
- **Read/answer about an image or video** → `understand_vision` (returns text, never a file).
- **Don't know which model exists** → `list_models(task_type=...)` then `get_model_card(model_name)`.
- **Fired a job with `wait=false`** → `task_status(task_id)` to peek, `task_wait(task_id)` to block for the result.

## Model selection (the `model` parameter)

Every generation tool takes `model`, accepting:
- a single id — `"doubao-seedream-5-0-lite"`, `"wan-2-0"`, `"minimax-speech-2-8-hd"`
- a **list** to restrict auto-routing to those candidates — `["wan-2-0", "wan-2-0-fast"]`
- `"auto"` (default) — the router scores all models of that task type (Chinese prompts bias toward Seedream/Seedance).

Three identifiers exist per model — always use the **`adapter_id`** (e.g. `wan-2-0-fast`) in tool calls, never the `cfgpu_model_id` or `display_name`. `list_models` shows all three.

## Shared generation parameters

All `generate_*` tools share these:

| Parameter | Default | Notes |
|---|---|---|
| `model` | `"auto"` | see above |
| `quality_tier` | `"balanced"` | `fast` / `balanced` / `best` |
| `wait` | `true` | `false` returns a `task_id` immediately (async models) |
| `timeout` | auto | max wait seconds when `wait=true` |
| `return_metadata` | `true` | adds `seed`, `model_used`, `usage` to the result |
| `model_specific` | — | raw API params merged **last** (overrides typed fields like `watermark`) |

Tool-specific parameters are documented in each model's skill / card. Key ones:
- `generate_image`: `aspect_ratio` (`1:1`…`21:9`), `resolution` (`1K`–`4K`), `reference_images`, `n` (1–15, 组图, `doubao-seedream-*` only), `watermark`.
- `generate_video`: `first_frame`/`last_frame` **or** `reference_images`/`reference_videos`/`reference_audios` (mutually exclusive scenarios), `duration_seconds` (4–15, `-1`=smart), `aspect_ratio`, `resolution` (`480p`/`720p`/`1080p`), `with_audio`, `watermark`.
- `generate_audio`: `voice`, `audio_format` (`mp3`/`wav`/`pcm`/`flac`), `sample_rate`, plus MiniMax-only `bitrate`/`speed`/`volume`/`pitch`/`emotion`.
- `understand_vision`: `images` (list), `video` (single URL), `system_prompt`, `max_tokens`, `temperature`. Synchronous — no `wait`/`task_id`.

## Reading results

### Media generation (`generate_image` / `generate_video` / `generate_audio`, and `task_wait` for them)

```json
{
  "urls": ["https://.../output.mp4"],
  "expires_at": "2026-06-25T12:00:00Z",
  "artifact": true,
  "status": "Success. URLs already generated; no further task_status/task_wait polling needed.",
  "model_used": "...", "seed": 15233            // when return_metadata=true
}
```

Give the user the `urls`. **Warn that links expire (~24 h) — download promptly.** When you see `artifact: true` + the success `status`, generation is **done** — do not keep polling `task_status`/`task_wait`. (`usage`/`payload` are routed to `structuredContent`, a client side-channel, so you may not see them in content — that's expected.)

### Vision understanding (`understand_vision`)

```json
{ "id": "chatcmpl-...", "model": "qwen3-vl-30b-a3b-thinking", "message": "the answer text" }
```

The answer is the top-level `message` string. For Thinking models the chain-of-thought (`reasoning_content`) and `usage` go to `structuredContent`. There are no `urls`.

### Async workflow

```json
generate_video({ "prompt": "...", "wait": false })   // → { "task_id": "cgt-..." }
task_status({ "task_id": "cgt-..." })                 // → status snapshot (see below)
task_wait({ "task_id": "cgt-...", "timeout": 600 })   // blocks, then final result
```

`task_status` / `task_wait` return `{ "task_id", "status", "result", "error" }`. Normalized `status` is one of:

| status | meaning | what to do |
|---|---|---|
| `pending` | queued, not started | keep waiting / `task_wait` |
| `running` | in progress | keep waiting / `task_wait` |
| `succeeded` | done | read `result.urls` |
| `failed` | terminal failure | read `error` — do **not** retry the same `task_id` |

## Error handling

Every tool **returns an error dict instead of raising**, so you always see the reason in the tool result:

```json
{ "error": true, "error_type": "invalid_params", "message": "...", "retryable": false, "adapter_id": "wan-2-0" }
```

Always surface `message` to the user. Check `retryable` before retrying. When `adapter_id` is present on an `invalid_params` / `model_unavailable` / `content_blocked` error, the message tells you to call `get_model_card(adapter_id)` — do that to find the correct parameters.

### Error catalogue (`error_type`) and fixes

| `error_type` | retryable | Typical cause | Fix |
|---|---|---|---|
| `invalid_params` | ❌ | param out of range / wrong scenario / bad media (HTTP 400/422) | Read `message`; call `get_model_card(adapter_id)`; correct the param. |
| `content_blocked` | ❌ | prompt or media tripped moderation | Rewrite the prompt, remove sensitive terms/media, retry. |
| `quota_exceeded` | ❌ | account balance / quota too low | Tell the user to top up the cfgpu account. Don't retry. |
| `auth` | ❌ | `CFGPU_API_TOKEN` missing/invalid/expired (HTTP 401/403) | Fix the token in the environment / request `Authorization` header. |
| `rate_limit` | ✅ | too many requests (HTTP 429) | Back off and retry after a short delay. |
| `model_unavailable` | ✅* | model temporarily down, **or** retired/no-access endpoint | Try another model, or `model="auto"`. *Dead/retired endpoints come back `retryable:false` — switch models, don't retry. |
| `task_failed` | ❌ | upstream rendering failed | Read `message`; adjust inputs and resubmit as a **new** task. |
| `timeout` | ❌ | `wait` exceeded `timeout`; job may still be running | The task is **not** dead — call `task_status`/`task_wait` on the same `task_id` to collect it later. |
| `unknown` | ✅ | unclassified upstream error | Retry once; if it persists, surface `message`. |

### Domain / scenario errors (surface inside `message`)

These come from per-model `build_payload` validation, mostly as `invalid_params`:

| Symptom in `message` | Cause | Fix |
|---|---|---|
| `mixed_scenarios` | combined first/last-frame **with** reference inputs in one `generate_video` call | Use exactly one scenario per call. |
| `audio_only` | sent `reference_audios` with no reference image/video | Add ≥1 `reference_images` or `reference_videos`. |
| `media_download_failed` | a reference/first-frame URL was unreachable | Use a **publicly accessible** URL; check it loads in a browser. |
| `n>1` rejected | `n>1` on a non-`doubao-seedream-*` image model | Use a `doubao-seedream-*` model for 组图, or set `n=1`. |
| 1080p rejected on `wan-2-0-fast` t2v | WAN 2.0 Fast text-to-video maxes at 720p | Use 480p/720p, add an image input, or switch to `wan-2-0`. |
| `does not support multi_modal_reference` | sent `reference_images`/`reference_videos`/`reference_audios` to a model lacking that capability (e.g. `doubao-seedance-1-5-pro`) | Switch to a reference-capable model (`doubao-seedance-2-0`, `wan-2-0`, or a `wan-2-*-r2v`/`-videoedit`), or drop the reference inputs. The upstream raw form is `the specified task_type r2v does not support model ...` — `task_type` is server-derived from the content shape, never a client param. |
| duration out of range | `duration_seconds` not 4–15 (or `-1`) | Clamp to the model's range (Seedance 1.5 Pro caps at 12). |

### Pydantic `InputValidationError` (before the call runs)

If you call a tool with an out-of-range value the schema rejects, you get a validation error **before** any API call — e.g. `n` outside 1–15, or `duration_seconds` outside 4–15/`-1`, or an `aspect_ratio`/`resolution`/`audio_format` not in its allowed set. Fix: re-call with a value inside the documented range. (If invoking a deferred/namespaced tool, the schema must be loaded first.)

### Generic non-cfgpu errors

`{ "error": true, "message": "..." }` with no `error_type` is a non-cfgpu exception (network, DB, bug). Surface `message`; retry once for transient network errors.

## Quick recipes

```json
// Image
generate_image({ "prompt": "a red panda in the snow", "resolution": "2K", "aspect_ratio": "16:9" })

// 组图 (related set of 4) — seedream only
generate_image({ "prompt": "四格漫画：小猫的一天", "model": "doubao-seedream-5-0-lite", "n": 4 })

// Video (text-to-video)
generate_video({ "prompt": "海浪拍打沙滩，黄昏，镜头拉近", "model": "wan-2-0", "resolution": "1080p" })

// Speech
generate_audio({ "text": "你好，欢迎收听。", "model": "seed-tts-2-0", "voice": "zh_female_xiaohe_uranus_bigtts" })

// Understand an image (returns text)
understand_vision({ "prompt": "描述这张图片", "images": ["https://example.com/a.jpg"] })

// Discover models
list_models({ "task_type": "video" })
get_model_card({ "model_name": "wan-2-0" })
```
