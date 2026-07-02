# Doubao Seedance 2.0

豆包大模型团队推出的新一代专业级多模态创作视频模型 Seedance 2.0，支持图像、视频、音频等多模态作为参考输入生成视频，还具备视频编辑、延长等能力，能高精度还原各类细节并稳定角色特征，具备极致拟真的视听稳定性，深度适配商业广告、影视制作与社交媒体营销等核心场景。

> **API 完全等同 WAN 2.0。** Seedance 2.0 与 WAN 2.0 的模型能力、参数类型、API 请求体与返回结构完全一致。完整的 content 输入数组、各多模态场景示例与字段映射详见 `wan-video` 的 card.md，此处仅列出本模型的标识与计价差异。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `doubao-seedance-2-0-260128` |
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit, video_extend, audio_generate, web_search |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

## 价格（按 token 计费）

| 分辨率范围 | 场景 | 单价 |
|-----------|------|------|
| (0, 720P] | 有视频输入的有声视频 | 0.0294 元 / K tokens |
| (0, 720P] | 有视频输入的无声视频 | 0.0294 元 / K tokens |
| (0, 720P] | 没有视频输入的有声视频 | 0.0483 元 / K tokens |
| (0, 720P] | 没有视频输入的无声视频 | 0.0483 元 / K tokens |
| (720P, 无限] | 有视频输入的有声视频 | 0.03255 元 / K tokens |
| (720P, 无限] | 有视频输入的无声视频 | 0.03255 元 / K tokens |
| (720P, 无限] | 没有视频输入的有声视频 | 0.05355 元 / K tokens |
| (720P, 无限] | 没有视频输入的无声视频 | 0.05355 元 / K tokens |

> Token 消耗见响应结构中的 `usage.totalTokens` 字段。

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频 |
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **first_last_frame** | 首帧 + 尾帧图片 + 文本生成视频（精准控制起止画面） |
| **multi_modal_reference** | 多模态参考生视频：图片(0-9) + 视频(0-3) + 音频(0-3) + 文本 |
| **video_edit** | 基于参考视频进行编辑（替换元素、修改内容） |
| **video_extend** | 延长已有视频时长 |
| **audio_generate** | 生成与画面同步的有声视频（人声、音效、背景音乐） |
| **web_search** | 联网搜索增强（仅文生视频支持） |

## 模型版本对比

| 模型 | CFGPU Model ID | 特点 |
|------|----------------|------|
| Doubao Seedance 2.0 | `doubao-seedance-2-0-260128` | 专业级品质，全能力支持 |
| Doubao Seedance 2.0 fast | `doubao-seedance-2-0-fast-260128` | 继承 2.0 核心能力，生成速度更快 |

## 参数与统一 Schema 映射

与 WAN 2.0 一致：

| 统一 Schema 字段 | API 字段 | 说明 |
|------------------|----------|------|
| `prompt` | `content[].type=text` | 视频描述文本 |
| `first_frame` | `content[].role=first_frame` | 首帧图片 |
| `last_frame` | `content[].role=last_frame` | 尾帧图片（需与 first_frame 同用） |
| `reference_images` | `content[].role=reference_image` | 参考图片（0-9，与首/尾帧互斥） |
| `reference_videos` | `content[].role=reference_video` | 参考视频（0-3） |
| `reference_audios` | `content[].role=reference_audio` | 参考音频（0-3） |
| `aspect_ratio` | 顶层 `ratio` | 宽高比，`adaptive` 自动匹配 |
| `duration_seconds` | 顶层 `duration` | 时长 4–15 秒，`-1` 为智能时长 |
| `resolution` | 顶层 `resolution` | 480p / 720p / 1080p |
| `with_audio` | 顶层 `generate_audio` | 是否生成有声视频 |
| `watermark` | 顶层 `watermark` | 是否添加水印 |

## 示例

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "doubao-seedance-2-0-260128",
    "content": [
        {"type": "text", "text": "海浪拍打沙滩，黄昏，电影质感"}
    ],
    "ratio": "16:9",
    "duration": 5,
    "resolution": "1080P",
    "generate_audio": true
}'
```

完整的多模态请求示例（首尾帧、参考图/视频/音频、视频编辑、延长）与响应结构请参见 `wan-video` 的 card.md。
