# CFGPU Client Guide

CFGPU 提供三种访问模式，共享同一套 service 层：

| 模式 | 适用场景 |
|------|---------|
| **Mode A — MCP Server** | Claude Desktop、任何 MCP Host、Inspector 调试 |
| **Mode B — Anthropic SDK Direct** | 自建 Agent，直接用 Anthropic SDK 驱动工具调用 |
| **Mode C — CLI** | 命令行脚本、Shell 管道、终端快速测试 |

所有模式都需要设置 API Token（**唯一的 secret，始终走环境变量**）：

```bash
export CFGPU_API_TOKEN=sk-...
```

### 配置：config.yaml

除 `CFGPU_API_TOKEN` 外，其余配置集中到 **config.yaml**。从 `config.example.yaml` 复制一份；用 `CFGPU_CONFIG` 指定路径，否则取当前目录的 `config.yaml`。缺文件时全部回退默认值（stdio 零配置可用）。优先级：**环境变量 override > config.yaml > 内置默认**。

```yaml
transport: stdio              # stdio | streamable-http
http: {host: 0.0.0.0, port: 8080, stateless: true}   # 仅 streamable-http 用；stateless 必须为 true（多租户 token 隔离），false 会被拒绝启动
cfgpu_api: {base_url: https://www.cfgpu.com/userapi/v1, http_timeout: 120, connect_timeout: 10}  # http_timeout/connect_timeout 非正数（如 0）回退默认值
task_db:
  url: sqlite:///~/.cfgpu/tasks.db        # 或 postgresql://user:pass@host:5432/cfgpu；亦可写 $DATABASE_URL / ${VAR} 从环境变量读取（变量未设置则启动报错）
  pool_min: 1                              # Postgres 连接池（SQLite 忽略）
  pool_max: 10
enabled_models: []            # 白名单覆盖；空 / 省略 = 全量加载
```

### 仅读环境变量的两个入口

| 变量 | 说明 |
|------|------|
| `CFGPU_API_TOKEN` | 唯一 secret。stdio / HTTP 未带 `Authorization` 头时的回退 token |
| `CFGPU_CONFIG` | config.yaml 路径（否则取 `./config.yaml`） |

### 其余环境变量（可选 override，正式归宿是 config.yaml）

| 变量 | 对应 config.yaml | 说明 |
|------|------|------|
| `CFGPU_ENABLED_MODELS` | `enabled_models` | 逗号分隔 `adapter_id`，白名单覆盖；缺省全量 |
| `CFGPU_BASE_URL` | `cfgpu_api.base_url` | 覆盖 API 基础 URL |
| `CFGPU_HTTP_TIMEOUT` | `cfgpu_api.http_timeout` | 单次请求总超时秒数，默认 `120` |
| `CFGPU_CONNECT_TIMEOUT` | `cfgpu_api.connect_timeout` | 建立连接超时秒数，默认 `10` |
| `CFGPU_TASK_DB_URL` / `CFGPU_DB_PATH` | `task_db.url` | task 存储 URL；旧 `CFGPU_DB_PATH` 会拼成 `sqlite:///<path>` |
| `CFGPU_TRANSPORT` | `transport` | `stdio` / `streamable-http` |
| `CFGPU_LOG_LEVEL` | — | 日志级别（`DEBUG`/`INFO`/`WARNING`），默认 `WARNING` |
| `CFGPU_DRY_RUN` | — | 非空时每次 POST 前在 INFO 日志打印 URL 和 payload，然后照常发送 |
| `CFGPU_LOG_RESPONSES` | — | 非空时每次 HTTP 响应体以缩进 JSON 在 INFO 日志打印，便于核对 adapter / card.md |
| `CFGPU_DOTENV` | — | 自定义 `.env` 路径，默认 `./.env` |

> **`.env` 自动加载**：启动时若当前目录存在 `.env`（或 `CFGPU_DOTENV` 指定的文件），其中的变量会被自动注入环境——免去每次手动 `export`。已存在的真实环境变量优先，不会被 `.env` 覆盖（`env > .env > config.yaml`）。`.env` 常含密钥（`CFGPU_API_TOKEN`、`$DATABASE_URL` 指向的 DB URL），已在 `.gitignore` 中，切勿提交。

