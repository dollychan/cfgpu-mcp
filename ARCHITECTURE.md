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

### 3.1 三种模型标识符

每个模型有三个 ID，**绝对不能混用**：

| 名称 | 示例 | 用途 |
|------|------|------|
| `adapter_id` | `wan-2-0-fast` | 目录名、registry key、用户传入的 `model=` 参数、日志 |
| `display_name` | `WAN 2.0 Fast (...)` | 仅 `list_models()` 返回值中展示 |
| `cfgpu_model_id` | `wan-video-fast` | **仅在** `build_payload()` 里写入 API 请求体 |

新开发者最常见的错误：在 `build_payload()` 以外的地方使用 `cfgpu_model_id`，或者把 `adapter_id` 传入 API。

### 3.2 同步模型 vs 异步模型

通过 `adapter.yaml` 中的 `is_async` 字段区分：

| | 同步（`is_async: false`） | 异步（`is_async: true`） |
|-|--------------------------|--------------------------|
| 代表模型 | Seedream（图片） | WAN 2.0（视频）、GPT Image 2、Nano Banana（图片） |
| POST 响应 | 直接包含图片 URL | 包含 `task_id`，需轮询 |
| `TaskManager.create()` | 立即写 `succeeded` 到 DB | 写 `pending`，等待轮询 |
| `TaskManager.wait()` | 立即返回（no-op） | 指数退避轮询直到完成 |
| 典型耗时 | 2–5 秒 | 30–600 秒 |

两类模型对调用方（service 层）完全透明，`TaskManager` 内部已处理差异。

### 3.3 `model="auto"` 的路由逻辑

`ModelRouter.select_model()` 对所有候选模型打分，最高分获选：

```
基础分（quality_tier）:
  fast   → speed_tier × 2 - cost_tier
  best   → +5（如有 best_quality 能力）+ speed_tier - cost_tier
  balanced → speed_tier - cost_tier

加分项:
  请求有 reference_images/videos/audios 且模型支持 → +3
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
    ├─ ModelRouter.get_adapter(model)   model 非 "auto"
    │   或
    │  ModelRouter.select_model(req)    model == "auto"
    │      └─ adapter.supports(req) + _score(adapter, req)
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
    └─ [is_async=true]  CFGPUClient.post() → 取 task_id → DB 写 pending
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

`get_registry()` / `get_client()` / `get_db()` 均为模块级单例，首次调用时初始化，后续调用直接返回已有实例。这避免了每次请求重新建立 HTTP 连接或重新解析 YAML。

程序退出时必须调用 `await config.close()`，以关闭 `aiohttp.ClientSession` 和 `aiosqlite.Connection`。各访问层（CLI 的 `_run()`、MCP server 的 `atexit`）各自负责调用。

---

## 5. 模型系统

### 5.1 Adapter 类层次

```
ModelAdapter (ABC, adapters/base.py)
    │
    ├── GenericAdapter          YAML 驱动，适合 payload 结构简单的模型
    │       └── 通过 payload_mapping DSL 把 req 字段映射到 API 字段
    │
    ├── _AsyncImageBase         共享 data-wrapped 响应处理 + _finalize_payload()
    │       ├── GptImage2Adapter      gpt-image-2
    │       └── NanoBananaAdapter     nano-banana-2 / nano-banana-pro（extends 链）
    │
    ├── WanVideoAdapter         手写 Python，处理复杂 content 数组构建
    │       └── 同时服务 wan-2-0 和 wan-2-0-fast（通过 extends 链）
    │
    └── SeedreamAdapter         手写 Python，处理 resolution×ratio → size 映射
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
# adapters/wan_video.py
@register_python_adapter        # 把 WanVideoAdapter 注册到 _PYTHON_ADAPTERS["wan-2-0"]
class WanVideoAdapter(ModelAdapter):
    adapter_id = "wan-2-0"
    ...
