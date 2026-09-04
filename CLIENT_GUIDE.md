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

除 `CFGPU_API_TOKEN` 外，其余配置集中到 **config.yaml**——它是这些字段的唯一来源，每项只在一处设置。从 `config.example.yaml` 复制一份；用 `CFGPU_CONFIG` 指定路径，否则取当前目录的 `config.yaml`。缺文件时全部回退默认值（stdio 零配置可用）。优先级：**config.yaml > 内置默认**。

```yaml
transport: stdio              # stdio | streamable-http
http: {host: 0.0.0.0, port: 8080, stateless: true}   # 仅 streamable-http 用；stateless 必须为 true（多租户 token 隔离），false 会被拒绝启动
cfgpu_api: {base_url: https://www.cfgpu.com/userapi/v1, http_timeout: 120, connect_timeout: 10}  # http_timeout/connect_timeout 非正数（如 0）回退默认值
task_db:
  url: sqlite:///~/.cfgpu/tasks.db        # 或 postgresql://user:pass@host:5432/cfgpu；亦可写 $DATABASE_URL / ${VAR} 从环境变量读取（变量未设置则启动报错）
  pool_min: 1                              # Postgres 连接池（SQLite 忽略）
  pool_max: 10
disabled_models: []           # 模型黑名单；空 / 省略 = 全量加载
disabled_tools: []            # 不注册的 MCP 工具；空 / 省略 = 全量暴露
```

### 可选：接入非 CFGPU 的上游（`providers:`）

绝大多数模型由 CFGPU 平台提供，不需要这一段。个别模型跑在别的地方 —— `cfdream/minimax-h3*`（MiniMax H3 本地权重，由自建 comfy-gateway 提供），以及测试期的 `MiniMax-H3`（挂在 CFGPU 的日常环境上）。

```yaml
providers:
  comfy:
    base_url: https://<gateway-host>/v1
    auth_scheme: raw            # 裸 token，不是 `Bearer <t>`
    token_env: COMFY_GATEWAY_TOKEN
    http_timeout: 60
  cfgpu-daily:                  # CFGPU 日常环境（示例）；目前没有模型用它，可删
    base_url: https://<daily-host>/userapi/v1
    auth_scheme: bearer
    token_env: CFGPU_DAILY_API_TOKEN
    http_timeout: 120
```

CFGPU 的日常环境也要写在这里：它是**另一台主机、另一份凭据**，不是内建的 `cfgpu` provider。因此它和 comfy 一样不读调用方逐请求带来的 `Authorization`，只认自己的 `token_env` —— 多租户部署下，所有调用方共用这一份日常凭据。MiniMax-H3 曾挂在这里，现已改用内建的 `cfgpu`，逐请求令牌对它生效。

配套要在环境（或 `.env`）里放 `COMFY_GATEWAY_TOKEN=...`。

三件要知道的事：

- **没配这一段，那些模型就不存在**：不会出现在 `list_models`，不会出现在工具 schema 的 `model` 枚举，`model="auto"` 也永远选不到它们。这是有意的 —— 注册一个连不上的主机，只会让失败推迟到调用方已经选定模型之后。启动日志会写明是哪个 provider 缺失。
- **`token_env` 是该 provider 凭据的唯一来源**。它不读多租户请求头里的用户 token（那是调用方的 **CFGPU** 凭据，只属于 CFGPU），也不回退 `CFGPU_API_TOKEN`；把 `token_env` 写成 `CFGPU_API_TOKEN` 会在加载配置时直接报错。
- **`cfgpu` 自己不写在这里**，它由上面的 `cfgpu_api:` 段合成。

### 仅读环境变量的两个入口

| 变量 | 说明 |
|------|------|
| `CFGPU_API_TOKEN` | 唯一 secret。stdio / HTTP 未带 `Authorization` 头时的回退 token |
| `CFGPU_CONFIG` | config.yaml 路径（否则取 `./config.yaml`） |
| 各 provider 的 `token_env` | 仅在配了 `providers:` 时需要，例如 `COMFY_GATEWAY_TOKEN`（见上文） |

> `transport` / `http` / `cfgpu_api.*`（base_url、http_timeout、connect_timeout）/ `task_db.*` / `disabled_models` / `disabled_tools` **只在 config.yaml 配置**，不再有对应的环境变量 override，避免多处设置。需要从环境读 DB URL 时，在 `task_db.url` 写 `$DATABASE_URL` / `${VAR}`（见上文）。

### 仅环境变量（无 config.yaml 对应项）

| 变量 | 说明 |
|------|------|
| `CFGPU_LOG_LEVEL` | 日志级别（`DEBUG`/`INFO`/`WARNING`），默认 `WARNING` |
| `CFGPU_DRY_RUN` | 非空时每次 POST 前在 INFO 日志打印 URL 和 payload，然后照常发送 |
| `CFGPU_LOG_RESPONSES` | 非空时每次 HTTP 响应体以缩进 JSON 在 INFO 日志打印，便于核对 adapter / card.md |
| `CFGPU_DOTENV` | 自定义 `.env` 路径，默认 `./.env` |

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

配置后重启 Claude Desktop，即可在对话中使用 `generate_image`、`generate_video`、`generate_audio`、`understand_vision` 等工具。

> **MCP 工具命名**：MCP server 内部名称为 `cfgpu`。Claude Desktop 等 MCP Host 直接以原始名称展示工具（`generate_image` 等）。如果使用 `langchain-mcp-adapters` 之类的第三方 MCP 客户端加载工具，客户端通常会自动拼接 server 名作为前缀，工具名变为 `cfgpu_generate_image`、`cfgpu_generate_video` 等。若需与 Mode B 的 `get_langgraph_tools()` 保持命名一致，建议直接使用 Mode B3，跳过 MCP 协议层。

### 可选：屏蔽部分模型

在 config.yaml 的 `disabled_models` 黑名单里列出**不要加载**的模型 —— 写 `model_name`（工具参数用的那个）即可，`adapter_id` / `cfgpu_model_id` 也认；省略 / 留空 = 全量加载：

```yaml
disabled_models:
  - wan-video-fast
  - doubao-seedream-5-0-lite
```

被禁用的模型不会注册：不出现在 `list_models`，不出现在工具 schema 的 `model` 枚举，`model="auto"` 也永远选不到它。

这里是黑名单而不是白名单：模型只会越来越多，「列出要排除的」在新增模型后依然正确，而白名单会把每个新模型都默默挡住，直到有人想起来去补一笔。