---

## Mode A — MCP Server（stdio）

### 配置 Claude Desktop

在 `~/Library/Application Support/Claude/claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "cfgpu": {
      "command": "cfgpu-mcp",
      "env": {
        "CFGPU_API_TOKEN": "sk-..."
      }
    }
  }
}
```

或者用绝对路径（适合 venv 环境）：

```json
{
  "mcpServers": {
    "cfgpu": {
      "command": "/path/to/venv/bin/cfgpu-mcp",
      "env": {
        "CFGPU_API_TOKEN": "sk-..."
      }
    }
  }
}
```

配置后重启 Claude Desktop，即可在对话中使用 `generate_image`、`generate_video` 等工具。

> **MCP 工具命名**：MCP server 内部名称为 `cfgpu`。Claude Desktop 等 MCP Host 直接以原始名称展示工具（`generate_image` 等）。如果使用 `langchain-mcp-adapters` 之类的第三方 MCP 客户端加载工具，客户端通常会自动拼接 server 名作为前缀，工具名变为 `cfgpu_generate_image`、`cfgpu_generate_video` 等。若需与 Mode B 的 `get_langgraph_tools()` 保持命名一致，建议直接使用 Mode B3，跳过 MCP 协议层。

### 可选：限制加载的模型

```json
{
  "env": {
    "CFGPU_API_TOKEN": "sk-...",
    "CFGPU_ENABLED_MODELS": "wan-2-0-fast,doubao-seedream-5-0-lite"
  }
}
```

### 使用 MCP Inspector 调试

```bash
# 需要 Node.js 环境
npx -y @modelcontextprotocol/inspector --env CFGPU_API_TOKEN=sk-... cfgpu-mcp
```

浏览器打开 `http://localhost:5173`，依次执行：
1. `initialize` — 确认握手成功
2. `tools/list` — 查看所有可用工具
3. 选择工具并填写参数，直接调用

### streamable-http（多租户、可水平扩展）

把 config.yaml 的 `transport` 设为 `streamable-http`，server 改用 HTTP 传输，可放在 LB 后跑多个实例：

```yaml
transport: streamable-http
http: {host: 0.0.0.0, port: 8080, stateless: true}
task_db:
  url: postgresql://user:pass@host:5432/cfgpu   # 多实例必须用共享 DB（Postgres）
```

```bash
pip install -e ".[http]"            # HTTP 传输需要（uvicorn）
pip install -e ".[postgres]"        # Postgres 后端需要（多实例）
CFGPU_CONFIG=./config.yaml cfgpu-mcp # 监听 http://0.0.0.0:8080/mcp
```

**逐请求 token**：HTTP 模式下，每个 MCP 请求用自己的 `Authorization: Bearer <token>` 头携带各自的 CFGPU Token——不再全局共用 `CFGPU_API_TOKEN`。未带头时回退到 `CFGPU_API_TOKEN` 环境变量。

**断点续查**：`wait=false` 提交后立即返回 `task_id`；客户端周期性调 `task_status(task_id)`（每次都带 token），服务端借此实时推进任务。**异步模型**（视频等）即使客户端断开，凭 `task_id` 重连仍可查到结果；**同步模型**（Seedream 图片）结果只在该次响应返回，不可断点续查。

> 多实例水平扩展必须用 Postgres：本地 SQLite 文件无法跨实例共享。单实例 HTTP 用 SQLite 亦可。详见 `docs/streamable/http-mcp-servers.md`。

---

## Mode B — Anthropic SDK Direct（Agent 模式）

不启动 MCP Server，而是将工具 schema 注入 Anthropic API，收到 `tool_use` 后调用 `dispatch_tool()` 路由到 service 层。

### 安装

```bash
pip install cfgpu-mcp anthropic
```

### 完整示例

