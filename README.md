# cfgpu-mcp

[![PyPI](https://img.shields.io/pypi/v/cfgpu-mcp.svg)](https://pypi.org/project/cfgpu-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/cfgpu-mcp.svg)](https://pypi.org/project/cfgpu-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的图像 / 视频 / 音频生成与视觉理解 server，统一封装 CFGPU 平台上的多家文生图、文生视频、语音合成、视觉理解模型。让 Claude、Cursor 等 MCP 客户端能直接调用 `generate_image` / `generate_video` / `generate_audio` / `understand_vision` 等工具，也提供独立 CLI 和多 SDK（Anthropic / OpenAI / LangGraph）适配。

## 功能特性

- **统一接口**：一套 `generate_image` / `generate_video` / `generate_audio` / `understand_vision` 工具覆盖所有模型，自动处理同步 / 异步任务、轮询与重试。`understand_vision` 用于图像理解、图像推理、视频理解，返回文本结果。
- **自动选型**：`model="auto"` 按质量档位、参考媒体、中文 prompt 等打分选最优；也可传单个 `adapter_id` 精确指定，或传候选列表在范围内选优。
- **三种部署模式**：MCP stdio server、Anthropic/OpenAI/LangGraph SDK 工具、独立 CLI，共享同一服务层。
- **可扩展**：新增模型只需添加 `adapter.yaml` + `card.md`，必要时挂一个 Python adapter。

## 支持的模型

| 类型 | adapter_id | 名称 |
|---|---|---|
| 图像 | `doubao-seedream-4-0` | Doubao Seedream 4.0 |
| 图像 | `doubao-seedream-4-5` | Doubao Seedream 4.5 |
| 图像 | `doubao-seedream-5-0-lite` | Doubao Seedream 5.0 lite |
| 图像 | `gpt-image-2` | GPT Image 2 |
| 图像 | `nano-banana-2` | Nano Banana 2 |
| 图像 | `nano-banana-pro` | Nano Banana Pro |
| 视频 | `wan-2-0` | WAN 2.0 (Seedance 2.0) |
| 视频 | `wan-2-0-fast` | WAN 2.0 Fast (Seedance 2.0 fast) |
| 视频 | `doubao-seedance-1-5-pro` | Doubao Seedance 1.5 Pro |
| 视频 | `doubao-seedance-2-0` | Doubao Seedance 2.0（多模态，API 等同 WAN 2.0） |
| 视频 | `doubao-seedance-2-0-fast` | Doubao Seedance 2.0 fast |
| 视频 | `doubao-seedance-2-0-mini` | Doubao Seedance 2.0 mini（高性价比） |
| 视频 | `doubao-seedance-2-5` | Doubao Seedance 2.5（单段 30 秒、最多 50 个参考素材、多语言配音） |
| 视频 | `happyhorse-1-0-t2v` | happyhorse-1.0-t2v |
| 视频 | `happyhorse-1-0-r2v` | happyhorse-1.0-r2v（参考生视频，最多 9 张参考图） |
| 视频 | `happyhorse-1-0-video-edit` | happyhorse-1.0-video-edit（视频编辑，源视频 + 最多 5 张参考图） |
| 语音 | `seed-tts-2-0` / `minimax-speech-2-8-hd` / `minimax-speech-2-8-turbo` | 豆包语音合成 2.0 / MiniMax 语音 2.8 |
| 视觉理解 | `qwen-3-6-plus` | Qwen3.6-Plus（图像/视频理解与推理） |

> 运行 `cfgpu models list` 查看当前实际加载的模型。

## 安装

需要 Python 3.11+，以及一个 CFGPU API token。

```bash
# 通过 uvx 直接运行，无需安装（推荐）
uvx cfgpu-mcp

# 或安装到环境中（含 CLI）
pip install "cfgpu-mcp[cli]"
```

## 在 MCP 客户端中使用

### Claude Desktop / Claude Code

编辑 MCP 配置（Claude Desktop 为 `claude_desktop_config.json`），加入：

```json
{
  "mcpServers": {
    "cfgpu": {
      "command": "uvx",
      "args": ["cfgpu-mcp"],
      "env": {
        "CFGPU_API_TOKEN": "sk-..."
      }
    }
  }
}
```

重启客户端后即可在对话中使用 `generate_image`、`generate_video`、`generate_audio`、`understand_vision`、`list_models`、`get_model_card` 等工具。

> 若已 `pip install`，把 `command` 改为 `cfgpu-mcp`、`args` 置为 `[]` 即可。

### Cursor / VS Code

在对应的 `mcp.json` 中使用相同的 `command` / `args` / `env` 结构。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `CFGPU_API_TOKEN` | ✅ | CFGPU API 的 Bearer token（唯一 secret） |
| `CFGPU_CONFIG` | | config.yaml 路径（默认 `./config.yaml`） |
| `CFGPU_DOTENV` | | 启动时自动加载的 `.env` 路径（默认 `./.env`） |
| `CFGPU_DRY_RUN` | | 置 1 时记录请求但仍发送，用于调试 |

> 模型白名单（`enabled_models`）、API base URL、超时、`task_db.url`、`transport` 等均只在 **config.yaml** 配置（见 `config.example.yaml`），不再有环境变量 override。需要从环境读 DB URL 时，在 `task_db.url` 写 `$DATABASE_URL`。

## CLI 用法

```bash
# 列出模型
cfgpu models list
cfgpu models list --task-type video

# 生成图片（等待完成，URL 打到 stdout，进度/元数据到 stderr）
cfgpu generate image "a red panda in the snow"
cfgpu generate image "..." --model doubao-seedream-5-0-lite --resolution 2K --json

# 生成视频
cfgpu generate video "waves on a beach" --model wan-2-0-fast -d 4 -r 480p

# 异步工作流
cfgpu generate video "..." --no-wait      # 立即返回 task_id
cfgpu task status <task_id>
cfgpu task wait <task_id>

# 管道友好：stdout = URL(s)，stderr = 进度
cfgpu generate image "..." | xargs open
```

## 在代码中使用（Anthropic / OpenAI / LangGraph）

server 的工具 schema 可直接导出给各家 SDK，service 层也可被直接调用：

```python
from cfgpu_mcp.service import image as image_svc

result = await image_svc.generate_image(
    prompt="a red panda in the snow",
    model="auto",                 # 或单个 id，或候选列表 ["doubao-seedream-5-0-lite", "gpt-image-2"]
    aspect_ratio="16:9",
    resolution="2K",
)
print(result["urls"])
```

完整的多 SDK 集成示例见 [CLIENT_GUIDE.md](CLIENT_GUIDE.md)。

## 开发

```bash
# 安装（可编辑 + 开发依赖 + CLI）
pip install -e ".[dev,cli]"

# 运行单元测试
pytest tests/unit/

# 运行集成测试（需要 CFGPU_API_TOKEN）
CFGPU_API_TOKEN=sk-... pytest tests/integration/ -v

# 本地启动 MCP server（stdio）
cfgpu-mcp
```

架构说明（三种标识符、adapter 注册、路由打分等）见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## License

[MIT](LICENSE)