```

```python
# adapters/__init__.py
from cfgpu_mcp.adapters import wan_video, seedream, async_image  # 触发 @register_python_adapter
```

`_instantiate()` 的查找顺序：
1. 在 `_PYTHON_ADAPTERS` 中查找 `adapter_id`（e.g. `wan-2-0-fast`）→ 未找到
2. 查找 `extends` 指向的父 ID（`wan-2-0`）→ 找到 `WanVideoAdapter`
3. 用 `WanVideoAdapter.from_config(merged_config)` 实例化，此时实例的 `adapter_id`、`cfgpu_model_id` 等已被 merged config 覆盖

这就是 `wan-2-0-fast` 如何复用 `WanVideoAdapter` 的全部逻辑，不需要 `wan_video_fast.py`。

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

### DB schema（SQLite）

```sql
tasks (
    id          TEXT PRIMARY KEY,   -- CFGPU 返回的 task_id（异步）或 uuid4（同步）
    adapter_id  TEXT,               -- 用于 task_wait 时重建 adapter
    status      TEXT,               -- pending | running | succeeded | failed
    payload     TEXT,               -- JSON，原始 API 请求体
    result      TEXT,               -- JSON，NormalizedResult.to_dict()
    error       TEXT,               -- 失败原因
    created_at  REAL,
    updated_at  REAL
)
```

DB 的作用：`cfgpu task status <task_id>` 和 `cfgpu task wait <task_id>` 需要在进程重启后仍能查询和恢复任务。如果只在内存中存储，CLI 的异步工作流（`--no-wait` 后稍后查询）就无法工作。

`service/task.py` 的 `get_status()` 在返回已成功但 result 中无 URL 的任务时，会尝试重新轮询 API 获取最新结果。轮询失败时以 `logger.debug()` 记录，不会阻断返回——此时返回 DB 中的 stale result。

### 指数退避轮询

`_STATUS_MAP`（模块级常量）将 CFGPU API 返回的原始状态映射到内部状态（`succeeded` / `failed` / `running` / `pending`），避免每次 `poll()` 调用重建 dict。

```python
interval = base_interval                        # 默认 5s
while not done:
    await asyncio.sleep(interval)
    interval = min(interval * backoff_factor, max_interval)  # 最长 20s
```

每个模型的轮询参数在 `adapter.yaml` 的 `poll_config` 中配置，`WanVideoAdapter` 还根据请求参数（时长、是否有参考媒体）动态延长 `estimate_poll_timeout()`。

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

### card.md 提示机制

当错误属于 `invalid_params`、`model_unavailable` 或 `content_blocked` 类型时，`service/image.py` 和 `service/video.py` 会把 `adapter.adapter_id` 写入 `CFGPUError.adapter_id`。`to_tool_result_dict()` 在 `message` 中追加提示：`"请调用 get_model_card 获取模型 {adapter_id} 的详细参数说明和使用示例。"`, 同时在 dict 中添加 `adapter_id` 字段，方便 LLM 直接用该值调用 `get_model_card`。其他错误类型（`auth`、`rate_limit`、`timeout` 等）不追加提示。

### 错误在各层的展示方式

| 层 | 展示方式 |
|----|---------|
| MCP tools（`tools/`） | 工具内部 try/except → 返回 `{"error": True, "error_type": ..., "message": ..., "retryable": ..., "adapter_id": ...}` dict，LLM 可直接读取 |
| agent/dispatcher | `dispatch_tool()` 内部 try/except → 返回同上 error dict（`ValueError` 除外，编程错误继续上抛）|
| CLI | `print_error()` 打印到 stderr，`sys.exit(1)` |

**为什么 MCP tools 不依赖 FastMCP 的异常捕获？**  
FastMCP 捕获异常后设置 `isError: true`，但 MCP 客户端是否将其内容暴露给 LLM 取决于具体实现，行为不一致。主动返回 error dict 可确保错误消息始终出现在 tool result 内容中，LLM 一定能看到并推理。

`tool_error_dict(e)` 定义在 `errors.py`，`tools/` 层和 `dispatcher.py` 均通过 import 共用它。

---

## 8. 文件结构速查

```
src/cfgpu_mcp/
│
├── server.py                   Mode A 入口，注册工具，启动 stdio
├── config.py                   单例资源管理（registry / client / db）
├── tool_registry.py            Pydantic 输入模型 + get_anthropic_tools() + NormalizedResult
├── router.py                   model="auto" 评分选模型
├── task_manager.py             同步/异步任务创建、轮询、等待
├── errors.py                   CFGPUError，HTTP 状态 → error_type 映射
│
├── adapters/
│   ├── base.py                 ModelAdapter ABC + @register_python_adapter + PollConfig
│   ├── registry.py             YAML 加载、extends 合并、Python 类解析
│   ├── generic.py              YAML DSL 驱动的通用 adapter
│   ├── wan_video.py            WAN 2.0 / WAN 2.0 Fast 的 Python Adapter
│   ├── seedream.py             Seedream 的 Python Adapter（同步模型）
│   ├── async_image.py          _AsyncImageBase + GptImage2 / NanoBanana Adapter
│   └── __init__.py             导入 wan_video、seedream、async_image 触发注册
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
│   ├── doubao-seedream-5-0-lite/
│   │   ├── adapter.yaml
│   │   └── card.md
│   ├── doubao-seedream-4-5/
│   │   ├── adapter.yaml        extends: doubao-seedream-5-0-lite, card_base: ~
│   │   └── card.md
│   └── doubao-seedream-4-0/
│       ├── adapter.yaml        extends: doubao-seedream-5-0-lite, card_base: ~
│       └── card.md
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
│   ├── preview.py              preview_generate_image() / preview_generate_video()（dry-run）
│   ├── task.py                 get_status() / wait_for_task()
│   └── model.py                list_models() / get_model_card()
│
├── tools/                      Mode A：FastMCP 工具注册（参数重声明层）
│   ├── generate.py
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
│   ├── cmd_generate.py         cfgpu generate image/video
│   ├── cmd_task.py             cfgpu task status/wait
│   ├── cmd_models.py           cfgpu models list/card
│   └── output.py               print_result() / run_with_progress() / print_error()
│
└── client/
    ├── cfgpu_client.py         aiohttp HTTP 客户端（最底层）
    └── db.py                   SQLite CRUD（insert/update/get/list）