```python
import asyncio
import anthropic
from cfgpu_mcp.tool_registry import get_anthropic_tools
from cfgpu_mcp.agent.dispatcher import dispatch_tool
from cfgpu_mcp.config import close

client = anthropic.Anthropic()

async def run_agent(user_message: str):
    # 获取所有工具的 schema（Anthropic API 格式）
    tools = get_anthropic_tools()

    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )

        # 追加 assistant 回复
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            # 无工具调用，对话结束
            for block in response.content:
                if hasattr(block, "text"):
                    print(block.text)
            break

        # 处理所有工具调用
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[tool] {block.name}({block.input})")
            result = await dispatch_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    await close()  # 清理 HTTP 客户端和 DB 连接

asyncio.run(run_agent("帮我生成一张熊猫在雪地里的图片"))
```

### 只加载部分工具

```python
# 只加载视频相关工具
tools = get_anthropic_tools(task_types=["video"])

# 只加载指定工具
tools = get_anthropic_tools(tools=["generate_image", "list_models"])
```

### 只加载部分模型

通过 `CFGPU_ENABLED_MODELS` 环境变量，或在代码中提前初始化 registry：

```python
from cfgpu_mcp.config import get_registry

# 只加载两个模型（影响 auto 路由和 list_models 结果）
get_registry(enabled_models=["wan-2-0-fast", "doubao-seedream-5-0-lite"])
```

### 直接调用 service 层（不经过 Agent）

如果不需要 LLM 决策，可直接调用 service 函数：

```python
import asyncio
from cfgpu_mcp.service import image as image_svc, video as video_svc
from cfgpu_mcp.config import close

async def main():
    # 生成图片，等待完成
    result = await image_svc.generate_image(
        prompt="a red panda in the snow",
        model="auto",           # 单个 id "doubao-seedream-5-0-lite"，或候选列表 ["doubao-seedream-5-0-lite", "seedream"] 在范围内选优
        aspect_ratio="16:9",
        resolution="2K",
        quality_tier="balanced",
        wait=True,
        return_metadata=True,
    )
    print(result["urls"])       # ['https://...']
    print(result["model_used"]) # 'seedream-v3'

    # 生成视频，不等待（异步任务）
    task = await video_svc.generate_video(
        prompt="waves crashing on a beach",
        model="wan-2-0-fast",
        duration_seconds=5,
        aspect_ratio="16:9",
        resolution="720p",
        with_audio=True,
        watermark=False,        # None=模型默认；gpt-image-2 / nano-banana 不支持(忽略)
        wait=False,             # 立即返回 task_id
    )
    print(task["task_id"])      # 'task-abc123'

    await close()

asyncio.run(main())
```

### 轮询异步任务

```python
from cfgpu_mcp.service import task as task_svc

async def poll():
    # 查询状态：未完成时返回 {task_id, status[, error]} 信封；
    # 一旦成功，返回与 generate_* 完全一致的扁平结果（顶层 urls/expires_at/...）
    status = await task_svc.get_status("task-abc123")
    if "urls" in status:
        print(status["urls"])             # 已成功
    else:
        print(status["status"])           # 'pending' | 'running' | 'failed'

    # 等待完成（内置指数退避轮询）→ 成功时直接是扁平结果，结构同 generate_*
    result = await task_svc.wait_for_task("task-abc123", timeout=300)
    print(result["urls"])
```

---

## Mode B2 — OpenAI SDK Direct

使用 `get_openai_tools()` 生成 OpenAI 格式 schema，`openai_dispatch_tool()` 处理响应中的工具调用。

### 安装

```bash
pip install cfgpu-mcp openai
```

### 完整示例

```python
import asyncio
import json
from openai import AsyncOpenAI
from cfgpu_mcp.agent.openai_tools import get_openai_tools, openai_dispatch_tool
from cfgpu_mcp.config import close

client = AsyncOpenAI()

async def run_agent(user_message: str):
    tools = get_openai_tools()
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = await client.chat.completions.create(
            model="gpt-4o",
            tools=tools,
            messages=messages,
        )
        msg = response.choices[0].message
        messages.append(msg)

        if msg.tool_calls is None:
            print(msg.content)
            break

        for tc in msg.tool_calls:
            print(f"[tool] {tc.function.name}({tc.function.arguments})")
            # arguments 是 JSON 字符串，openai_dispatch_tool 会自动解析
            result = await openai_dispatch_tool(tc.function.name, tc.function.arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    await close()

asyncio.run(run_agent("帮我生成一张熊猫在雪地里的图片"))
```

