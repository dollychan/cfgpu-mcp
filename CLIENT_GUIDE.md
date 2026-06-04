# CFGPU Client Guide

CFGPU 提供三种访问模式，共享同一套 service 层：

| 模式 | 适用场景 |
|------|---------|
| **Mode A — MCP Server** | Claude Desktop、任何 MCP Host、Inspector 调试 |
| **Mode B — Anthropic SDK Direct** | 自建 Agent，直接用 Anthropic SDK 驱动工具调用 |
| **Mode C — CLI** | 命令行脚本、Shell 管道、终端快速测试 |

所有模式都需要设置环境变量：

```bash
export CFGPU_API_TOKEN=sk-...
```

### 可选环境变量

| 变量 | 说明 |
|------|------|
| `CFGPU_ENABLED_MODELS` | 逗号分隔的 `adapter_id` 列表，限制加载的模型；缺省加载全部 |
| `CFGPU_BASE_URL` | 覆盖 API 基础 URL |
| `CFGPU_DB_PATH` | SQLite 路径，默认 `~/.cfgpu/tasks.db`。多个 agent 同时通过 stdio 启动 MCP server 时共享同一文件（WAL 模式保证并发安全）；如需进程间完全隔离，为每个 agent 设置不同路径 |
| `CFGPU_LOG_LEVEL` | 日志级别（`DEBUG` / `INFO` / `WARNING`），默认 `WARNING` |
| `CFGPU_DRY_RUN` | 设为任意非空值时，每次 POST 请求前在 INFO 日志中打印完整 URL 和 payload，然后照常发送 |

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
    # 查询状态
    status = await task_svc.get_status("task-abc123")
    print(status["status"])   # 'pending' | 'succeeded' | 'failed'

    # 等待完成（内置指数退避轮询）
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

# 输出完整 JSON（含元数据）
cfgpu generate image "..." --metadata --json
```

### 生成视频

```bash
# 基础用法
cfgpu generate video "waves crashing on a beach" --model wan-2-0-fast

# 指定时长和分辨率
cfgpu generate video "..." -d 8 -r 480p --no-audio

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
| `model_used` | `str \| null` | | API 返回的实际模型标识符 |
| `seed` | `int \| null` | | 部分模型返回的种子值 |
| `cost_tokens` | `int \| null` | | 部分模型返回的消耗 token 数 |

未标记"默认返回"的字段需加 `return_metadata=True` / `--metadata` 才会出现。

### 等待完成（`wait=True`）

```json
{
  "urls": ["https://cdn.cfgpu.com/..."],
  "expires_at": "2026-05-13T10:00:00Z"
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
