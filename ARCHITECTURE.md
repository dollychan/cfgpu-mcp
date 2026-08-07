# CFGPU MCP Server — 架构文档

面向新开发者的设计理念与维护指南。

---

## 目录

1. [设计目标](#1-设计目标)
2. [三种部署模式](#2-三种部署模式)
3. [核心概念](#3-核心概念)
4. [分层架构](#4-分层架构)
5. [模型系统](#5-模型系统)
6. [任务生命周期](#6-任务生命周期)
7. [错误处理](#7-错误处理)
8. [文件结构速查](#8-文件结构速查)
9. [常见开发任务](#9-常见开发任务)

---

## 1. 设计目标

**单一 service 层，多种访问形态。**

同一套生成逻辑（路由 → 构建 payload → 轮询 → 返回结果）需要被三类调用方使用：MCP Host（Claude Desktop）、自建 Agent（Anthropic / OpenAI / LangGraph SDK）、命令行脚本。如果为每种调用方各写一套业务逻辑，三份代码会立即产生行为不一致。

解决方案：所有业务逻辑下沉到 `service/`，各访问层只做协议适配，不含逻辑：

```
访问层（MCP tools / agent / CLI）
        │ 参数透传，无逻辑
    service/          ← 唯一的业务逻辑所在
        │
    task_manager.py   ← 同步/异步任务调度
        │
    adapters/         ← 模型差异封装
        │
    cfgpu_client.py   ← HTTP，唯一知道 API URL 的地方
```

**推论**：新增功能时，先判断它属于哪一层，只在对应层实现，不要把 HTTP 知识带入 service 层，也不要把业务逻辑带入 tools 层。

---

## 2. 三种部署模式

| 模式 | 入口 | 适用场景 |
|------|------|---------|
| **Mode A** MCP stdio | `server.py` → `tools/` | Claude Desktop、任何 MCP Host |
| **Mode B** Agent Direct | `agent/dispatcher.py` / `agent/*_tools.py` | 自建 Agent，LLM 驱动工具调用 |
| **Mode C** CLI | `cli/main.py` → `cli/cmd_*.py` | 命令行脚本、Shell 管道 |

三种模式调用的是完全相同的 `service/` 函数，返回完全相同的 dict 结构。

### Mode A — MCP 协议适配层的存在意义

`tools/generate.py`（及 `tools/tasks.py`、`tools/models.py`）的存在是 FastMCP 的限制：FastMCP 通过函数签名来生成 JSON Schema，不支持直接传入 Pydantic 模型。因此 `tools/` 层必须把所有参数显式重声明一遍，然后原样转发给 `service/`。**这一层不含任何逻辑，修改时只需同步参数列表。**

**参数描述不重复声明**：FastMCP 从裸函数签名生成的 schema 不带任何 per-parameter 描述（只有 docstring 作为工具级描述）。为避免在 wrapper 里重抄一份 `Field(description=...)`，`server.py` 在注册完所有工具后调用 `_inject_param_descriptions(mcp)`，从 `tool_registry.get_field_descriptions(tool_name)`（描述的单一来源）回填到每个 FastMCP 工具的 `parameters.properties[*].description`。因此 MCP client 看到的 per-param 描述与 Mode B / OpenAI / LangGraph 完全一致，且只维护 `tool_registry.py` 一处。`test_schema_consistency.py` 中的 `test_mcp_schema_carries_pydantic_descriptions` 守住这一致性。

**MCP 工具名与 Mode B 的一致性**：`server.py` 将 FastMCP server 命名为 `cfgpu`（`FastMCP("cfgpu")`）。某些 MCP 客户端（如 `langchain-mcp-adapters`）加载工具时会自动拼接 `{server_name}_{tool_name}`，即 `cfgpu_generate_image`、`cfgpu_generate_video` 等。Mode B 的 `get_langgraph_tools()` 返回的 `StructuredTool` 名称为原始的 `generate_image`，不含前缀。如需通过 MCP 协议接入 LangGraph，需注意这一命名差异。

**Mode A 的两种传输：stdio 与 streamable-http**。`server.main()` 按 `settings.transport`（config.yaml）分发：

- `stdio`（默认）：单进程单用户，桌面端 MCP Host。token 取 `CFGPU_API_TOKEN` 环境变量。
- `streamable-http`：多租户、可水平扩展。`http_app.py` 自建 uvicorn + 一层纯 ASGI `RequestContextMiddleware`，把每个请求的 `Authorization: Bearer <token>` 绑定到请求级 ContextVar（`context.py`），底层 `CFGPUClient` 逐请求注入该 token——共享连接池服务所有租户，不再全局共用一个 token。uvicorn 不在核心依赖里，需装 `http` extra：`pip install -e ".[http]"`。配 `stateless_http=True` 时每请求独立,可放在 LB 后跑 N 个实例。**`http.stateless` 必须为 `true`**：`false` 时 FastMCP 在长生命周期的 session task 内执行工具调用，请求级 ContextVar 会被冻结在该 session 的首个请求上，导致后续请求复用首个调用者的 token（跨租户泄漏）；`build_http_app()` 因此在 `stateless=false` 时直接拒绝启动。

两种传输共用同一套 `service/`，唯一的有状态点是 task 存储（见 §6，可配置 SQLite / Postgres）。详见 `docs/streamable/http-mcp-servers.md`。

### Mode B — 工具 schema 的单一来源

`tool_registry.py` 是 Mode B 的核心：它定义了所有工具的 Pydantic 输入模型，并提供：

- `get_anthropic_tools()` → Anthropic SDK 格式
- `get_openai_tools()` → OpenAI SDK 格式（`agent/openai_tools.py`）
- `get_langgraph_tools()` → LangChain `StructuredTool` 列表（`agent/langgraph_tools.py`）

三个函数共享同一批 Pydantic 模型（`_REGISTRY`），schema 只需维护一处。

### Mode C — stdout/stderr 分离

CLI 的核心设计原则：**stdout = 纯 URL（可 pipe），stderr = 进度 + 元数据 + 错误**。这让 `cfgpu generate image "..." | xargs open` 之类的 Shell 组合可以正常工作。每个 `cmd_*.py` 文件用自己的 `_run()` 函数包裹 `asyncio.run()`，并在 `finally` 中调用 `config.close()` 清理 HTTP 连接和 DB。

---

## 3. 核心概念

### 3.1 四种模型标识符

每个模型有四个 ID，**绝对不能混用**：

| 名称 | 示例 | 用途 |
|------|------|------|
| `adapter_id` | `wan-2-0-fast` | 目录名、registry key、内部日志；**不对外暴露** |
| `display_name` | `WAN 2.0 Fast (...)` | `list_models()` 返回值中展示 |
| `cfgpu_model_id` | `wan-video-fast` | **只在** `build_payload()` 里写入 API 请求体；**不对外暴露** |
| `model_name` | `wan-video-fast` | 唯一对外公开的模型标识——`model=` 参数、`list_models()` 的 `model_id`、`model_used`、错误里的 `model_id` 一律用它 |

`list_models()` 返回 `model_id`（即 `model_name`）+ `display_name`，**不回传 `adapter_id` 或 `cfgpu_model_id`**。新开发者最常见的错误：在 `build_payload()` 以外的地方使用 `cfgpu_model_id`，或者把 `adapter_id` 传给调用方/写进对外可见的结果。`registry.get()` 依次按 `model_name` → `adapter_id` → `cfgpu_model_id` → `display_name` 解析，因此旧调用方传 `adapter_id`/`cfgpu_model_id` 仍能命中，但工具 schema 的 `model` 枚举、`list_models`、`model_used`、错误 `model_id` 只会**出现** `model_name` 的取值。

**四种 `task_type`**：`image` / `video` / `audio` 三类都是**媒体生成**（返回 `urls`），而 `understand`（视觉理解 / 图像推理 / 视频理解，如 Qwen3-VL）是**返回文本**的对话类任务——走 OpenAI 兼容的 `/model/v1/chat/completions`，结果落在 `NormalizedResult.message`（assistant 消息 `{role, content[, reasoning_content]}`，回答是 `content`、Thinking 模型的推理过程是 `reasoning_content`）与 `response_id`，`urls` 为空。其工具返回 chat-completion 结构 `{id, model, message, payload[, usage]}`（`usage` 受 `return_metadata` 控制）。路由、`supports()`、`select_model()` 都按 `task_type` 隔离，understand 请求永远不会选中媒体模型，反之亦然。

### 3.2 同步模型 vs 异步模型

通过 `adapter.yaml` 中的 `is_async` 字段区分：

| | 同步（`is_async: false`） | 异步（`is_async: true`） |
|-|--------------------------|--------------------------|
| 代表模型 | Seedream（图片）、MiniMax 语音、Qwen3-VL（视觉理解） | WAN 2.0（视频）、GPT Image 2、Nano Banana（图片）、豆包 seed-tts |
| POST 响应 | 直接包含图片 URL | 包含 `task_id`，需轮询 |
| `TaskManager.create()` | 立即写 `succeeded` 到 DB | 写 `pending`，等待轮询 |
| `TaskManager.wait()` | 立即返回（no-op） | 指数退避轮询直到完成 |
| 典型耗时 | 2–5 秒 | 30–600 秒 |

两类模型对调用方（service 层）完全透明，`TaskManager` 内部已处理差异。

### 3.3 `model` 参数与路由逻辑

`model` 接受三种形态，由 `ModelRouter.resolve(req)` 统一分发：

| 取值 | 例子 | 行为 |
|---|---|---|
| 单个 id（str） | `"wan-2-0"` | `get_adapter()` 精确取该模型，跳过打分；仍调一次 `supports(req)` 校验 |
| id 列表（list[str]） | `["wan-2-0", "wan-2-0-fast"]` | `select_model(allowed=...)` 仅在该候选范围内打分选最优 |
| `"auto"`（str，默认） | `"auto"` | `select_model()` 在全部模型中打分选最优 |

列表与 `"auto"` 都只产出**一个** task / 一个结果，区别仅是候选池大小；列表里若含未知或与当前任务类型不符的 id，`select_model()` 抛 `invalid_params`。**单个 id 的精确路径同样会过 `supports(req)`**：auto/列表路径靠 `supports()` 过滤候选，但直接点名的模型若与任务类型 / 能力不符，否则会让 `build_payload()` 里的 `assert` 泄漏成裸 `AssertionError`——因此 `resolve()` 在此也校验，不通过则抛带 `model_id` 的 `invalid_params`（触发 `get_model_card` 提示）。

`ModelRouter.select_model()` 对候选模型打分，最高分获选：

```
基础分（quality_tier）:
  fast   → speed_tier × 2 - cost_tier
  best   → cost_tier × 2 + speed_tier - cost_tier（以 cost_tier 作质量代理：越贵≈越优）
  balanced → speed_tier - cost_tier

加分项:
  图像请求有 reference_images 且模型支持 multi_image_fusion/multi_image_group → +3
  视频请求有 reference_images/videos/audios 且模型支持 multi_modal_reference → +3
  中文 prompt 且模型是 doubao-seedream-* → +2
```

这意味着：默认 balanced 模式下，低成本高速模型优先；中文 prompt 会偏向 Seedream；有参考媒体时优先选择支持多模态参考的模型。

---

## 4. 分层架构

### 完整请求路径（以 `generate_image` 为例）

```
用户 / LLM / CLI
    │
    ▼
tools/generate.py::generate_image()     [Mode A]
agent/dispatcher.py::dispatch_tool()    [Mode B]
cli/cmd_generate.py::image_cmd()        [Mode C]
    │ 参数透传
    ▼
service/image.py::generate_image()
    │
    ├─ config.get_registry()
    │      └─ AdapterRegistry（YAML + Python 类，已加载）
    │
    ├─ ModelRouter.resolve(req)         按 req.model 形态分发：
    │      ├─ get_adapter(model)            model 为单个 id
    │      └─ select_model(req, allowed)    model 为列表（受限候选）或 "auto"（全部）
    │          └─ adapter.supports(req) + _score(adapter, req)
    │
    ├─ config.get_client()             CFGPUClient（单例）
    ├─ config.get_db()                 aiosqlite 连接（单例）
    │
    ▼
TaskManager.create(adapter, req)
    │
    ├─ adapter.build_payload(req)      统一 schema → CFGPU API 格式
    │
    ├─ [is_async=false] CFGPUClient.post() → adapter.parse_response() → DB 写 succeeded
    └─ [is_async=true]  CFGPUClient.post() → 取 task_id（取不到则抛 CFGPUError）→ DB 写 pending
    │
    ▼ （wait=True 时继续）
TaskManager.wait(task, adapter, req)
    │
    └─ 轮询循环（指数退避）
           CFGPUClient.get(poll_endpoint) → adapter.parse_response() → DB 更新
    │
    ▼
service 返回 dict → 访问层格式化 → 用户
```

### 单例资源（`config.py`）

`get_settings()` / `get_registry()` / `get_client()` / `get_task_repository()` 均为模块级单例，首次调用时初始化，后续调用直接返回已有实例。这避免了每次请求重新建立 HTTP 连接、重新解析 YAML 或重开数据库。

- `get_settings()` 从 config.yaml 加载配置（`settings.py`）。
- `get_client()` 构造的 `CFGPUClient` **不再持有 token**——共享连接池，token 逐请求从 ContextVar 解析；`base_url`/超时来自 settings。
- `get_task_repository()` 按 `task_db.url` 的 scheme 选择 `SqliteTaskRepository` 或 `PostgresTaskRepository`（`client/repository.py`）。取代了旧的 `get_db()`。该单例的初始化是 `async` 且首调时有 `await`，因此用 `asyncio.Lock` + 双重检查保护：否则一批并发工具调用会各自看到 `_repo is None` 并同时建仓库 / 跑建表 DDL，撞上 Postgres `CREATE TABLE` 的目录竞争（`pg_type_typname_nsp_index` 唯一键冲突 → `duplicate key (tasks)`）。跨实例同时启动的竞争则由 `PostgresTaskRepository._init_schema` 的事务级 advisory lock（`pg_advisory_xact_lock`）串行化建表，输家随后跑 `CREATE ... IF NOT EXISTS` 成空操作。

程序退出时必须调用 `await config.close()`，以关闭 `aiohttp.ClientSession` 和 task 仓库（SQLite 连接 / Postgres 连接池）。各访问层各自负责调用：CLI 在 `_run()` 的 `finally` 中调用；stdio MCP server 通过 FastMCP 的 `lifespan` 上下文在关闭阶段调用。**不能用 `atexit` + `asyncio.run()`**——那会新建事件循环去关闭绑定在 server 原循环上的 `ClientSession`，触发 "Event loop is closed" 告警；lifespan 在 server 自身的事件循环内退出，确保 session 在它被创建的同一循环上关闭。

**streamable-http 下的清理差异**：`streamable_http_app()` 用 session manager 的 lifespan 覆盖了构造器 lifespan，且后者在 stateless 模式下每请求运行一次——因此共享单例**不能**在 `server._lifespan` 里关（已加 `transport == "stdio"` 门控）。HTTP 进程级清理由 `http_app.RequestContextMiddleware` 在 ASGI shutdown 终态（`lifespan.shutdown.complete` 或 `lifespan.shutdown.failed`）统一 `config.close()` 一次；清理在转发该消息**之后**执行并包在 try 中，避免缓慢或抛错的 `close()` 阻塞 uvicorn 关停。

---

## 5. 模型系统

### 5.1 Adapter 类层次

```
ModelAdapter (ABC, adapters/base.py)
    │
    ├── GenericAdapter            YAML 驱动，适合 payload 结构简单的模型
    │       └── 通过 payload_mapping DSL 把 req 字段映射到 API 字段
    │
    ├── _AsyncImageBase           共享 data-wrapped 响应处理 + _finalize_payload()
    │       ├── GptImage2Adapter      gpt-image-2
    │       └── NanoBananaAdapter     nano-banana-2 / nano-banana-pro（extends 链）
    │
    ├── SeedanceVideoAdapter           手写 Python，处理复杂 content 数组构建
    │       └── 同时服务 wan-2-0 和 wan-2-0-fast（通过 extends 链）
    │
    ├── SeedreamAdapter           手写 Python，处理 resolution×ratio → size 映射
    │
    ├── HappyHorseVideoAdapter    手写 Python，DashScope 风格嵌套 payload
    │       └── input.media 数组 + parameters 对象；大写状态码归一化
    │
    ├── WanVideoAdapter           手写 Python，万相 2.6/2.7 视频家族（基类=wan2.7-i2v）
    │       ├── 请求用 HappyHorse 风格 input/parameters（轮询用 Seedance 标准响应 content.videoUrl）；input 由 _build_input 钩子构建
    │       ├── 万相 2.7（media 数组）：WanVideoR2VAdapter / WanVideoT2VAdapter / WanVideoEditAdapter（type=video+reference_image）
    │       └── 万相 2.6（扁平 input 字段，无 media）：Wan26VideoT2VAdapter / Wan26VideoI2VAdapter（img_url/audio_url）/ Wan26VideoR2VAdapter（reference_urls）
    │
    └── KlingVideoAdapter         手写 Python，可灵 O1 的 flat payload
            ├── resolution×ratio → size 像素串、quality_tier → std/pro mode、with_audio → sound=on/off
            ├── 素材走两个并列数组：image_list[{image,type:first_frame|end_frame}]（无 type = 参考图）
            │   与 video_list[{video_url,refer_type:feature|base}]；refer_type=base（视频编辑）时不下发 seconds
            ├── 轮询响应把结果嵌在 taskResult.videos[].url（顶层 status=completed），parse_response 读该数组
            └── 同时服务 kling-video-o1 和 kling-v3-omni（通过 extends 链）
```

**选择 GenericAdapter 还是 Python Adapter？**

- API payload 是简单的字段映射（改个名字、加个常量）→ 用 `payload_mapping` YAML DSL，不写 Python
- 有复杂逻辑（条件分支、数组构建、特殊计算）→ 写 Python Adapter

### 5.2 YAML extends 继承

`wan-2-0-fast` 的 YAML 只需要写和父模型的**差异**：

```yaml
# wan-2-0-fast/adapter.yaml
extends: wan-2-0
adapter_id: wan-2-0-fast
cfgpu_model_id: wan-video-fast
cost_tier: 2
speed_tier: 4
poll_config:
  default_timeout: 300   # 只覆盖这一项，其他 poll_config 字段继承
```

`_merge_extends()` 的合并规则：
- 普通字段（字符串、整数）：子模型覆盖父模型
- dict 字段（`poll_config`）：**field-level merge**，子模型只覆盖指定键
- `extends` 字段在合并后**保留**在 merged dict 中——这是关键，`_instantiate()` 需要它来追溯父模型对应的 Python 类

### 5.3 Python Adapter 注册机制

```python
# adapters/seedance_video.py
@register_python_adapter        # 把 SeedanceVideoAdapter 注册到 _PYTHON_ADAPTERS["wan-2-0"]
class SeedanceVideoAdapter(ModelAdapter):
    adapter_id = "wan-2-0"
    ...
```

```python
# adapters/__init__.py
from cfgpu_mcp.adapters import seedance_video, seedream, async_image, happyhorse_video  # 触发 @register_python_adapter
```

`_instantiate()` 的查找顺序：
1. 在 `_PYTHON_ADAPTERS` 中查找 `adapter_id`（e.g. `wan-2-0-fast`）→ 未找到
2. 沿 `extends` 链**逐级向上**查找父 ID（`wan-2-0`）→ 找到 `SeedanceVideoAdapter`
3. 用 `SeedanceVideoAdapter.from_config(merged_config)` 实例化，此时实例的 `adapter_id`、`cfgpu_model_id` 等已被 merged config 覆盖

这就是 `wan-2-0-fast`、`doubao-seedance-2-0` / `-fast`、`doubao-seedance-1-5-pro` 如何复用 `SeedanceVideoAdapter` 的全部逻辑，不需要各自单独的 Python 文件。

第 2 步必须沿整条链向上走，而不是只看一层父级。例如 `nano-banana-pro-premium` → `nano-banana-pro` → `nano-banana-2`：只有 `nano-banana-2` 注册了 `NanoBananaAdapter`，中间的 `nano-banana-pro` 没有。若只查一层，孙级变体会 fallback 到 `GenericAdapter`，由于没有 `payload_mapping` 而构建出空 payload，导致 API 报 `model参数不能为空`。`_instantiate()` 因此接收完整的 `raw_configs`，以便逐级追溯 `extends`。

### 5.4 Model Card 合并

`get_model_card()` 读取 `card.md`，如果 `adapter.yaml` 中有 `extends` 或 `card_base`，则将变体的 card 与父模型的 card 合并：同名 `##` 节标题，子模型覆盖；子模型独有节，追加到末尾。

---

## 6. 任务生命周期

### 状态机

```
pending → running → succeeded
                 └→ failed
```

同步模型（Seedream）直接从 `pending` 跳到 `succeeded`，不经过 `running`。

### Task 存储：可配置仓库（SQLite / Postgres）

task 状态通过 `TaskRepository` 接口持久化（`client/repository.py`），后端由 config.yaml 的 `task_db.url` 的 scheme 决定：

- `sqlite:///path` → `SqliteTaskRepository`（单实例 / stdio / CLI，WAL 模式）
- `postgresql://...` → `PostgresTaskRepository`（asyncpg 连接池；多实例水平扩展，`client/postgres_repo.py`，需 `[postgres]` 可选依赖）

`task_db.url` 支持 `$VAR` / `${VAR}` 形式从环境变量读取（`settings._expand_env`），便于把带密码的 DB URL 移出 config.yaml；引用的变量未设置时启动即报错，而非把字面量 `"$DATABASE_URL"` 传给驱动。

`load_settings()` 开头会调用 `_load_dotenv()`：若当前目录（或 `CFGPU_DOTENV` 指定路径）存在 `.env`，用 python-dotenv 注入环境，`override=False` 保证真实环境变量仍优先（`env > .env > config.yaml`）。python-dotenv 缺失时静默跳过，stdio 仍可零配置运行。`.env` 已在 `.gitignore` 中。

两个后端返回**完全一致的行结构**（JSON 列存 text，读时 `json.loads`），上层 `Task` / `_present` 对后端无感。

```sql
tasks (
    id          TEXT PRIMARY KEY,   -- CFGPU 返回的 task_id（异步）或 uuid4（同步）
    adapter_id  TEXT,               -- 用于 task_wait 时重建 adapter
    status      TEXT,               -- pending | running | succeeded | failed
    payload     TEXT,               -- JSON，原始 API 请求体
    result      TEXT,               -- JSON，NormalizedResult.to_dict()
    error       TEXT,               -- 失败原因
    created_at  REAL / DOUBLE PRECISION,
    updated_at  REAL / DOUBLE PRECISION
)
-- Postgres 额外建索引 idx_tasks_status_created(status, created_at)
```

DB 的作用：`task status <task_id>` / `task wait <task_id>` 需要在进程重启后仍能查询和恢复任务。如果只在内存中存储，异步工作流（`--no-wait` / `wait=false` 后稍后查询）就无法工作。

**多进程 / 多实例共享**：stdio 下每个 agent spawn 独立 server 进程，但共指同一 SQLite 文件（`~/.cfgpu/tasks.db`，WAL 支持并发读 + 单写）；需进程隔离则各设不同 `task_db.url`。streamable-http 多实例水平扩展时改用 Postgres，所有实例共指同一库——本地 SQLite 文件无法跨实例共享。

**客户端驱动轮询**：`service/task.py` 的 `get_status()` 对**非终态的异步任务**（pending/running）或「已成功但 result 无 URL」的任务，做**一次**实时上游轮询并落库。这是 `wait=false` 客户端驱动模型的关键——每次 `task_status` 调用都带着调用方 token，服务端借它把异步任务往前推一步，无需为此挂住连接；客户端断开后凭 task_id 重连即可继续。仅对异步模型（`adapter.is_async`）执行——同步模型无 `poll_endpoint`，跳过；轮询失败以 `logger.debug()` 记录、返回 DB 中的 stale 值，不阻断。

### 指数退避轮询

`_STATUS_MAP`（模块级常量）将 CFGPU API 返回的原始状态映射到内部状态（`succeeded` / `failed` / `running` / `pending`），避免每次 `poll()` 调用重建 dict。

```python
interval = base_interval                        # 默认 5s
while not done:
    await asyncio.sleep(interval)
    interval = min(interval * backoff_factor, max_interval)  # 最长 20s
```

每个模型的轮询参数在 `adapter.yaml` 的 `poll_config` 中配置，`SeedanceVideoAdapter` 还根据请求参数（时长、是否有参考媒体）动态延长 `estimate_poll_timeout()`。

---

## 7. 错误处理

### CFGPUError 的两条识别路径

```python
CFGPUError.from_http_response(status, body)
    # 路径1：从 body 的 error.code 识别（优先）
    body = {"error": {"code": "quota_exceeded", "message": "..."}}
    → error_type = "quota_exceeded"

    # 路径2：从 HTTP status 识别（fallback）
    status = 401 → error_type = "auth"
    status = 429 → error_type = "rate_limit"
```

`retryable` 字段由 `_RETRYABLE` 集合决定（`rate_limit`、`model_unavailable`、`unknown`），调用方可据此决定是否重试。目前系统内部没有自动重试，该字段供外部调用方使用。

### 失效 / 无权限端点：快速失败（非重试）

上游对**已下线或无访问权限**的模型有时返回 `HTTP 200 + error body`（OpenAI 兼容结构），无法仅凭 status 判断。`CFGPUClient._request()` 因此在 `resp.ok` 之外，额外检查 `body["error"]` 是否为非空 dict，命中则同样走 `from_http_response`。

若不特判，这类错误会落到 `error_type=unknown`（属于 `_RETRYABLE`），导致调用方反复重试一个永远不会恢复的端点。`from_http_response` 通过 `_is_dead_endpoint(code, raw_msg)` 识别这种情况：

- body code 为 `model_not_found`，或
- 消息文本包含 `does not exist` / `do not have access` / `does not have access` / `no access to`（见 `_DEAD_ENDPOINT_PHRASES`）

命中后强制 `error_type = "model_unavailable"` 且 `retryable = False`（显式覆盖默认值——`model_unavailable` 默认在 `_RETRYABLE` 中，用于 router 的"换个模型重试"语义）。

### card.md 提示机制

当错误属于 `invalid_params`、`model_unavailable` 或 `content_blocked` 类型时，service 层（`image.py` / `video.py` / `audio.py` / `vision.py` / `task.py`）会把 `adapter.model_name` 写入 `CFGPUError.model_id`。`to_tool_result_dict()` 在 `message` 中追加提示：`"请调用 get_model_card 获取模型 {model_id} 的详细参数说明和使用示例。"`, 同时在 dict 中添加 `model_id` 字段，方便 LLM 直接用该值调用 `get_model_card`。**agent 侧只见 `model_id`（全局唯一的 `model_name`），从不暴露 MCP 内部的 `adapter_id` / `cfgpu_model_id`**——`registry.get()` 同时按 `model_name` 解析，故 agent 拿 `model_id` 即可命中。其他错误类型（`auth`、`rate_limit`、`timeout` 等）不追加提示。

### 错误在各层的展示方式

| 层 | 展示方式 |
|----|---------|
| MCP tools（`tools/`） | 工具内部 try/except → 返回 `{"error": True, "error_type": ..., "message": ..., "retryable": ..., "model_id": ...}` dict，LLM 可直接读取 |
| agent/dispatcher | `dispatch_tool()` 内部 try/except → 返回同上 error dict（`ValueError` 除外，编程错误继续上抛）|
| CLI | `print_error()` 打印到 stderr，`sys.exit(1)` |

**为什么 MCP tools 不依赖 FastMCP 的异常捕获？**  
FastMCP 捕获异常后设置 `isError: true`，但 MCP 客户端是否将其内容暴露给 LLM 取决于具体实现，行为不一致。主动返回 error dict 可确保错误消息始终出现在 tool result 内容中，LLM 一定能看到并推理。

`tool_error_dict(e)` 定义在 `errors.py`，`tools/` 层和 `dispatcher.py` 均通过 import 共用它。

### 工具结果的 artifact 标记

MCP 工具（Mode A）在成功返回包含已生成媒体的结果时，会在结果顶层追加 `"artifact": True`，与 error dict 的 `"error": True` 顶层布尔标记对称，供 MCP 客户端快速判断"本次结果含可渲染产物"。

`task_status` / `task_wait` 的返回结构与 `generate_*` 对齐：`service/task.py` 的 `_present(task)` 在任务成功且有 URL 时直接返回扁平的 `NormalizedResult` dict（顶层 `urls` / `expires_at` / 元数据），与 `generate_*` 完全一致；未完成时返回 `{task_id, status}` 信封（对应 generate 的 `wait=False`）。因此不再出现 `result` 嵌套层。**失败任务**由 `_raise_if_failed(task)` 抛出标准 `CFGPUError(task_failed)`（带 `model_id`——由 `registry.get(task.adapter_id).model_name` 映射得到，不暴露内部 `adapter_id`），经工具层 `tool_error_dict` 转成与 `task_wait` / `generate_*` 完全一致的 error dict——`task_status` 不再有独有的 `{status: "failed", error: "<string>"}` 信封。`wait_for_task()` 重建用于超时估算的最小 `req` 时也按 `adapter.task_type` 完整映射到对应 Input 类型（image/video/audio/understand），避免把错误的 Input 喂给 per-type 的 `estimate_poll_timeout()` 覆盖。

**`payload` 字段（真实 API 请求体回传）**：所有成功结果（`generate_*` 以及 `_present` 的成功分支）在 `NormalizedResult` 元数据之外追加 `payload` 字段，内容是 `Task.public_payload()` —— 即真正 POST 给该模型专属 API 的请求体（`adapter.build_payload(req)` 的产物，含 `cfgpu_model_id` 与各模型私有字段），而非通用工具入参。`public_payload()` 会剥除内部回显用的保留键 `_requested_aspect_ratio`（见 §异步 aspect_ratio 兜底），保证只暴露真实发往上游的字段。**该字段始终返回，不受 `return_metadata` 影响**：`return_metadata=False` 的精简输出（`urls` / `expires_at`）同样带上 `payload`。

**`request_id`（调用方关联标识回显）**：`generate_*` 接受一个可选的 `request_id`，由调用方在**发起时**自选，用来把稍后经 `task_status` / `task_wait` 返回的异步 artifact / 失败 join 回原始的 generate 请求——异步流程里两者分属不同 tool_call，而 `task_id` 要等 POST 返回才有、同步模型更无 `task_id`，因此不能充当发起即得的关联键。实现复用 payload 保留键机制：`TaskManager.create()`（同步与异步两条路）经 `_stash_internal()` 把 `req.request_id` 存进 `_request_id` 保留键，`public_payload()` 与 `_requested_aspect_ratio` 一并剥除，故绝不上行到上游 API。回显在 **service 层**完成（`tool_registry.stamp_echo()`），而非 MCP wrapper——因此 MCP（Mode A）、Agent dispatcher（Mode B）、CLI（Mode C）三种直连 service 的模式一致生效；`_present()` 从 `task.payload` 取回并盖章成功/pending 两种形状，`CFGPUError.request_id` 则让失败结果（`to_tool_result_dict`）也带上它。一律"有值才加"（`setdefault`），不传时结果结构不变。`understand_vision` 恒同步、单次返回、无关联缺口，故不设此参数。

**`caption`（产物标签回显）**：与 `request_id` 同机制的第二个回显字段（同一个 `stamp_echo()`、同一套 `_stash_internal()` / `public_payload()` 保留键，键名 `_caption`），但用途不同——它是给**产物**起的一句人类可读短标签，服务端只存不解释。存在的理由是自建素材台账的客户端（DeerFlow / cf-dream 把每个生成产物登记为可用短 id 引用的 material）：标签若不能在发起时携带，台账条目就是无名的，只能事后再花一次工具调用补名字；而把它存进任务记录，正是**两段式**（`wait=False` → `task_wait`，产物晚一次 tool_call 才存在）无需客户端自持 `task_id → 标签` 映射的原因。两处刻意的取舍：①超长**截断不报错**（`CAPTION_MAX_CHARS=200`，`CaptionStr` 的 `AfterValidator`）——标签不影响出图，为它失败一整次调用不划算；②**失败路径不回显**——`CFGPUError` 带 `request_id` 不带 `caption`，调用失败即无产物可标注（由 `test_failed_task_carries_request_id_but_not_caption` 钉住）。三个 generate 工具的字段声明共用 `caption_field()`，避免三份副本漂移。

`tool_registry.annotate_artifact(result)` 是单一实现：当 `result` 含非空 `urls`（顶层；并保留对嵌套 `result.urls` 的兼容判断作兜底）时打标记；无 URL 的结果（`wait=False` 的 pending、轮询中的 running、error dict）原样返回。`tools/generate.py`、`tools/tasks.py` 在各工具 `return` 处包一层调用。该标记只在 MCP 工具层加，service / dispatcher / CLI 的原始返回不受影响。

---

## 8. 文件结构速查

```
src/cfgpu_mcp/
│
├── server.py                   Mode A 入口，注册工具，按 settings.transport 启动 stdio / streamable-http
├── settings.py                 config.yaml 加载（env override）→ Settings
├── context.py                  请求级 token ContextVar（streamable-http 多租户）
├── http_app.py                 RequestContextMiddleware（token + 清理）+ build_http_app()
├── config.py                   单例资源管理（settings / registry / client / task_repository）
├── tool_registry.py            Pydantic 输入模型 + get_anthropic_tools() + NormalizedResult
├── router.py                   model="auto" 评分选模型
├── task_manager.py             同步/异步任务创建、轮询、等待
├── errors.py                   CFGPUError，HTTP 状态 → error_type 映射
│
├── adapters/
│   ├── base.py                 ModelAdapter ABC + @register_python_adapter + PollConfig
│   ├── registry.py             YAML 加载、extends 合并、Python 类解析
│   ├── generic.py              YAML DSL 驱动的通用 adapter
│   ├── seedance_video.py       Seedance 系列 Python Adapter（WAN 2.0 / WAN 2.0 Fast / Seedance 2.0 / 2.0 Fast / 1.5 Pro 共用）
│   ├── seedream.py             Seedream 系列 Python Adapter（同步模型；5.0 lite / 5.0 Pro / 4.5 / 4.0 共用。Pro 为单图模型，n>1 报错；1K 档位透传 size="1K"）
│   ├── async_image.py          _AsyncImageBase + GptImage2 / NanoBanana Adapter
│   ├── happyhorse_video.py     HappyHorse 的 Python Adapter（DashScope 风格）
│   ├── kling_video.py          Kling Video O1 的 Python Adapter（flat prompt/size/mode/seconds/sound + image_list/video_list）
│   ├── wan_video.py            万相 2.6/2.7 视频家族 Adapter（HappyHorse 风格请求 + Seedance 标准轮询；_build_input 钩子区分 2.6 扁平字段 / 2.7 media 数组）
│   ├── audio_tts.py            语音合成（task_type=audio）：SeedTTSAdapter（豆包 seed-tts，异步）+ MiniMaxSpeechAdapter（MiniMax speech，同步）
│   ├── vision_chat.py          视觉理解（task_type=understand）：QwenVisionAdapter（Qwen3-VL，OpenAI 兼容 chat/completions，同步，返回文本）
│   └── __init__.py             导入 seedance_video、seedream、async_image、happyhorse_video、kling_video、wan_video、audio_tts、vision_chat 触发注册
│
├── models/
│   ├── wan-2-0/
│   │   ├── adapter.yaml        完整配置
│   │   └── card.md             模型说明
│   ├── wan-2-0-fast/
│   │   ├── adapter.yaml        只写差异，extends: wan-2-0
│   │   └── card.md
│   ├── doubao-seedance-1-5-pro/
│   │   ├── adapter.yaml        extends: wan-2-0, card_base: ~（不继承 card.md）
│   │   └── card.md
│   ├── doubao-seedance-2-0/
│   │   ├── adapter.yaml        Seedance 2.0，API 等同 WAN 2.0，extends: wan-2-0, card_base: ~
│   │   └── card.md
│   ├── doubao-seedance-2-0-fast/
│   │   ├── adapter.yaml        extends: doubao-seedance-2-0, card_base: ~
│   │   └── card.md
│   ├── doubao-seedance-2-0-mini/
│   │   ├── adapter.yaml        extends: doubao-seedance-2-0, card_base: ~（高性价比）
│   │   └── card.md
│   ├── doubao-seedream-5-0-lite/
│   │   ├── adapter.yaml
│   │   └── card.md
│   ├── doubao-seedream-5-0-pro/
│   │   ├── adapter.yaml        extends: doubao-seedream-5-0-lite, card_base: ~（单图、1K/2K，不支持组图/联网搜索）
│   │   └── card.md
│   ├── doubao-seedream-4-5/
│   │   ├── adapter.yaml        extends: doubao-seedream-5-0-lite, card_base: ~
│   │   └── card.md
│   └── doubao-seedream-4-0/
│       ├── adapter.yaml        extends: doubao-seedream-5-0-lite, card_base: ~
│       └── card.md
│   ├── happyhorse-1-0-t2v/
│   │   ├── adapter.yaml        DashScope 风格异步视频模型
│   │   └── card.md
│   ├── happyhorse-1-0-r2v/
│   │   ├── adapter.yaml        参考生视频，extends: happyhorse-1-0-t2v, card_base: ~
│   │   └── card.md
│   ├── happyhorse-1-0-video-edit/
│   │   ├── adapter.yaml        视频编辑（源视频+参考图），extends: happyhorse-1-0-t2v, card_base: ~
│   │   └── card.md
│   ├── kling-video-o1/
│   │   ├── adapter.yaml        可灵 O1，flat payload，目前仅 text_to_video
│   │   └── card.md
│   ├── kling-v3-omni/
│   │   ├── adapter.yaml        可灵 V3 全能版，extends: kling-video-o1, card_base: ~
│   │   └── card.md
│   ├── wan-2-7-i2v/
│   │   ├── adapter.yaml        万相 2.7 图生视频，独立 WanVideoAdapter（仅 image_to_video）
│   │   └── card.md
│   ├── wan-2-7-r2v/
│   │   ├── adapter.yaml        万相 2.7 参考生视频，WanVideoR2VAdapter（multi_modal_reference）
│   │   └── card.md
│   ├── wan-2-7-t2v/
│   │   ├── adapter.yaml        万相 2.7 文生视频，WanVideoT2VAdapter（text_to_video，无 media）
│   │   └── card.md
│   ├── wan-2-7-videoedit/
│   │   ├── adapter.yaml        万相 2.7 视频编辑，WanVideoEditAdapter（video_edit，源视频+参考图）
│   │   └── card.md
│   ├── wan-2-6-t2v/
│   │   ├── adapter.yaml        万相 2.6 文生视频，Wan26VideoT2VAdapter（扁平 input）
│   │   └── card.md
│   ├── wan-2-6-i2v/
│   │   ├── adapter.yaml        万相 2.6 图生视频，Wan26VideoI2VAdapter（img_url + 可选 audio_url）
│   │   └── card.md
│   ├── wan-2-6-r2v/
│   │   ├── adapter.yaml        万相 2.6 参考生视频，Wan26VideoR2VAdapter（reference_urls 扁平列表）
│   │   └── card.md
│   ├── seed-tts-2-0/
│   │   ├── adapter.yaml        豆包语音合成 2.0（task_type=audio，异步，SeedTTSAdapter）
│   │   └── card.md
│   ├── minimax-speech-2-8-hd/
│   │   ├── adapter.yaml        MiniMax 语音 2.8 HD（task_type=audio，同步，MiniMaxSpeechAdapter）
│   │   └── card.md
│   ├── minimax-speech-2-8-turbo/
│   │   ├── adapter.yaml        extends: minimax-speech-2-8-hd（更快更省）
│   │   └── card.md
│   ├── qwen-3-6-plus/
│   │   ├── adapter.yaml        Qwen3.6-Plus（task_type=understand，同步，QwenVisionAdapter）
│   │   └── card.md
│   ├── gpt-image-2/
│   │   ├── adapter.yaml
│   │   └── card.md
│   ├── nano-banana-2/
│   │   ├── adapter.yaml
│   │   └── card.md
│   ├── nano-banana-pro/
│   │   ├── adapter.yaml        extends: nano-banana-2
│   │   └── card.md
│
├── service/                    业务逻辑层（三种模式共享）
│   ├── image.py                generate_image()
│   ├── video.py                generate_video()
│   ├── audio.py                generate_audio()（语音合成 / TTS）
│   ├── vision.py               understand_vision()（视觉理解 / 图像推理 / 视频理解，返回文本）
│   ├── task.py                 get_status() / wait_for_task()
│   └── model.py                list_models() / get_model_card()
│
├── tools/                      Mode A：FastMCP 工具注册（参数重声明层）
│   ├── generate.py
│   ├── understand.py           understand_vision 工具
│   ├── tasks.py
│   └── models.py
│
├── agent/                      Mode B：SDK 直接访问接口
│   ├── dispatcher.py           Anthropic SDK dispatch_tool()
│   ├── openai_tools.py         get_openai_tools() + openai_dispatch_tool()
│   └── langgraph_tools.py      get_langgraph_tools() → StructuredTool 列表
│
├── cli/                        Mode C：命令行入口
│   ├── main.py                 click 根命令组
│   ├── cmd_generate.py         cfgpu generate image/video/audio
│   ├── cmd_understand.py       cfgpu understand（视觉理解，文本输出 stdout）
│   ├── cmd_task.py             cfgpu task status/wait
│   ├── cmd_models.py           cfgpu models list/card
│   └── output.py               print_result() / run_with_progress() / print_error()
│
└── client/
    ├── cfgpu_client.py         aiohttp HTTP 客户端（最底层）；token 逐请求注入；CFGPU_DRY_RUN=1 时记录请求日志不发送
    ├── repository.py           TaskRepository 接口 + SqliteTaskRepository + create_task_repository() 工厂
    ├── postgres_repo.py        PostgresTaskRepository（asyncpg 连接池，[postgres] 可选依赖）
    └── db.py                   SQLite CRUD（insert/update/get/list）+ open_db(path)
```

> 项目根另有 `config.example.yaml`（配置模板，运行时 `config.yaml` 被 gitignore）。streamable-http 多租户/扩展设计详见 `docs/streamable/http-mcp-servers.md`。

---

## 9. 常见开发任务

### 添加新模型（仅配置差异）

适用场景：新模型和已有模型 payload 结构相同，只有 `cfgpu_model_id`、速度、费用不同。

```bash
mkdir src/cfgpu_mcp/models/wan-2-0-turbo
```

`adapter.yaml`:
```yaml
extends: wan-2-0
adapter_id: wan-2-0-turbo
display_name: "WAN 2.0 Turbo"
cfgpu_model_id: wan-video-turbo
model_name: wan-video-turbo   # 唯一对外暴露的标识，通常与 cfgpu_model_id 同名即可
cost_tier: 4
speed_tier: 5
poll_config:
  default_timeout: 200
```

`card.md`（可选，只写差异，空文件也可以）

无需写 Python 代码。重启后自动加载，`wan-2-0-turbo` 会复用 `SeedanceVideoAdapter`。

---

### 添加新模型（需要自定义 payload 逻辑）

1. 创建目录和 YAML（同上，可不写 `extends`）
2. 创建 Python Adapter：

```python
# src/cfgpu_mcp/adapters/my_model.py
from cfgpu_mcp.adapters.base import ModelAdapter, register_python_adapter
from cfgpu_mcp.tool_registry import GenerateImageInput, NormalizedResult

@register_python_adapter
class MyModelAdapter(ModelAdapter):
    adapter_id = "my-model"   # 必须和 adapter.yaml 中的 adapter_id 一致

    def build_payload(self, req):
        assert isinstance(req, GenerateImageInput)
        return {
            "model": self.cfgpu_model_id,  # 只在这里用 cfgpu_model_id
            "prompt": req.prompt,
            # 自定义映射逻辑...
        }

    def parse_response(self, resp) -> NormalizedResult:
        return NormalizedResult(
            urls=[resp["result"]["url"]],
            expires_at=None,
            task_id=resp.get("id"),
            model_used=resp.get("model"),  # 可为 None；见下方兜底说明
            seed=None,
            usage=resp.get("usage"),  # 原样保留 API 的 usage 对象（计费结构因 API 而异）
        )
```

> **`model_used` 一律回填为 `model_name`**：adapter 的 `parse_response()` 常把 `resp.get("model")`（API 响应里回显的值，其实就是内部 `cfgpu_model_id`）写进 `model_used`，但这个值绝不能直接暴露给调用方。因此 `TaskManager` 在 `create()`（同步）和 `poll()`（异步）中，于 `parse_response()` **之后无条件覆盖**：`result.model_used = adapter.model_name`——不是"缺省才兜底"，而是每次都用公开标识覆盖掉 adapter 可能塞进来的内部 ID。这对 `model="auto"` 尤为关键——调用方唯一能得知 router 实际选中哪个模型的渠道就是 `model_used`，它必须是一个稳定、公开的 `model_name`。

> **`aspect_ratio` 回传**：`aspect_ratio` 是回传给客户端的宽高比元数据，取值**优先用上游响应实际返回的 `ratio`**——部分 API（如 WAN，响应里带 `"ratio": "9:16"`）会回传解析后的真实宽高比，这在请求传 `adaptive` 时尤其有意义。各 adapter 的 `parse_response()` 在响应含 `ratio` 时即填入 `result.aspect_ratio`；仅当响应未回传时，才由 `TaskManager` 兜底为**请求**的 `aspect_ratio`（`if not result.aspect_ratio: ...`——这里是真正的"缺省才兜底"，与上面 `model_used` 的无条件覆盖不同）。
>
> 该请求兜底值并非来自响应，而异步模型的结果要到 `poll()` 才定型、此处已无请求对象，因此 `create()` 把请求的 `aspect_ratio` 暂存进**入库的** payload（保留键 `_requested_aspect_ratio`），`poll()` 再从 `task.payload` 取回。该保留键只进数据库、不会发往上游（POST 用的是干净的 payload），且 payload 仅供内部回读、从不重新提交，因此对上游与客户端均无影响，同时也让 `task_status` 重新轮询时仍能带上正确的宽高比。

3. 在 `adapters/__init__.py` 中导入（触发注册）：

```python
from cfgpu_mcp.adapters import seedance_video, seedream, my_model
```

---

### 添加新工具（新 service 函数）

以添加 `cancel_task` 为例：

**第一步**：在 `tool_registry.py` 新增 Pydantic 输入模型和 `_REGISTRY` 条目：

```python
class CancelTaskInput(BaseModel):
    """Cancel a pending or running task."""
    task_id: str = Field(description="Task ID to cancel")

_REGISTRY.append(("cancel_task", CancelTaskInput))
```

**第二步**：在 `service/task.py` 实现业务逻辑。

**第三步**：在 `agent/dispatcher.py` 的 `match` 分支中添加路由。

**第四步**：在 `tools/tasks.py` 的 `register()` 中添加 FastMCP 包装（Mode A）。

**第五步**：在 `cli/cmd_task.py` 中添加 CLI 命令（Mode C）。

`get_openai_tools()` 和 `get_langgraph_tools()` 会自动从 `_REGISTRY` 读取，无需修改。

---

### 修改工具参数

工具参数定义在**四处**，需同步修改：

1. `tool_registry.py` 中的 Pydantic 模型（Mode B + schema 生成的来源）
2. `tools/*.py` 中的函数签名（Mode A，FastMCP 限制）
3. `service/*.py` 中的函数签名（实际实现）
4. `cli/cmd_*.py` 中的 click options（Mode C）

若该参数还要进入 API 请求体，则需第 5 处：相关 adapter 的 `build_payload()`。

#### 通用参数 vs `model_specific`

跨多数模型、用户高频调整的开关应做成**通用参数**（typed field），而不是埋在 free-form 的 `model_specific` 里。已有两个范例：

- `with_audio`：视频音频开关，`SeedanceVideoAdapter` 映射为 `generate_audio`。
- `watermark`：水印开关。类型为 `Optional[bool]`，**默认 `None` 表示不写入 payload、沿用各模型 API 自身默认**（避免覆盖 Seedream 4.5 的 `false` 等差异化默认）。支持的 adapter（`seedance_video`、`seedream`、`happyhorse`）在 `payload.update(req.model_specific)` **之前**写入 `payload["watermark"]`，因此 `model_specific` 仍可覆盖它；不支持的 adapter（`async_image` 下的 gpt-image-2 / nano-banana）不读取该字段，传入即被忽略。
- `n`：图片组图数量（1-15），默认 1。仅 `SeedreamAdapter` 支持 `n>1`——会自动写入 `sequential_image_generation=auto` + `sequential_image_generation_options.max_images=n`；`async_image`（gpt-image-2 / nano-banana）的 `supports()` 对 `n>1` 直接拒绝。
- `resolution`（视频）：开放 `1080p`，WAN 2.0 / Doubao Seedance 1.5 Pro / HappyHorse 支持（`happyhorse` 在 `build_payload` 中 `.upper()` 成 `1080P`；`happyhorse` 仍拒绝 `480p`）。**例外：WAN 2.0 Fast 文生视频（t2v）不支持 `1080p`，`supports()` 会拒绝（仅 480p/720p；带首帧/参考图视频的 i2v 场景才放行 1080p）；`model="auto"` 命中该组合时会自动回退到完整版 WAN 2.0。**
- `duration_seconds`（视频）：允许 `-1`（智能时长，`SeedanceVideoAdapter` 直接透传）。`SeedanceVideoAdapter.supports()` 对 `doubao-seedance-1-5-pro` 额外限制显式时长 ≤12s；`happyhorse` 拒绝 `-1`。
- **能力校验（视频）**：CFGPU 上游 API 会**按 `content` 数组形态在服务端推导 `task_type`**（如带 `reference_video` → `r2v`），客户端从不传 `task_type`。`SeedanceVideoAdapter.supports()` 据此把场景映射成能力名（首帧+尾帧→`first_last_frame`、仅首帧→`image_to_video`、reference_images/videos/audios→`multi_modal_reference`、纯文本→`text_to_video`），若该能力不在模型 `capabilities` 内则本地直接拒绝（如 `doubao-seedance-1-5-pro` 无 `multi_modal_reference`，传 `reference_videos` 会得到清晰报错，而非上游 `the specified task_type r2v does not support model ...`）。这也让 `model="auto"` 路由跳过不支持该场景的模型。

> 前端 HITL 的参数取值范围以 `tool_param_constraints.json` 描述：按 `工具→模型→args` 列出每个通用参数对应该模型的真实取值范围；`watermark`、`n` 作为通用参数列在支持模型的顶层 args（`n` 仅列在 seedream 系；gpt/nano 均不列），`model_specific.fields` 仅保留模型私有子字段（如 `seed`、`sample_mode`、`response_format` 等）。新增/调整参数时同步该文件。

---

### 调试 MCP 协议

```bash
# 启动 Inspector（需要 Node.js）
npx -y @modelcontextprotocol/inspector --env CFGPU_API_TOKEN=sk-... cfgpu-mcp

# 建议测试顺序：
# 1. initialize      确认握手
# 2. tools/list      确认工具注册（工具名为 generate_image 等原始名称）
# 3. list_models {}  不消耗 API quota，快速验证 registry
# 4. generate_image {"prompt":"test","wait":false}  验证 API 连通性
```

### 记录完整 HTTP 请求（dry-run 模式）

```bash
# 启动时设置 CFGPU_DRY_RUN=1，所有 POST 请求都会在 INFO 日志中打印完整 URL 和 payload，
# 然后照常发送。配合 CFGPU_LOG_LEVEL=INFO 查看输出。
CFGPU_DRY_RUN=1 CFGPU_LOG_LEVEL=INFO cfgpu generate image "test"
# stderr 输出示例：
# INFO cfgpu_mcp.client.cfgpu_client - DRY-RUN POST https://www.cfgpu.com/userapi/v1/v1/images/generations
# {
#   "model": "seedream-v3",
#   "prompt": "test",
#   ...
# }
```

### 记录完整 API 响应（验证 adapter / card.md）

```bash
# 设置 CFGPU_LOG_RESPONSES=1，每次 HTTP 响应（POST 和轮询 GET）的完整响应体
# 都会以缩进 JSON 在 INFO 日志中打印，便于核对 adapter.parse_response 的取值路径
# 和 card.md 里描述的响应结构是否与真实 API 一致。配合 CFGPU_LOG_LEVEL=INFO 查看。
# （未设置该变量时，完整响应仍在 DEBUG 级别记录。）
CFGPU_LOG_RESPONSES=1 CFGPU_LOG_LEVEL=INFO cfgpu generate image "test"
# stderr 输出示例：
# INFO cfgpu_mcp.client.cfgpu_client - CFGPU response [POST https://.../v1/images/generations]:
# {
#   "data": [{"url": "https://cdn.cfgpu.com/img-abc.png"}],
#   "usage": {"total_tokens": 100}
# }
```

### 运行测试

```bash
# 全部单元测试（无需 API Token）
pytest tests/unit/ -q

# 单个文件
pytest tests/unit/test_openai_tools.py -v

# 集成测试（需要真实 Token）
CFGPU_API_TOKEN=sk-... pytest tests/integration/ -v
```

---

## 附录：设计决策记录

**为什么 tools/ 层要重声明所有参数，而不是直接传 Pydantic 模型？**
FastMCP 0.x 通过函数签名内省来生成 JSON Schema，不支持以 Pydantic 模型作为参数类型。未来 FastMCP 版本若支持，tools/ 层可以大幅简化。

**为什么 DB 用 SQLite 而不是内存 dict？**
CLI 的异步工作流：`cfgpu generate video ... --no-wait` 拿到 task_id，进程退出；几分钟后 `cfgpu task wait <task_id>` 需要恢复状态。进程间共享状态必须持久化。SQLite 无需额外服务，满足单机场景。多 agent 并发访问同一文件时，WAL 模式（`PRAGMA journal_mode=WAL`）保证并发读写安全；如需完全隔离，各 agent 在各自 config.yaml 的 `task_db.url` 指向不同的 SQLite 文件。

**为什么 `_merge_extends()` 要在合并后的 dict 里保留 `extends` 字段？**
`_instantiate()` 分两步工作：先用 `adapter_id` 在 `_PYTHON_ADAPTERS` 里查找 Python 类，找不到时沿 `extends` 链逐级向上查找父 ID。如果 `extends` 被清除，variant 模型（如 `wan-2-0-fast`）就找不到对应的 `SeedanceVideoAdapter`，会 fallback 到 `GenericAdapter`，导致视频 payload 构建错误。注意必须遍历整条链（见 5.3），孙级变体如 `nano-banana-pro-premium` 的 Python 类位于祖父 `nano-banana-2` 上。

**为什么 `cfgpu_model_id` 只允许在 `build_payload()` 里出现？**
防止 `cfgpu_model_id` 污染到用户界面或日志。它是 CFGPU API 内部实现细节，会随版本更迭变化，且部分模型的 `cfgpu_model_id` 就是厂商原始 model 名（如 `nano-pro-official`），不适合直接暴露。调用方只需要知道 `model_name`（唯一对外的公开标识，见 §3.1）；`adapter_id` 同样是内部注册表 key，不对外暴露。