### 过滤工具

```python
# 只加载图片相关工具
tools = get_openai_tools(task_types=["image"])

# 指定工具白名单
tools = get_openai_tools(tools=["generate_image", "list_models"])
```

---

## Mode B3 — LangGraph

使用 `get_langgraph_tools()` 返回 `StructuredTool` 列表，`args_schema` 直接复用 `tool_registry.py` 中的 Pydantic 模型，schema 定义保持单一来源。工具名为原始名称（`generate_image` 等），**不含** MCP server 前缀，与 Mode B / Mode B2 保持一致。

### 安装

```bash
pip install cfgpu-mcp langchain-core langgraph langchain-anthropic
# 或使用 OpenAI 作为驱动模型：
pip install cfgpu-mcp langchain-core langgraph langchain-openai
```

### prebuilt ReAct Agent（最简）

```python
import asyncio
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from cfgpu_mcp.agent.langgraph_tools import get_langgraph_tools
from cfgpu_mcp.config import close

tools = get_langgraph_tools()
model = ChatAnthropic(model="claude-opus-4-7")
agent = create_react_agent(model, tools)

async def main():
    result = await agent.ainvoke({
        "messages": [("user", "帮我生成一张熊猫在雪地里的 16:9 图片")]
    })
    print(result["messages"][-1].content)
    await close()

asyncio.run(main())
```

### 过滤工具

```python
# 只加载视频工具
tools = get_langgraph_tools(task_types=["video"])

# 指定工具白名单
tools = get_langgraph_tools(tools=["generate_image", "list_models"])
```

### 自定义 Graph（需要多步控制）

```python
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode

tool_node = ToolNode(tools)

def should_continue(state: MessagesState) -> str:
    return "tools" if state["messages"][-1].tool_calls else END

builder = StateGraph(MessagesState)
builder.add_node("agent", lambda s: {
    "messages": [model.bind_tools(tools).invoke(s["messages"])]
})
builder.add_node("tools", tool_node)
builder.set_entry_point("agent")
builder.add_conditional_edges("agent", should_continue)
builder.add_edge("tools", "agent")

graph = builder.compile()
```

> **注意**：LangGraph 的 `ToolNode` 自动以 `await` 调用 async 函数，无需手动处理。资源清理仍需在程序退出时调用 `await close()`。

---

## Mode C — CLI

### 安装

```bash
pip install "cfgpu-mcp[cli]"
```

### 生成图片

```bash
# 基础用法（自动选模型，等待完成，URL 输出到 stdout）
cfgpu generate image "a red panda in the snow"

# 指定模型和参数（同步模型 — Seedream 系列）
cfgpu generate image "富士山日出" \
  --model doubao-seedream-5-0-lite \
  --aspect-ratio 16:9 \
  --resolution 2K \
  --quality-tier best

# 异步模型 — GPT Image 2 / Nano Banana（轮询返回结果）
cfgpu generate image "cyberpunk cityscape" \
  --model gpt-image-2 \
  --aspect-ratio 16:9

cfgpu generate image "watercolor landscape" \
  --model nano-banana-2 \
  --resolution 2K

# 使用参考图
cfgpu generate image "same style portrait" \
  --reference-images https://example.com/ref1.jpg \
  --reference-images https://example.com/ref2.jpg

# 组图（一次生成多张关联图片，仅 doubao-seedream-* 支持，1-15 张）
cfgpu generate image "四格漫画分镜" --model doubao-seedream-5-0-lite -n 4

# 输出完整 JSON（含元数据）
cfgpu generate image "..." --metadata --json
```

### 生成视频