禁用一个被别的模型 `extends:` 的父模型是安全的 —— 变体是从 YAML 继承字段，不是从注册后的 adapter。

> 旧的 `enabled_models` 白名单已被移除。config.yaml 里如果还留着**非空**的 `enabled_models`，启动会直接报错（两者含义相反，静默忽略会把本该关掉的模型全部放出来）；留着空值只是一条警告。

### 可选：裁剪暴露的 MCP 工具

在 config.yaml 的 `disabled_tools` 里列出**不要注册**的工具，例如只做图的部署不必把视频/音频工具塞进每一次模型上下文：

```yaml
disabled_tools:
  - generate_audio
  - understand_vision
```

可用的名字：`generate_image`、`generate_video`、`generate_audio`、`understand_vision`、`task_status`、`task_wait`、`list_models`、`get_model_card`。写错名字会在启动时报错 —— 这个字段的意义就是「某个工具不暴露」，拼错却静默通过，恰好等于没生效。

被禁用的工具是**注销**而非隐藏：客户端硬调它会得到 "unknown tool"。注意 `task_status` / `task_wait` 是异步任务取结果的唯一入口，和 `generate_video` 一起留着才有意义。

该配置只作用于 MCP（Mode A）。Mode B / Mode C 直接调 service 层，用 `get_anthropic_tools(tools=[...])` 自行筛选。

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

在 config.yaml 的 `disabled_models` 黑名单里排除，或在代码中提前初始化 registry：

```python
from cfgpu_mcp.config import get_registry

# 只加载两个模型（影响 auto 路由和 list_models 结果）
get_registry(enabled_models=["wan-2-0-fast", "doubao-seedream-5-0-lite"])

# 或反过来，排除若干模型
get_registry(disabled_models=["wan-2-0-fast"])
```

`enabled_models` 白名单只保留在代码入口（嵌入方常常明确知道自己要哪几个），config.yaml 那侧只有黑名单。两者可以同时给，同一个模型被两边点到时**黑名单赢**。

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
        watermark=False,        # 默认 false；不支持水印字段的模型会忽略
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
    # 查询状态：未完成时返回 {task_id, status} 信封；一旦成功，返回与 generate_*
    # 完全一致的扁平结果（顶层 urls/expires_at/...）；失败则抛出 CFGPUError(task_failed)。
    status = await task_svc.get_status("task-abc123")
    if "urls" in status:
        print(status["urls"])             # 已成功
    else:
        print(status["status"])           # 'pending' | 'running'

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

# 组图（一次生成多张关联图片，1-15 张；doubao-seedream-* 与 wan2.7-image 支持）
cfgpu generate image "四格漫画分镜" --model doubao-seedream-5-0-lite -n 4

# 万相 2.7 图像（同步）— 文生图 / 多图编辑 / 图像集，1K/2K 两档
cfgpu generate image "一间有着精致窗户的花店，漂亮的木质门，摆放着花朵" \
  --model wan2.7-image --aspect-ratio 16:9 --resolution 2K

# 万相 2.7 多图编辑：图片在参数里的顺序就是 prompt 里的「图1」「图2」
cfgpu generate image "把图2的涂鸦喷到图1的车上" \
  --model wan2.7-image \
  --reference-images https://example.com/car.webp \
  --reference-images https://example.com/paint.webp

# 输出完整 JSON（含元数据）
cfgpu generate image "..." --metadata --json
```

### 生成视频

```bash
# 基础用法
cfgpu generate video "waves crashing on a beach" --model wan-2-0-fast

# 指定时长和分辨率（1080p：WAN 2.0 / Seedance 1.5 Pro / HappyHorse 支持；WAN 2.0 Fast 文生视频不支持 1080p；-d -1 = 智能时长，仅 WAN 2.0 / Seedance）
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

# Kling Video O1（可灵 O1）/ Kling V3 Omni（可灵 V3 全能版）— resolution+aspect_ratio 自动映射为像素 size
cfgpu generate video "一只可爱的橘猫在阳光下奔跑，慢镜头，电影质感" \
  --model kling-video-o1 -r 1080p -d 5
cfgpu generate video "..." --model kling-v3-omni -r 1080p -d 5

# Kling — 首帧 / 首尾帧 / 参考图（都进 image_list），参考视频进 video_list（refer_type=feature）
cfgpu generate video "首帧变尾帧" --model kling-video-o1 -r 720p \
  --first-frame https://example.com/start.png \
  --last-frame https://example.com/end.png
cfgpu generate video "跟随参考视频运镜" --model kling-v3-omni -r 1080p \
  --reference-videos https://example.com/ref.mp4
# 视频编辑（把源视频当 base）：统一 Schema 无「编辑 vs 参考」之分，用 model_specific 覆盖 video_list
#   {"video_list": [{"video_url": "https://src.mp4", "refer_type": "base"}]}
#   此时 seconds 不下发，时长跟随源视频
# 不支持 --reference-audios（请求体没有音频输入槽位）；--last-frame 必须与 --first-frame 同时给出

# 万相 2.7（wan-2-7-t2v）— 文生视频，支持电影级分镜叙事；需显式时长（不支持 -1 智能时长）
cfgpu generate video "侦探追查故事：第1个镜头[0-3秒]雨夜街头...第2个镜头[3-6秒]..." \
  --model wan-2-7-t2v -r 720p -d 5

# 万相 2.7（wan-2-7-i2v）— 仅图生视频，必须提供首帧；需显式时长（不支持 -1 智能时长）
cfgpu generate video "一只猫在草地上奔跑" \
  --model wan-2-7-i2v --first-frame https://example.com/cat.jpg -r 720p -d 5

# 万相 2.7（wan-2-7-r2v）— 参考生视频，需 ≥1 个参考视频/图片；提示词可引用「视频1」「图片3」
cfgpu generate video "视频2抱着图片3在咖啡厅弹民谣，视频1笑着看着视频2" \
  --model wan-2-7-r2v \
  --reference-videos https://example.com/role1.mp4 \
  --reference-videos https://example.com/role2.mp4 \
  --reference-images https://example.com/object4.png -r 720p -d 5

# 万相 2.7（wan-2-7-videoedit）— 视频编辑，需 1 个源视频（reference_videos）+ 可选参考图
cfgpu generate video "将视频中女孩的衣服替换为图片中的衣服" \
  --model wan-2-7-videoedit \
  --reference-videos https://example.com/src.mp4 \
  --reference-images https://example.com/clothes.png -r 720p -d 5

