# Doubao Seedance 2.0 mini

Seedance 2.0 mini 是面向更广泛视频生成需求推出的新一代高性价比视频生成模型。在保持竞争力效果的同时，将视频生成能力带入更低门槛、更高频、更规模化的应用场景。

> **API 请求形态与 WAN 2.0 / Seedance 2.0 相同。** 使用相同的 content 多模态数组和返回结构；分辨率等模型专属范围以本卡为准。完整场景示例详见 `wan-video` 的 card.md。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `Doubao-Seedance-2.0-mini` |
| 任务（tasks） | text_to_video, image_to_video, first_last_frame, reference_to_video, framed_reference_video, video_edit, video_extend, synced_audio_output, voice_reference_video |
| 成本档位 | 1/5 |
| 速度档位 | 3/5 |

## 价格（按 token 计费）

| 分辨率范围 | 场景 | 单价 |
|-----------|------|------|
| (0, 无限] | 有视频输入的有声视频 | 0.0147 元 / K tokens |
| (0, 无限] | 有视频输入的无声视频 | 0.0147 元 / K tokens |
| (0, 无限] | 没有视频输入的有声视频 | 0.02415 元 / K tokens |
| (0, 无限] | 没有视频输入的无声视频 | 0.02415 元 / K tokens |

> Token 消耗见响应结构中的 `usage.totalTokens` 字段。有视频输入按 0.0147 元/K tokens 计价，无视频输入按 0.02415 元/K tokens 计价（与是否有声无关）。

## 能力说明

与 Seedance 2.0 完全相同（text_to_video / image_to_video / first_last_frame / reference_to_video / video_edit / video_extend / synced_audio_output / voice_reference_video），更低门槛、更高性价比，适合高频、规模化场景。

## 参数与统一 Schema 映射

与 WAN 2.0 / Seedance 2.0 一致：

| 统一 Schema 字段 | API 字段 | 说明 |
|------------------|----------|------|
| `prompt` | `content[].type=text` | 文生视频必填；图生视频、首尾帧和全模态参考任务可选 |
| `first_frame` | `content[].role=first_frame` | 首帧图片 |
| `last_frame` | `content[].role=last_frame` | 尾帧图片（需与 first_frame 同用） |
| `reference_images` | `content[].role=reference_image` | 参考图片（0-9）。与首/尾帧不能同时作为参数；要某张参考图作首/尾帧，在 prompt 里写「以 [[handle]] 作为视频首帧」 |
| `reference_videos` | `content[].role=reference_video` | 参考视频（0-3） |
| `reference_audios` | `content[].role=reference_audio` | 参考音频（0-3）：音色、节奏、音画同步参考 |
| `aspect_ratio` | 顶层 `ratio` | 宽高比，`adaptive` 自动匹配 |
| `duration_seconds` | 顶层 `duration` | 时长 4–15 秒，`-1` 为智能时长 |
| `resolution` | 顶层 `resolution` | **仅 480p / 720p**，默认 720p（无 1080p，需要 1080p 请用 Seedance 2.0） |
| `with_audio` | 顶层 `generate_audio` | 是否生成有声视频 |
| `watermark` | 顶层 `watermark` | 是否添加水印 |

> 参考音频不可单独输入；使用 `reference_audios` 时，至少还需传入 1 张参考图片或 1 个参考视频。

**参考音频的用途**（由 prompt 指定，如「小姚的音色参考音频1」「背景 BGM 参考音频1」）：原生音画同步、节奏适配，以及音色与语音克隆 —— 用样本的音色说 prompt 里的新台词，样本本身的内容不会出现在成片里（`voice_reference_video`）。参考音频不会原样保留为成片音轨；要原声当音轨、口型跟着对，用 `audio_driven_video` 的模型。

## 示例

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "Doubao-Seedance-2.0-mini",
    "content": [
        {"type": "text", "text": "写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上，花瓣上有几颗晶莹的露珠"}
    ],
    "ratio": "16:9",
    "duration": 5,
    "watermark": false
}'
```

完整的多模态请求示例与响应结构请参见 `wan-video` 的 card.md。