```

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
cost_tier: 4
speed_tier: 5
poll_config:
  default_timeout: 200
```

`card.md`（可选，只写差异，空文件也可以）

无需写 Python 代码。重启后自动加载，`wan-2-0-turbo` 会复用 `WanVideoAdapter`。

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
            model_used=resp.get("model"),
            seed=None,
            cost_tokens=None,
        )
```

3. 在 `adapters/__init__.py` 中导入（触发注册）：

```python
from cfgpu_mcp.adapters import wan_video, seedream, my_model
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

### Preview（dry-run）工具模式

`preview_generate_image` / `preview_generate_video` 是"只解析、不调用"的 dry-run 工具。它们：

1. 接受与真实工具**完全相同的参数**（schema 通过继承 `GenerateImageInput` / `GenerateVideoInput` 自动共享）
2. 走完整的路由和 `build_payload()` 流程
3. 不调用 `CFGPUClient`，直接返回摘要

```python
# service/preview.py 中的共享辅助函数
def _resolve_adapter(req, model) -> ModelAdapter:
    ...  # 复用 ModelRouter 逻辑

def _build_preview(adapter, req) -> dict:
    return {
        "dry_run": True,
        "model": adapter.adapter_id,
        "display_name": adapter.display_name,
        "cost_tier": adapter.cost_tier,
        "speed_tier": adapter.speed_tier,
        "is_async": adapter.is_async,
        "estimated_seconds": adapter.estimate_poll_timeout(req),
        "payload": adapter.build_payload(req),  # 真实的 API payload
    }
```

扩展时遵循同一模式：新 Pydantic 类继承现有输入模型（只改 `__doc__`），对应的 service 函数调用 `_build_preview`。

---

### 修改工具参数

工具参数定义在**两处**，需同步修改：

1. `tool_registry.py` 中的 Pydantic 模型（Mode B + schema 生成的来源）
2. `tools/*.py` 中的函数签名（Mode A，FastMCP 限制）
3. `service/*.py` 中的函数签名（实际实现）
4. `cli/cmd_*.py` 中的 click options（Mode C）

---

### 调试 MCP 协议

```bash
# 启动 Inspector（需要 Node.js）
npx -y @modelcontextprotocol/inspector --env CFGPU_API_TOKEN=sk-... cfgpu-mcp

# 建议测试顺序：
# 1. initialize      确认握手
# 2. tools/list      确认工具注册
# 3. list_models {}  不消耗 API quota，快速验证 registry
# 4. generate_image {"prompt":"test","wait":false}  验证 API 连通性
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
CLI 的异步工作流：`cfgpu generate video ... --no-wait` 拿到 task_id，进程退出；几分钟后 `cfgpu task wait <task_id>` 需要恢复状态。进程间共享状态必须持久化。SQLite 无需额外服务，满足单机场景。

**为什么 `_merge_extends()` 要在合并后的 dict 里保留 `extends` 字段？**
`_instantiate()` 分两步工作：先用 `adapter_id` 在 `_PYTHON_ADAPTERS` 里查找 Python 类，找不到时退而查 `extends` 指向的父 ID。如果 `extends` 被清除，variant 模型（如 `wan-2-0-fast`）就找不到对应的 `WanVideoAdapter`，会 fallback 到 `GenericAdapter`，导致视频 payload 构建错误。

**为什么 `cfgpu_model_id` 只允许在 `build_payload()` 里出现？**
防止 `cfgpu_model_id` 污染到用户界面或日志。用户只需要知道 `adapter_id`（人类可读、稳定），`cfgpu_model_id` 是 CFGPU API 内部实现细节，会随版本更迭变化。