```bash
# 基础用法
cfgpu generate video "waves crashing on a beach" --model wan-2-0-fast

# 指定时长和分辨率（所有视频模型均支持 1080p；-d -1 = 智能时长，仅 WAN 2.0 / Seedance）
cfgpu generate video "..." -d 8 -r 1080p --no-audio

# 图生视频（指定首帧）
cfgpu generate video "zoom out slowly" \
  --first-frame https://example.com/frame.jpg

# 首帧 + 尾帧
cfgpu generate video "transition between scenes" \
  --first-frame https://example.com/start.jpg \
  --last-frame  https://example.com/end.jpg

# 多模态参考（视频 + 音频）
cfgpu generate video "..." \
  --reference-videos https://example.com/ref.mp4 \
  --reference-audios https://example.com/bgm.mp3

# HappyHorse — 多参考图生视频（multi_modal_reference）
cfgpu generate video "身着旗袍的女性，低角度仰拍" \
  --model happyhorse-1-0-t2v \
  --reference-images https://example.com/ref1.jpg \
  --reference-images https://example.com/ref2.jpg

# 去除水印（watermark 已是一等公民参数，无需走 model_specific）
cfgpu generate video "..." --no-watermark
cfgpu generate image "..." --no-watermark      # gpt-image-2 / nano-banana 不支持，忽略

# 传入其它模型特有参数
cfgpu generate video "..." --model-specific '{"tools": [{"type": "web_search"}]}'
```

> `watermark` 现已提升为通用参数（`--watermark/--no-watermark`，service 层 `watermark=True/False`）。
> 不传时（`None`）沿用各模型自身的默认值（多数为开启，Seedream 4.5 为关闭）。
> `gpt-image-2`、`nano-banana-2`、`nano-banana-pro` 不支持，传入会被忽略。
> 若仍在 `model_specific` 中显式传 `watermark`，会覆盖通用参数（合并发生在最后）。

> `n`（组图数量）同样是通用参数（`-n`，service 层 `n=`，1-15）。仅 `doubao-seedream-*`
> 支持 `n>1`（自动设置 `sequential_image_generation=auto` + `max_images=n`）；`gpt-image-2`、
> `nano-banana-*` 传 `n>1` 会被拒绝。`resolution` 现已开放 `1080p`（全部视频模型支持，
> HappyHorse 会自动大写为 `1080P`），`duration_seconds=-1` 表示智能时长（仅 WAN 2.0 / Seedance）。

### 异步工作流（--no-wait）

```bash
# 立即返回 task_id（不等待）
TASK=$(cfgpu generate video "..." --no-wait)
echo "task: $TASK"

# 查询状态
cfgpu task status $TASK

# 等待完成（阻塞，带进度显示）
cfgpu task wait $TASK --timeout 600
```

### 查看模型

```bash
# 列出所有模型（表格）
cfgpu models list

# 只看视频模型
cfgpu models list --task-type video

# 输出 JSON（含所有字段）
cfgpu models list --json

# 查看某个模型的详细说明（markdown）
cfgpu models card wan-2-0-fast
cfgpu models card doubao-seedream-5-0-lite
```

### Pipe 友好

stdout 只输出 URL，stderr 输出进度和元数据：

```bash
# 直接用 open 打开生成的图片（macOS）
cfgpu generate image "a red panda" | xargs open

# 下载到本地
cfgpu generate image "..." | xargs -I{} curl -o output.jpg {}

# 批量生成
for prompt in "sunrise" "sunset" "noon"; do
  cfgpu generate image "$prompt" >> urls.txt
done
```

---

## 返回值格式

所有模型（图片/视频、同步/异步）的生成结果都经过 `NormalizedResult` 统一化，无论底层 API 响应格式如何，最终返回结构一致。

| 字段 | 类型 | 默认返回 | 说明 |
|------|------|:--------:|------|
| `urls` | `list[str]` | ✓ | 生成的资源 URL 列表 |
| `expires_at` | `str \| null` | ✓ | URL 过期时间（ISO 8601），通常 24 小时后失效 |
| `task_id` | `str \| null` | | 任务 ID；同步模型为 `null` |
| `model_used` | `str \| null` | | 实际使用的模型标识符。优先取 API 返回的 `model` 字段；若 API 未回传，则兜底为所选 adapter 的 `cfgpu_model_id`。`model="auto"` 时尤其有用——可据此得知 router 实际选中的模型 |
| `seed` | `int \| null` | | 部分模型返回的种子值 |
| `cost_tokens` | `int \| null` | | 部分模型返回的消耗 token 数 |

