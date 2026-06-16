# Doubao Seedance 2.0 fast

Seedance 2.0 fast 是豆包大模型团队推出的新一代多模态视频创作模型，继承了 Seedance 2.0 模型的核心功能和优势，生成速度更快。

> **API 完全等同 WAN 2.0 / Seedance 2.0。** 模型能力、参数类型、API 请求体与返回结构完全一致，共用 `SeedanceVideoAdapter`（`adapters/seedance_video.py`）。完整的 content 输入数组、各多模态场景示例与字段映射详见 `wan-2-0` 的 card.md，此处仅列出本模型的标识与计价差异。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `doubao-seedance-2-0-fast-260128` |
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit, video_extend, audio_generate, web_search |
| 成本档位 | 2/5 |
| 速度档位 | 4/5 |

## 价格（按 token 计费）

| 分辨率范围 | 场景 | 单价 |
|-----------|------|------|
| (0, 无限] | 有视频输入的有声视频 | 0.0231 元 / K tokens |
| (0, 无限] | 有视频输入的无声视频 | 0.0231 元 / K tokens |
| (0, 无限] | 没有视频输入的有声视频 | 0.03885 元 / K tokens |
| (0, 无限] | 没有视频输入的无声视频 | 0.03885 元 / K tokens |

> Token 消耗见响应结构中的 `usage.totalTokens` 字段。fast 版本所有分辨率统一计价。

## 能力说明

与 Seedance 2.0 完全相同（text_to_video / image_to_video / first_last_frame / multi_modal_reference / video_edit / video_extend / audio_generate / web_search），生成速度更快、成本更低。

## 参数与统一 Schema 映射

与 WAN 2.0 / Seedance 2.0 一致：

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
    "model": "doubao-seedance-2-0-fast-260128",
    "content": [
        {"type": "text", "text": "一只橘猫在阳光下奔跑，慢镜头"}
    ],
    "ratio": "16:9",
    "duration": 5,
    "resolution": "720P",
    "generate_audio": true
}'
```

完整的多模态请求示例与响应结构请参见 `wan-2-0` 的 card.md。