# 万相 2.6 — 与 2.7 的 input 形态不同（扁平字段，非 media 数组），但 CLI 用法一致
cfgpu generate video "侦探追查故事，电影级分镜..." --model wan-2-6-t2v -r 720p -d 5
# 2.6 图生视频：首帧 → img_url，可选音频（reference_audios[0]）→ audio_url（如音频驱动 rap）
cfgpu generate video "少年说唱 rap" --model wan-2-6-i2v \
  --first-frame https://example.com/rap.png \
  --reference-audios https://example.com/rap.mp3 -r 720p -d 5
# 2.6 参考生视频：reference_videos/images 合并为扁平 reference_urls
cfgpu generate video "character1在沙发上开心地看电影" --model wan-2-6-r2v \
  --reference-videos https://example.com/vace.mp4 -r 720p -d 5

# Grok Imagine Video（cf-imagine-video / cf-imagine-video-1.5）— 文生/图生短视频，恒定同步输出音频
# 两者 API 形状相同，只差价格档位：cf-imagine-video 更便宜（0.275 / 0.385 元每秒，480P 以上统一价），
# cf-imagine-video-1.5 分三档（0.44 / 0.77 / 1.32 元每秒）
cfgpu generate video "镜头不动，石灯上的蚂蚁正在爬行，背景花草随风轻微晃动" \
  --model cf-imagine-video -r 720p -d 10
cfgpu generate video "..." --model cf-imagine-video-1.5 -r 720p -d 10
# 图生视频：--first-frame 与 --reference-images 都进同一个 refer_images 数组（首帧排第一）
cfgpu generate video "石灯上的蚂蚁正在爬行" --model cf-imagine-video-1.5 \
  --first-frame https://example.com/stone.jpeg -r 720p -d 10
# 不支持 --last-frame / --reference-videos / --reference-audios；需显式时长（不支持 -1）
# --no-audio 不生效（请求体没有声音开关，音频恒定生成）

# 去除水印（watermark 已是一等公民参数，无需走 model_specific）
cfgpu generate video "..." --no-watermark
cfgpu generate image "..." --no-watermark      # gpt-image-2 / nano-banana 不支持，忽略

# 传入其它模型特有参数
cfgpu generate video "..." --model-specific '{"tools": [{"type": "web_search"}]}'
```

> `watermark` 是通用参数（`--watermark/--no-watermark`，service 层 `watermark=True/False`），
> 默认为 `false`。对支持的模型，未显式传入时仍会将 `false` 写入上游 payload；
> `MiniMax-H3` 上它写作 `aigc_watermark`（AIGC 标识水印），同样会显式写入。
> GPT Image / Nano Banana、Grok、Kling 和 `cfdream/minimax-h3*` 等没有 watermark 请求字段的模型会忽略它。

### 生成音频（语音合成 / text-to-speech）

```bash
# 豆包语音合成 2.0（seed-tts-2-0）— 异步模型，speaker 选音色
cfgpu generate audio "明朝开国皇帝朱元璋也称这本书为，万物之根" \
  --model seed-tts-2-0 --voice zh_female_xiaohe_uranus_bigtts

# MiniMax 语音 2.8 HD / Turbo — 同步模型，voice_id 选音色，支持 speed/volume/pitch/emotion
cfgpu generate audio "今天是不是很开心呀(laughs)，当然了！" \
  --model minimax-speech-2-8-hd --voice male-qn-qingse --emotion happy
cfgpu generate audio "更快更省的合成" --model minimax-speech-2-8-turbo --speed 1.2

# 调整输出格式与采样率（MiniMax 还可设 --bitrate）
cfgpu generate audio "..." --format wav --sample-rate 24000

# pronunciation_dict / subtitle_enable 等 MiniMax 专有字段走 --model-specific
cfgpu generate audio "处理危险" --model minimax-speech-2-8-hd \
  --model-specific '{"input": {"pronunciation_dict": {"tone": ["处理/(chu3)(li3)"]}}}'