未标记"默认返回"的字段需加 `return_metadata=True` / `--metadata` 才会出现。

> **`artifact` 标记（仅 Mode A / MCP 工具）**：`generate_image`、`generate_video`、`task_status`、`task_wait` 这四个 MCP 工具，当返回结果包含已生成的媒体（非空 `urls`）时，会在结果顶层追加 `"artifact": true`，便于客户端快速识别"本次结果含可渲染产物"。四个工具成功时都返回同一套扁平结构（顶层 `urls`），无 URL 的结果（如 `wait=False` 的 pending 响应、轮询中的 running 状态、错误 dict）不带此字段。

### 等待完成（`wait=True`）

```json
{
  "urls": ["https://cdn.cfgpu.com/..."],
  "expires_at": "2026-05-13T10:00:00Z",
  "artifact": true
}
```

加上 `return_metadata=True` / `--metadata`，返回全部字段：

```json
{
  "urls": ["https://cdn.cfgpu.com/..."],
  "expires_at": "2026-05-13T10:00:00Z",
  "task_id": "task-abc123",
  "model_used": "seedream-v3",
  "seed": 42,
  "cost_tokens": 100
}
```

### 不等待（`wait=False` / `--no-wait`）

```json
{
  "task_id": "task-abc123",
  "status": "pending"
}
```

### 异步任务查询（`task_status` / `task_wait`）

与 `generate_*` 保持一致：**任务成功后返回上方的扁平结果**（顶层 `urls` / `expires_at` / 元数据，外加 `artifact: true`），不再嵌套在 `result` 里。任务尚未完成时返回信封：

```json
{ "task_id": "task-abc123", "status": "running" }
```

> `task_status` 对**非终态的异步任务**会做一次实时上游轮询再返回，所以反复调用它即可把 `wait=false` 提交的任务驱动到完成（客户端驱动轮询）；`task_wait` 则阻塞轮询直到终态或超时。

失败时信封带 `error`（`task_wait` 失败则抛出 / 返回 error dict）：

```json
{ "task_id": "task-abc123", "status": "failed", "error": "..." }
```

### 错误

**Mode A（MCP）和 Mode B（Agent SDK）**：工具层捕获所有错误，返回结构化 dict，确保 LLM 能读取错误原因：

```json
{
  "error": true,
  "error_type": "invalid_params",
  "message": "请求参数错误：image size must be at least 3686400 pixels 请调用 get_model_card 获取模型 gpt-image-2 的详细参数说明和使用示例。",
  "retryable": false,
  "adapter_id": "gpt-image-2"
}
```

当 `error_type` 为 `invalid_params`、`model_unavailable` 或 `content_blocked` 时，`message` 会追加 `get_model_card` 提示，`adapter_id` 字段也会出现在 dict 中。LLM 可直接用 `adapter_id` 值调用 `get_model_card` 获取该模型的完整参数说明。

`error_type` 可取值：`auth` | `rate_limit` | `quota_exceeded` | `content_blocked` | `invalid_params` | `model_unavailable` | `task_failed` | `timeout` | `unknown`

**Mode B service 层直接调用**：service 函数抛出 `CFGPUError`，需自行捕获：

```python
from cfgpu_mcp.errors import CFGPUError

try:
    result = await image_svc.generate_image(...)
except CFGPUError as e:
    print(e.error_type)    # "auth" | "invalid_params" | ...
    print(e.user_message)  # 人类可读的错误描述
    print(e.retryable)     # True 表示可重试
    print(e.original)      # 原始 HTTP 响应体
```

**Mode C（CLI）**：错误输出到 stderr，exit code 为 1：

```
Error [auth]: CFGPU_API_TOKEN 未设置，请在环境变量中配置 API Token。
```
