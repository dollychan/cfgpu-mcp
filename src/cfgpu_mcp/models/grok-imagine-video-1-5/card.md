# Grok Imagine Video 1.5

Grok‑Imagine‑Video 是 xAI 开发的文生 / 图生视频生成模型，采用 Aurora 自回归帧架构，可根据文字或图片生成数秒 720p 短视频，并**同步输出音频**。属于生视频多模态模型。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| 公开模型 ID（`model` 参数 / `list_models`） | `cf-imagine-video-1.5` |
| 上游 API 模型 ID（仅出现在请求体 `model` 字段） | `grok-imagine-video-1.5` |
| 能力标签 | text_to_video, image_to_video, multi_modal_reference |
| 成本档位 | 3/5 |
| 速度档位 | 3/5 |

## 价格

按秒计费，单价随输出分辨率分档：

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 480P] | 统一计价 | 0.44 元 / 秒 |
| 分辨率 (480P, 720P] | 统一计价 | 0.77 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.32 元 / 秒 |

## 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 固定值：`grok-imagine-video-1.5` |
| prompt | string | ✓ | - | 视频描述，支持中英文 |
| aspect_ratio | string | ✓ | 16:9 | 宽高比，如 `16:9` / `9:16` |
| video_length | string | ✓ | "5" | 视频时长（秒），**字符串**形式 |
| resolution_name | string | ✓ | 720p | 分辨率档位，**小写**（`480p` / `720p` / `1080p`） |
| refer_images | array | - | - | 参考图 URL 数组（图生视频） |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Grok 字段 | 映射说明 |
|------------------|-----------|----------|
| prompt | prompt | 直接透传 |
| aspect_ratio | aspect_ratio | 直接透传；`adaptive` 按 `16:9` 下发（请求体没有自适应取值） |
| resolution | resolution_name | 直接透传，保持小写（`720p`，不像万相那样转大写） |
| duration_seconds | video_length | 转成字符串透传；不支持 `-1` 智能时长 |
| first_frame | refer_images[0] | 首帧即参考图，排在数组第一位 |
| reference_images | refer_images[] | 追加在 `first_frame` 之后 |
| last_frame | -（不支持） | 请求体只有一个图片槽位，`supports()` 直接拒绝 |
| reference_videos / reference_audios | -（不支持） | 请求体没有视频 / 音频输入槽位，`supports()` 直接拒绝 |
| with_audio | -（不下发） | 模型恒定同步输出音频，请求体没有开关 |
| watermark | -（不下发） | 请求体没有水印字段，需要时用 `model_specific` |
| model_specific | -（合并到顶层） | 末位合并，可覆盖上述字段 |

## 能力与限制

- 支持文生视频与图生视频（参考图）。
- 音频恒定生成，`with_audio=false` 不会生效（请求体没有对应开关）。
- 不支持 `last_frame` / `reference_videos` / `reference_audios`。
- 需要显式时长，不支持 `duration_seconds=-1`。

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `data.taskId`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务未 `completed` 时持续查询
4. **获取结果**：任务 `completed` 后从 `data.videoUrl` 取视频 URL（24 小时内有效）

## 示例

### 文生视频

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "prompt": "镜头不动，石灯上的蚂蚁正在爬行，背景花草随风轻微晃动",
    "model": "grok-imagine-video-1.5",
    "aspect_ratio": "16:9",
    "video_length": "10",
    "resolution_name": "720p"
}'
```

### 图生视频（参考图）

```json
{
  "prompt": "镜头不动，石灯上的蚂蚁正在爬行，背景花草随风轻微晃动",
  "model": "grok-imagine-video-1.5",
  "aspect_ratio": "16:9",
  "video_length": "10",
  "resolution_name": "720p",
  "refer_images": ["https://smartml-oss-production.cfgpu.com/llm-experience/image/2026/08/06/793b73c0372b46fa92ab13365c0a1748.jpeg?Expires=..."]
}
```

### 查询任务

```bash
curl -X GET https://www.cfgpu.com/userapi/v1/video/tasks/<TASK_ID> \
--header "Authorization: Bearer <API-TOKEN>"
```

## 响应结构

查询任务结果 GET `/video/tasks/{task_id}`，结果包在 `data` 里：

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "taskId": "d7f12675-02f4-49f4-9ece-3b42fb74e87d",
        "status": "completed",
        "jobId": "6fe2dc0e-b655-9003-b3fe-d5ecf89ebda3",
        "videoId": null,
        "videoUrl": "https://smartml-oss-production.cfgpu.com/VIDEO_GENERATIONS/2026-08-11/.../d587fae059dd4822b1bbe98321c96ad9.mp4?Expires=...",
        "proxyUrl": null,
        "prompt": null,
        "aspectRatio": null,
        "videoLength": 1,
        "resolutionName": null,
        "quota": null,
        "finishTime": null,
        "pointsCost": 25,
        "pointsRefunded": false
    }
}
```

| 结果字段 | 来源 |
|---|---|
| `urls[0]` | `data.videoUrl`（为空时退回 `data.proxyUrl`） |
| `task_id` | `data.taskId` |
| `aspect_ratio` | `data.aspectRatio`；为 `null` 时兜底为本次请求的 `aspect_ratio` |

### 计费字段（响应无 `usage`，由 adapter 组装）

Grok 按秒计费、单价随分辨率分档，但任务响应里**没有 `usage` 对象**，计费数值散在 `data` 里。adapter 将其组装为与可灵 / 万相 / HappyHorse 一致的形状：

| 结果 `usage` 字段 | 来源 | 说明 |
|---|---|---|
| `duration` | `data.videoLength` | 计费时长（秒）；字符串形式会转为数字 |
| `sr` | `data.resolutionName`（`"720p"` → `720`） | 分辨率档位（短边） |
| `ratio` | `data.aspectRatio` | 输出宽高比 |

例：`"videoLength": 10` + `"resolutionName": "720p"` + `"aspectRatio": "16:9"` → `usage: {"duration": 10, "sr": 720, "ratio": "16:9"}`。三项都取不到时（如任务尚在排队）`usage` 为 `null`，而不是一个全空的记录。