```

> `seed-tts-2-0` 为异步（提交后轮询 `/voice/tasks/{task_id}`，产物是音频 URL），MiniMax 两款为同步（POST 直接返回结果）。
> **MiniMax 不返回 URL**：音频以十六进制字符串内联在 `output.data.audio`，服务端解码后放进 `inline_media`（见 §返回值格式），`urls` 为空数组。
> `--speed/--volume/--pitch/--emotion` 仅 MiniMax 生效，seed-tts 会忽略；音频链接 24 小时内有效。
> `gpt-image-2`、`nano-banana-2`、`nano-banana-pro` 不支持，传入会被忽略。
> 若仍在 `model_specific` 中显式传 `watermark`，会覆盖通用参数（合并发生在最后）。

> `n`（组图数量）同样是通用参数（`-n`，service 层 `n=`，1-15）。支持 `n>1` 的是带
> `multi_image_group` 能力的模型：`doubao-seedream-*`（自动设置
> `sequential_image_generation=auto` + `max_images=n`）与 `wan2.7-image`（自动设置
> `enable_sequential=true` + `n`，**上限 12**，超出在发请求前被拒）。**例外：
> `doubao-seedream-5-0-pro` 为单图模型，不支持组图，`n>1` 会报错**；`gpt-image-2`、
> `nano-banana-*` 传 `n>1` 也会被拒绝。
> 两家的 `n` 都是**上限而不是张数**：出几张由模型决定，少于 `n` 是正常结果，结果回来之前
> 不要向用户承诺具体张数。`resolution` 现已开放 `1080p`（WAN 2.0 / Seedance 1.5 Pro /
> HappyHorse 支持，HappyHorse 会自动大写为 `1080P`；**WAN 2.0 Fast 文生视频不支持 1080p，仅 480p/720p，
> 带首帧/参考媒体的 i2v 场景才放行**），`duration_seconds=-1` 表示智能时长（仅 WAN 2.0 / Seedance）。
> `duration_seconds` 的 schema 范围是 4–30，但 30 秒**只有 `doubao-seedance-2-5` 支持**；WAN 2.0 /
> Seedance 2.0 系上限 15 秒，Seedance 1.5 Pro 上限 12 秒，超限由对应模型的 `supports()` 在发请求前拒绝。
> 分辨率同理是逐模型的：`doubao-seedance-2-5` / `-2-0-fast` / `-2-0-mini` **只支持 480p/720p**，
> 传 `1080p` 会在发请求前被拒绝（需要 1080p 用 `doubao-seedance-2-0` / `wan-2-0`）。

> **`model="auto"` 现在选谁（图片）**：`balanced` / `fast` 都是 `doubao-seedream-5-0-pro`；
> Pro 被参数排除时（3K/4K、组图 `n>1`、联网搜索这三项它没有）退到 `doubao-seedream-5-0-lite`。
> `best` 是 `cf-image-2`，被排除时退到 `cf-pro`。中文 prompt 仍会额外偏向 Seedream 家族。
> **视频**：`balanced` 是 `MiniMax-H3`，`fast` 是 `doubao-seedance-2-0-fast`，
> `best` 是 `doubao-seedance-2-5`（30 秒单段直出、最多 50 个参考素材、多语种旁白）。
> 三档各有各的落点：日常请求想要的模型，未必是点名要快时想要的那个。被参数排除时
> 才轮到别的模型 —— 例如 480p 或超过 15 秒都不在 `MiniMax-H3` 的能力内，`balanced`
> 会自动退到 `doubao-seedance-2-0-fast`。
> 这些落点由 `adapter.yaml` 的 `default_for` / `auto_priority` / `quality_rank` 声明，
> 不是硬编码的名单，也不再像从前那样由 `adapter_id` 的字母序决定（那会让 auto 恒选
> 族里最老的模型）。真正跑了哪个，永远以返回的 `model_used` 为准。

> `quality_tier`（fast/balanced/best）**不只是 `model="auto"` 的路由偏好**：部分模型的 adapter
> 会把它映射进真实请求，选定模型后依然生效——`cf-image-2` 映射为 API 的 `quality`
> （`fast`→`low`、`balanced`→`medium`、`best`→`high`，默认 `balanced` 即 `medium`），
> 可灵 `kling-video-o1` / `kling-v3-omni` 映射为 `mode`（`best`→`pro`，其余 `std`）。
> 其余模型选定后忽略该参数。需要绕过映射时用 `model_specific`（最后合并，可覆盖）。

> **图片的 `resolution`（1K/2K/3K/4K）与视频不同：不做本地取值校验。** 统一 Schema 的取值集比
> 单个模型宽，超出的值会原样上行、由上游 API 拒绝并回传原始报错。已知差异：`cf-image-2`
> （GPT Image 2）只有 1K/2K/4K 三档，传 `3K` 会被上游拒绝；它的 `aspect_ratio` 也没有 `21:9`。
> 分辨率直接影响计价（`cf-image-2` 三档分别为 0.105 / 0.16 / 0.21 元每张），需要哪档就显式传哪档。

> **Seedream 系列与 `wan2.7-image` 是例外：档位在本地校验，且始终以精确像素上行。** 各家族支持的档位不同
> （`doubao-seedream-5-0-pro`：1K/1.5K/2K；`5-0-lite` 与 `4.5`：2K/3K/4K；`4.0`：1K/2K/3K/4K），
> 超出的档位由 `supports()` 直接拒绝而不是静默降档——`validate_only` 会在 `corrected_args`
> 里给出最接近的可用档位，`model="auto"` 则会绕开不支持该档的模型。adapter 把
> （`resolution`, `aspect_ratio`）查成一个精确的 `宽x高` 送上游，因此 `aspect_ratio` 在
> 每一档都真实生效。注意 Pro 的 1.5K：官方称它与 1K 同价且效果更好，但那个等价只在上行
> **档位名**时成立；发像素后可能按上一档计费。要保 1K 价就传 `1K`。

> `wan2.7-image` 走同一条路，只是像素表是**算出来的**而不是抄来的：官方只公布了每档的总像素
> 预算（1K = 1024x1024，2K = 2048x2048）和全场景区间 [768x768, 2048x2048]，adapter 取满足预算
> 的最大整数倍，宽高比因此是精确值。它只有 1K / 2K 两档（4K 只有 pro 版的文生图有），其余档位
> 由 `supports()` 拒绝、`validate_only` 在 `corrected_args` 里给出最接近的可用档。
> 注意分隔符是 `宽*高` 而不是 Seedream 的 `宽x高`。另有一个后果值得知道：上行档位名时，上游会把
> 输出**按最后一张输入图片的宽高比**缩放，而发像素则由 `aspect_ratio`（默认 `1:1`）说了算——
> 编辑一张 16:9 的图也会得到 1:1。想把画幅交还给模型，用
> `model_specific={"parameters": {"size": "2K"}}` 覆盖回档位名（该模型的 `model_specific`
> 对 `parameters` 做深合并，不会把整个对象替换掉）。

### 视觉理解（图像理解 / 图像推理 / 视频理解）

```bash
# 图像理解 / 推理（可传多张图，模型联合推理）
cfgpu understand "描述这张图片，并指出其中的异常之处" \
  --model qwen-3-6-plus -i https://example.com/a.jpg

# 视频理解（单个公网视频链接）
cfgpu understand "详细描述视频内容，并列出关键事件的时间线" \
  --video https://example.com/clip.mp4

