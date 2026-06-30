# happyhorse-1.0-i2v

HappyHorse-1.0-I2V 支持图生视频，具备高度还原的动态画面生成能力，能够精准理解文本语义，输出流畅自然、细节丰富的高质量视频。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `happyhorse-1.0-i2v` |
| 能力标签 | image_to_video |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 价格

| 分辨率范围 | 单价 |
|-----------|------|
| (0, 720P] | 0.945 元 / 秒 |
| (720P, 无限] | 1.68 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **image_to_video** | 首帧图片 + 文本生成视频 |

**不支持：** text_to_video（纯文生视频）、last_frame（尾帧）、reference_images（多参考图）、reference_videos、reference_audios、480p 分辨率。

## 参数说明

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | ✓ | 视频描述文本 |
| `first_frame` | string | ✓ | 首帧图片 URL |

### 视频输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resolution` | string | `1080P` | 分辨率：`720P` 或 `1080P`，adapter 自动大写 |
| `aspect_ratio` | string | `16:9` | 宽高比：16:9、9:16、1:1、4:3、3:4、4:5、5:4 |
| `duration_seconds` | integer | 5 | 视频时长（秒） |
| `watermark` | boolean | `true` | 是否添加水印（统一 schema 参数，直接传入） |
| `model_specific.seed` | integer | - | 随机数种子，取值范围 [0, 2147483647] |

## 示例

### 图生视频

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "happyhorse-1.0-i2v",
    "input": {
        "prompt": "一只猫在草地上奔跑",
        "media": [
            {
                "type": "first_frame",
                "url": "https://example.com/cat.jpg"
            }
        ]
    },
    "parameters": {
        "resolution": "720P",
        "duration": 5
    }
}'
```

### 视频查询

```bash
curl -X GET https://www.cfgpu.com/userapi/v1/video/tasks/<TASK_ID> \
--header "Authorization: Bearer <API-TOKEN>"
```

## 响应结构

### 创建任务 POST `/video/generations`（异步）

```json
{
  "requestId": "...",
  "model": "happyhorse-1.0-i2v",
  "output": {
    "taskId": "task-abc123",
    "taskStatus": "PENDING"
  }
}
```

### 查询任务 GET `/video/tasks/{task_id}`

> **注意：响应体字段为 camelCase**（`taskId` / `taskStatus` / `videoUrl` / `origPrompt`），与万相 / Seedance 一致。adapter 据此提取链接。

```json
{
  "requestId": "...",
  "model": "happyhorse-1.0-i2v",
  "output": {
    "taskId": "task-abc123",
    "taskStatus": "SUCCEEDED",
    "videoUrl": "https://cdn.example.com/video.mp4",
    "origPrompt": "一只猫在草地上奔跑",
    "submitTime": "2026-06-10 10:00:00.000",
    "scheduledTime": "2026-06-10 10:00:01.000",
    "endTime": "2026-06-10 10:00:30.000"
  },
  "usage": {
    "duration": 5,
    "outputVideoDuration": 5,
    "videoCount": 1,
    "sr": 720,
    "ratio": null
  }
}
```

**任务状态值：**

| 状态 | 说明 |
|------|------|
| `PENDING` | 任务排队中 |
| `RUNNING` | 任务处理中 |
| `SUCCEEDED` | 任务执行成功 |
| `FAILED` | 任务执行失败 |
| `CANCELED` | 任务已取消（等同于失败） |
| `UNKNOWN` | 任务不存在或状态未知（等同于失败） |

## 约束与限制

- 异步接口，提交后需轮询获取结果
- 创建任务：POST `/video/generations`，返回 `task_id`
- 查询状态：GET `/video/tasks/{task_id}`
- `first_frame` 与 `reference_images` 互斥，不可同时传入

## 与统一 Schema 的映射

| 统一 Schema 字段 | HappyHorse 字段 | 映射说明 |
|------------------|-----------------|----------|
| `prompt` | `input.prompt` | 视频描述文本 |
| `first_frame` | `input.media[].type=first_frame` | 首帧图片（必填） |
| `resolution` | `parameters.resolution` | `720p` → `720P`（uppercase） |
| `aspect_ratio` | `parameters.ratio` | `adaptive` 时不传，API 默认 `16:9` |
| `duration_seconds` | `parameters.duration` | 视频时长（秒） |
| `watermark` | 顶层 `watermark` | 统一 schema 参数，直接映射到 payload 顶层 |
| `model_specific` | `parameters.*` 或顶层 | 可传 `seed` 等额外参数 |

**不支持的统一 Schema 字段：** `last_frame`、`reference_images`、`reference_videos`、`reference_audios`、`with_audio`。