# 纯文本对话（不传媒体）；--system 自定义系统提示，--metadata 额外打印 usage
cfgpu understand "用一句话解释相对论" --system "你是物理学教授" --metadata
```

> `understand_vision` 走 OpenAI 兼容的 `/model/v1/chat/completions`，**同步返回文本结果**（不是媒体文件）。
> stdout 输出回答文本（`message.content`），stderr 输出 id / model；Thinking 模型的推理过程（`message.reasoning_content`）也打到 stderr，`--metadata` 额外打印 usage。
> 与 `generate_*` 不同，understand 结果没有 `urls`，因此不带 `artifact` 标记。

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
| `inline_media` | `list[object]` | ✓（有才出现） | **内联产物**：部分模型不给下载链接，直接把媒体内容返回（目前仅 MiniMax 语音）。每项为 `{data, mime_type, filename}`，`data` 是 base64 编码的文件内容，可直接解码落盘。此时 `urls` 为空数组 —— `inline_media` 就是本次产物本身，因此与 `urls` 同级、**不受 `return_metadata` 影响**；无内联产物的模型不会出现该字段。MCP 下它走 `structuredContent` 侧信道（不进模型上下文），见 §content / structuredContent 拆分 |
| `task_id` | `str \| null` | | 任务 ID；同步模型为 `null` |
| `model_used` | `str \| null` | | 实际使用的模型公开标识（`model_name`，与 `list_models()`/`model` 参数同一套 id 空间；从不是内部的 `cfgpu_model_id`）。`model="auto"` 时尤其有用——可据此得知 router 实际选中的模型 |
| `aspect_ratio` | `str \| null` | | 本次输出的宽高比。**优先取 API 响应实际返回的 `ratio`**（部分模型如 WAN 会回传解析后的真实比例，请求传 `adaptive` 时尤其有用）；API 未回传时兜底为本次请求的 `aspect_ratio`。便于客户端无需保存原始参数即可得知所用宽高比 |
| `seed` | `int \| null` | | 部分模型返回的种子值 |
| `usage` | `object \| null` | | 原样保留 API 返回的 `usage` 对象。不同 API 的计费方式与结构各异（如 `total_tokens` / `totalTokens` / `completionTokens` 等），故不做归一化；API 未回传时为 `null`。**例外：可灵（`kling-video-o1` / `kling-v3-omni`）与 Grok（`cf-imagine-video` / `cf-imagine-video-1.5`）按秒计费但响应里没有 `usage` 对象**，计费数值散落在可灵的顶层 `seconds` / `size`、Grok 的 `data.videoLength` / `data.resolutionName`，故由 adapter 组装为 `{duration, sr, ratio}`（与万相 / HappyHorse 的按秒计费字段同形）—— 详见下方说明 |
| `payload` | `object` | ✓ | **真实发送给该模型专属 API 的请求体**（即 `adapter.build_payload(req)` 的产物，而非通用工具入参）。便于 agent 看到底层 API 实际收到的字段（含 `cfgpu_model_id`、各模型私有字段等）。**始终返回，不受 `return_metadata` 影响**。内部用于异步轮询回显的 `_requested_aspect_ratio` 键不会出现在此 |

未标记"默认返回"的字段需加 `return_metadata=True` / `--metadata` 才会出现。

> **`usage` 与计费**：视频模型分两种计费口径 —— Seedance / 万相 2.0 家族**按 token** 计费，读 `usage.totalTokens`；万相 2.6 / 2.7、HappyHorse、可灵、Grok**按秒**计费且单价随输出分辨率分档，读 `usage.duration`（计费时长，秒）与 `usage.sr`（分辨率短边，如 `1080`）。万相 / HappyHorse 上游直接回传 `usage`，原样透传；可灵与 Grok 的任务响应里没有 `usage`，由各自 adapter 组装出同形的 `{duration, sr, ratio}`，三项都取不到时（如任务尚在排队）`usage` 为 `null`，而不是一个全空的记录：
>
> - **可灵**：从顶层 `"seconds": "5"` / `"size": "1920x1080"` 组装出 `{"duration": 5, "sr": 1080, "ratio": "16:9"}`。`duration` 转为数字（视频编辑任务时长跟随源视频、响应无 `seconds`，则退回 `taskResult.videos[0].duration`），`sr` 取**短边**（分辨率档位按短边划分，竖屏 1080x1920 与横屏 1920x1080 同档），`ratio` 由 `size` 反查得出（可灵只回传像素 `size`，不回传 `ratio`）。
> - **Grok**：从 `data.videoLength` / `data.resolutionName` / `data.aspectRatio` 组装出 `{"duration": 10, "sr": 720, "ratio": "16:9"}`。`duration` 取 `videoLength`（字符串形式转为数字），`sr` 由分辨率档位名解析（`"720p"` → `720`），后两者响应常为 `null`，此时对应项为 `null`（结果顶层的 `aspect_ratio` 另有兜底，退回本次请求值）。

> **视觉理解（`understand_vision`）的返回结构不同**：它返回的是文本而非媒体，沿用 chat-completion 结构 —— 结果顶层为 `id`（响应 id `chatcmpl-...`）、`model`（实际模型）、`message`（assistant 消息：`{role, content}`，回答在 `content`；Thinking 模型额外带 `reasoning_content` 推理过程）、`payload`（真实 API 请求体）。`return_metadata=True` 时追加 `usage`（token 用量）。没有 `urls` / `expires_at` / `task_id`，因此也不带 `artifact` 标记。
>
> ```json
> {
>   "id": "chatcmpl-e08011aa8a004eadbb55a9ca23b76113",
>   "model": "qwen3.6-plus",
>   "message": {
>     "role": "assistant",
>     "content": "The video is a cinematic montage of fantastical creatures...",
>     "reasoning_content": "So, let's analyze this video. First, ..."
>   },
>   "usage": {
>     "prompt_tokens": 7749, "completion_tokens": 795, "total_tokens": 8544,
>     "completion_tokens_details": {"text_tokens": 419, "reasoning_tokens": 376},
>     "prompt_tokens_details": {"text_tokens": 25, "video_tokens": 7724}
>   },
>   "payload": { "model": "qwen3.6-plus", "messages": [ ... ], "stream": false }
> }
> ```

> **`artifact` 标记（仅 Mode A / MCP 工具）**：`generate_image`、`generate_video`、`generate_audio`、`task_status`、`task_wait` 这五个 MCP 工具，当返回结果包含已生成的媒体（非空 `urls` **或** 非空 `inline_media`）时，会在结果顶层追加 `"artifact": true`，便于客户端快速识别"本次结果含可渲染产物"。这些工具成功时都返回同一套扁平结构，无产物的结果（如 `wait=False` 的 pending 响应、轮询中的 running 状态、错误 dict）不带此字段。两种产物形态同权：MiniMax 语音只有 `inline_media`、没有 URL，同样是一次成功的生成。
>
> **`inline_media` 走 structuredContent（仅 Mode A / MCP 工具）**：`generate_audio` / `task_status` / `task_wait` 的返回被拆成 LLM 可见的 `content` 与客户端可见的 `structuredContent`，`inline_media`（连同 `usage` / `payload`）只出现在后者 —— base64 音频数据对模型毫无用处，进上下文只会挤爆窗口。客户端从 `structuredContent.inline_media` 取数据落盘；模型那边靠 `content` 里的 `artifact: true` + `status: "succeeded"` + `note` 就知道生成已完成。Mode B / B2 / B3 / CLI 直连 service 层，不做拆分，`inline_media` 就在结果顶层。

> **`request_id`（调用方关联标识，可选，全模式生效）**：`generate_image` / `generate_video` / `generate_audio` 接受一个可选的 `request_id` 入参，服务端会将其**原样回显**在本次即时响应，以及之后由 `task_status` / `task_wait` 返回的最终 artifact / error 上（有值才出现，不传则响应结构完全不变）。用途——异步流程里 generate 与稍后返回 artifact 的 `task_status` / `task_wait` 分属**不同的 tool_call**，而 `task_id` 要等 POST 返回才有、同步模型更是没有 `task_id`；`request_id` 由调用方在**发起时**自选，从而在整条链路上提供一个稳定、可直接对应的关联键，用来把异步结果 / 失败 join 回原始请求。它只作关联用途，绝不进入上游 API 请求体（`payload` 中不出现）。此回显在 service 层完成，故 MCP、Agent dispatcher、CLI 三种模式一致生效。仅可能异步的 `generate_*` 支持；`understand_vision` 恒同步、单次返回，无关联缺口，故不设此参数。

> **`caption`（产物标签，可选，全模式生效）**：`generate_image` / `generate_video` / `generate_audio` 接受一个可选的 `caption` 入参——一句人类可读的**短标签**（如 `"角色阿雅 第一版"` / `"封面图 v1"`），服务端同样**原样回显**在即时响应，以及之后由 `task_status` / `task_wait` 返回的最终 artifact 上。用途——客户端若自建素材台账（如 DeerFlow / cf-dream 把每个生成产物登记为可用短 id 引用的 **material**），不带标签的条目是无名的，只能在生成之后再花一次工具调用去补名字；把标签放在发起时携带即可省掉这一跳，而它随任务记录存储这一点，使**两段式**（`wait=False` → `task_wait`）也无需客户端自己维护 `task_id → 标签` 的映射。
>
> 三条约束：①它是**标签不是第二个 prompt**——写清主体与版本即可，不要复述生成 prompt；②超过 200 字符会被**截断而非报错**（标签不影响出图，为它失败一整次调用不划算）；③绝不进入上游 API 请求体（`payload` 中不出现），对生成结果零影响。与 `request_id` 的唯一差异在**失败路径**：错误结果带 `request_id`（关联标识正是用来把失败 join 回原请求的），但不带 `caption`——调用失败即没有产物可标注。

> **`label`（产物名字，可选，全模式生效）**：`generate_image` / `generate_video` / `generate_audio` 还接受一个可选的 `label`——产物的**展示名 / 文件名**（如 `"阿雅侧脸.png"` / `"cover_v1.png"`），回显机制与 `caption` 完全相同（即时响应 + 之后的 `task_status` / `task_wait`，存进任务记录故两段式也带得回来）。

> **为什么是两个字段而不是一个**：它们答的是两个读者。`caption` 是写给**模型**看的描述——它落在 agent 每轮重读的素材台账里，用来分辨哪条素材是哪条，所以需要能消歧（"角色阿雅 第一版, 侧脸"）；`label` 是写给**人**看的名字——宿主把它当作用户素材面板里的文件名，所以要短、要稳、不能含文件系统拒收的字符。一个字段兼不了两职：长到能消歧的描述做文件名很糟，短到能显示的文件名消不了歧。两者的**归属**也不同——宿主可以让 agent 随时改写 caption，而 label 是素材的出生名，合并就意味着一次无关的模型调用能改掉用户的文件名。

> 三条要点：①**保持名字的形状**——几个词，不要句子、不要 prompt 原文，长描述放 `caption`；②超过 100 字符**截断而非报错**（比 caption 的 200 短是故意的：这是文件名），服务端只做纯截断，**非法字符 / 后缀保留这类清洗归宿主**——只有它知道自己的存储规则；③两个字段**互不兜底**：只传一个绝不会替你编出另一个，回落策略归宿主（它才知道自己的台账要基名还是要描述）。失败路径与 `caption` 同理：错误结果两者都不带——调用失败即没有产物可命名。

> **建议每次都传 `label`**：不传时宿主只能拿不透明的生成 key 显示产物。但它在 schema 里是**可选**的，且刻意不改成必填——本服务器有多个消费方，新增必填参数对每一个都是破坏性变更，而漏传一次的代价是一整轮失败的调用，为一个装饰性字符串不划算。"要不要强制"是宿主自己的提示词该管的事。

> **`validate_only`（预检，可选，全模式生效）**：`generate_image` / `generate_video` / `generate_audio` / `understand_vision` 接受 `validate_only: bool = false`。置 `true` 时走**完全相同**的解析链路——Pydantic 校验 → 路由（`model="auto"` 在此解析成具体模型）→ `supports()` 逐参数校验 → 构建真实上游 payload——然后**在发出 POST 之前返回**。不创建任务、不生成、不计费、不写任务表。
>
> 返回：`{"validated": true, "model_used": "<具体模型>", "task_type", "is_async", "cost_tier", "speed_tier", "corrected_args": {...}, "payload": {<真实上游请求>}}`（Mode A 下 `payload` 随既有拆分进 `structuredContent`，不进模型上下文；`corrected_args` 留在 `content`，它是要照做的指令）。
>
> 用途——**给需要人工确认的调用方解决顺序问题**：审批必须在计费调用之前呈现，而参数校验只发生在调用内部，于是用户批准了一个几秒后就被拒的请求。重试只花一次模型往返，白批一次卡片花的却是人的注意力，而且下一张卡片会被更不信任地阅读。正确顺序是：`validate_only=true` 预检 → 通过才呈现审批 → 用户批准后不带该参数再调一次。
>
> **这段配方的读者是集成方，不是模型（2026-09-04 补，源于一次现网事故）。** 这个开关属于**调用方**：宿主若自己做预检，就会在提交点无条件赋值，模型写的 `validate_only` 一律作废。此前工具描述里也写着这段编排建议，而真正读描述的是**在选参数的模型**——一个以为审批循环归自己编排的模型于是照做了：它发 `validate_only=true`「先验一下」，并把这句话说给用户，然后拿回一次真实的、计费的、有产物的生成，还把它当成故障报给用户。而用户刚刚批准的那张卡片渲染的正是一次真实生成。
>
> 所以 schema 里那段描述现在只声明**所有权**（这个开关属于调用方 host；host 若接管则你写的值被忽略，调用可能真实执行并计费），编排配方留在这里。**给宿主的两条建议**：①若你自己接管这个开关，就在模型可见的工具描述或提示词里说明它不由模型决定；②接管要**留痕**——把「你写的 `validate_only` 未生效，本次是真实执行」写回那次调用的结果里，否则模型只能把成功的结果读成故障，并说出来。DeerFlow 侧的落地见其 `cfgpu-docs/preflight-middleware.md` §3.2.3 / R4。
>
> **`corrected_args`：正式提交前要改的参数。** 用**工具参数名**表达，调用方直接 `{**原参数, **corrected_args}` 覆盖即可，不需要自己掌握任何 per-model 知识（`payload` 承担不了这个用途：它说的是上游方言 `cfgpu_model_id` / `video_length` / `resolution_name`，得反向翻译）。
>
> 当前它**只在模型选择被委托出去时**含 `model` 一键——即 `model="auto"` 或 `model=["a","b"]`（候选列表本质是"在这个子集里 auto"）。此时路由结果必须钉死：审批卡片上写 `auto` 等于没写，用户无从判断费用与时长；钉死也保证了正式提交跑的就是预检验过的那个模型。**显式指定的 model 一律不改写**，连规范化成 `model_name` 都不做——`adapter_id` / `cfgpu_model_id` / `display_name` 本就能解析到同一个 adapter，改写只会让卡片显示一个调用方没写过的名字。`model_used` 负责**报告**，`corrected_args` 负责**指令**，两个字段各司其职。
>
> 代价要知道：钉死 `model` 换掉了 `auto` 的 failover——预检与提交之间该模型若不可用，原本会自动路由到下一个候选，现在直接硬失败。这是有意的取舍：审批只有在它指名了真正会跑的东西时才有意义，而这种失败是显式且可重试的。
>
> 三条要点：①**失败与真实调用完全一致**——内部抛的是同一个 `CFGPUError`，`error_type` / `card_hint` / 各模型专有的错误回译都相同，所以预检通过即意味着参数被接受。**但异常不越过工具边界**：与本服务器所有工具一样，`tool_error_dict` 把它转成一个*正常返回*的错误字典，调用方拿到的是 `{"error": true, "error_type": ..., "message": ..., "retryable": false, "model_id": ...}` 而**不是**协议级异常，返回里也**不会有 `validated` 键**。判"预检没过"要看 `error is true`，别只看 `validated is false`——只认后者会把每一次被拒的调用当成通过放行；②它校验的是**请求合法**，不保证**生成成功**（远端 5xx、限流、内容审核仍可能发生），预检不是产物预览；③回显 `request_id` 但不回显 `caption` / `label`——与错误路径同理，预检没有产物可标注、可命名。`understand_vision` 恒同步、便宜、可重跑，且未知模型会回退 auto，故不设此参数。
>
> **`understand_vision` 上的它答的是另一个问题**（2026-08-28 加）：视觉理解是同步、便宜、可重跑的，前面没有审批卡片，所以本来不在预检清单里。但**宿主的预检是按 pattern 匹配的、不是按工具挑的**——只要这个工具进了那份清单，`validate_only` 就会作为一个普通入参发过来。而**未声明的入参不会被拒，会被静默丢掉**（FastMCP 的入参模型是普通 pydantic 模型，`extra="ignore"`；`model_dump_one_level` 只遍历已声明字段），于是那次"空跑"是一次真实的、计费的视觉理解调用，答案被丢弃后再跑一次。**从响应上看不出来**：被忽略的预检和成功的预检长得一模一样。所以判据不是"这个工具需不需要预检"，而是"它honor不honor这个开关"——后者从外面看得见，前者看不见。
>
> ⚠️ 与环境变量 `CFGPU_DRY_RUN` 无关且语义相反：后者是"打印请求日志但**照常发送**"的调试开关。

### 等待完成（`wait=True`）

```json
{
  "urls": ["https://cdn.cfgpu.com/..."],
  "expires_at": "2026-05-13T10:00:00Z",
  "payload": {"model": "seedream-v3", "prompt": "...", "size": "2K", "..." : "..."},
  "artifact": true,
  "status": "succeeded",
  "note": "Success. URLs already generated; no further task_status/task_wait polling needed."
}
```

加上 `return_metadata=True` / `--metadata`，返回全部字段：

```json
{
  "urls": ["https://cdn.cfgpu.com/..."],
  "expires_at": "2026-05-13T10:00:00Z",
  "task_id": "task-abc123",
  "model_used": "seedream-v3",
  "aspect_ratio": "16:9",
  "seed": 42,
  "usage": {"total_tokens": 100},
  "payload": {"model": "seedream-v3", "prompt": "...", "size": "2K", "..." : "..."}
}
```

### 不等待（`wait=False` / `--no-wait`）

```json
{
  "task_id": "task-abc123",
  "status": "pending"
}
```

传入 `request_id` 时，即时响应与之后的 `task_status` / `task_wait` 结果都会带上它，供跨 tool_call 关联：

```json
{
  "task_id": "task-abc123",
  "status": "pending",
  "request_id": "gen-用户自选-01"
}
```

### 异步任务查询（`task_status` / `task_wait`）

与 `generate_*` 保持一致：**任务成功后返回上方的扁平结果**（顶层 `urls` / `expires_at` / 元数据，外加 `artifact: true`），不再嵌套在 `result` 里。任务尚未完成时返回信封：

```json
{ "task_id": "task-abc123", "status": "running" }
```

> `task_status` 对**非终态的异步任务**会做一次实时上游轮询再返回，所以反复调用它即可把 `wait=false` 提交的任务驱动到完成（客户端驱动轮询）；`task_wait` 则阻塞轮询直到终态或超时。

任务**失败**时，`task_status` 与 `task_wait` 都返回下方「错误」一节描述的标准 error dict（`error_type: "task_failed"`，并带 `model_id`），两者形状完全一致——不再有 `task_status` 独有的 `{status: "failed", error: "..."}` 信封。

### 怎么判断「这个请求结束了没有」

`generate_*` / `task_status` / `task_wait` 三类工具返回**同一套形状**。按这个顺序判，一次判完：

```
1. result["error"] is True        → 结束了（失败，或请求压根没成立）
2. result["artifact"] is True     → 结束了（成功，urls / inline_media 就在结果里）
3. 否则                            → 没结束，用 result["task_id"] 继续 task_status
```

**`status` 是机器枚举**，取值 `succeeded` / `running` / `pending`（`failed` 走 error 通道，等不到）。给人和模型读的那句话在 `note` 里，不要拿它做控制流。

**`error: true` 一定意味着「这条线到此为止」**：任务还活着的情况绝不会走 error 通道。`generate_*(wait=true)` 等待超时、连续轮询失败放弃、轮询中撞上 token 失效——这三种任务都还在上游跑，返回的都是未终态信封而不是错误：

```json
{
  "task_id": "task-abc123",
  "status": "running",
  "last_error": {
    "error_type": "auth",
    "message": "API Token 无效或已过期，请检查 CFGPU_API_TOKEN。",
    "retryable": false,
    "consecutive_failures": 5,
    "elapsed": 187
  }
}
```

**`last_error` 回答的是「为什么这次没等到结果」，不是「任务怎么了」。** 它出现与否本身就是信号：

- **没有它** = 服务端一路正常观察到最后，任务确实在跑，只是还没做完。继续 `task_status` 即可。
- **有它** = 服务端有一段时间没看见这个任务了，`status: "running"` 只代表**未观测到终态**。先看 `last_error.error_type`：`auth` 要先修凭据再轮询（不修的话继续轮也是白轮，`retryable` 会是 `false`）；`timeout` / `unknown` 直接再调 `task_status`。

`task_status` 也会带 `last_error`（此前只有 `task_wait` 会）：它每次调用会做一次实时重查，那次重查如果撞上**不会自愈**的错误（模型下架、上游持续拒绝的凭据），就带着 `last_error` 返回未终态信封，而不是给你一个干净的 `status: "running"` —— 后者会把你推进一个不会终止的轮询循环。可重试的抖动仍然静默回落到上一次已知状态，形状不变。这种单次重查没有 `elapsed` / `consecutive_failures`（那是「等」的属性），所以那两个键不出现。

这里的 `retryable` 只回答**「再调一次 `task_status` 有没有希望」**，与顶层 error dict 里那个「重发整个请求安不安全」是两回事。

**反过来，上游明确判死的任务不会出现在 `last_error` 里。** 有一类失败是上游用 HTTP 200 回的 —— 状态行说「这次查询成功」，body 里的 `error` 说「任务完了」，例如：

```
The request failed because the output video may be related to copyright restrictions.
```

这种响应现在收敛成标准的终态错误（`error: true`、`error_type: "task_failed"`、`retryable: false`，带 `task_id` 和 `request_id`），`generate_*(wait=true)` / `task_status` / `task_wait` 三处一致。此前它被当成一次「轮询失败」，于是拿到的是 `status: "running"` + `last_error{error_type: "unknown", retryable: true}` —— 一条永远不会再变的任务，却被告知继续轮询。**所以：看到 `last_error` 就按上面那条走（再查一次 / 先修凭据），不必怀疑任务其实已经死了。**

> 两个不在这套契约里的例外：`validate_only` 预检返回 `{validated, model_used, …}`，三个键一个都没有，判据是 `"validated" in result`；`understand_vision` 返回文本，没有 `artifact` / `task_id`，恒同步，终态判据只有「没有 `error`」。

### 错误

**Mode A（MCP）和 Mode B（Agent SDK）**：工具层捕获所有错误，返回结构化 dict，确保 LLM 能读取错误原因：

```json
{
  "error": true,
  "error_type": "invalid_params",
  "message": "请求参数错误：image size must be at least 3686400 pixels 请调用 get_model_card 获取模型 gpt-image-2 的详细参数说明和使用示例。",
  "retryable": false,
  "model_id": "gpt-image-2"
}
```

当 `error_type` 为 `invalid_params`、`model_unavailable` 或 `content_blocked` 时，`message` 会追加 `get_model_card` 提示，`model_id` 字段也会出现在 dict 中。LLM 可直接用 `model_id` 值调用 `get_model_card` 获取该模型的完整参数说明。（`model_id` 即全局唯一的 `model_name`；对外从不暴露 MCP 内部的 `adapter_id` / `cfgpu_model_id`。）

`error_type` 可取值：`auth` | `rate_limit` | `quota_exceeded` | `content_blocked` | `invalid_params` | `model_unavailable` | `task_failed` | `timeout` | `unknown`

> **任务还活着时不会走这个通道。** 等待超时、连续轮询失败放弃、轮询中 token 失效——这三种都返回上一节的未终态信封（带 `task_id`，可能带 `last_error`），不是错误。所以拿到 error dict 就可以认定这条线已经结束，不必再去反推。
>
> 单次轮询请求打不通不会立刻判失败：可重试的错误会被吸收，连续 5 次才放弃。真正的上限是该模型的轮询超时（`poll_config.default_timeout`，H3 是 1500s），与单次 HTTP 超时（`http_timeout`）是两回事。
>
> **`timeout` 分两种，顶层 `phase` 说明是哪一种，别改错配置项。** `phase: "request"` 是连上了但上游没在 `http_timeout` 内答完；`phase: "connect"` 是 DNS / TCP / TLS 没在 `connect_timeout` 内完成，**上游根本没收到请求** —— 这几乎总是部署机器到该上游的网络不通（内网环境、出口策略、DNS），把 `http_timeout` 调大不会有任何作用。两种都带 `original.elapsed`（实测耗时，不是配置值），拿它和两个配置值一比即可确认。
>
> **`outcome_unknown: true` 是唯一一种「不知道成没成」的结果，别盲目重发。** 只出现在**提交请求**（POST）的 `phase: "request"` 超时上：请求已经发出、回应没等到，上游可能已经受理并开始计费，而服务端既没拿到 `task_id` 也没落库，**无法从本服务确认**。这种情况 `retryable` 是 `false`——不是说重发一定失败，而是说重发有重复计费的风险，请先到上游侧确认。相对地，`phase: "connect"` 的超时上游根本没收到，`retryable` 为 `true`，安全重发。

**Mode B service 层直接调用**：service 函数抛出 `CFGPUError`，需自行捕获：

```python
from cfgpu_mcp.errors import CFGPUError

try:
    result = await image_svc.generate_image(...)
except CFGPUError as e:
    print(e.error_type)    # "auth" | "invalid_params" | ...
    print(e.user_message)  # 人类可读的错误描述
    print(e.retryable)     # True 表示重发这个请求是安全的
    print(e.outcome_unknown)  # True 表示上游可能已受理并计费，无法确认，勿盲目重发
    print(e.original)      # 原始 HTTP 响应体
```

**Mode C（CLI）**：错误输出到 stderr，exit code 为 1：

```
Error [auth]: CFGPU_API_TOKEN 未设置，请在环境变量中配置 API Token。
```
