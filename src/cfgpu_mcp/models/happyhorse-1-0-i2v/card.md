# happyhorse-1.0-i2v

HappyHorse-1.0-I2V 支持图生视频，具备高度还原的动态画面生成能力，能够精准理解文本语义，输出流畅自然、细节丰富的高质量视频。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `happyhorse-1.0-i2v` |
| 任务（tasks） | image_to_video |
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

**不支持：** text_to_video（纯文生视频）、last_frame（尾帧）、reference_images（多参考图）、reference_videos、reference_audios。支持 480P / 720P / 1080P。

## 参数说明

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | | 视频描述文本；省略时模型根据首帧推断运动 |
| `first_frame` | string | ✓ | 首帧图片 URL |

### 视频输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resolution` | string | `1080P` | 分辨率：`480P`、`720P` 或 `1080P`，adapter 自动大写 |
| `aspect_ratio` | - | 输出比例自动跟随首帧，不发送 ratio 参数 |
| `duration_seconds` | integer | 5 | 视频时长（秒） |
| `watermark` | boolean | `false` | 写入 `parameters.watermark`；上游仅在省略时默认 `true` |
| `model_specific.parameters.seed` | integer | - | 随机数种子，取值范围 [0, 2147483647] |

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

> **注意：创建响应是 snake_case**（`request_id` / `task_id` / `task_status`），且**不回显 `model`**；只有查询响应是 camelCase。两个端点写法不一致，adapter 两种都读。

```json
{
  "request_id": "b0850872-0dd2-9301-9f19-1691a1970db4",
  "output": {
    "task_id": "b7bc7a97-7f49-4e66-b2cc-5505d7c53c2d",
    "task_status": "PENDING"
  }
}
```

### 查询任务 GET `/video/tasks/{task_id}`

> **注意：查询响应体字段为 camelCase**（`taskId` / `taskStatus` / `videoUrl` / `origPrompt`），与万相 / Seedance 一致 —— 与上面创建响应的 snake_case 不同。失败原因在 `output.message` / `output.code`（成功时两者为 `null`）。

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
- 必须提供 `first_frame`；不接受 `reference_images`

## 与统一 Schema 的映射

| 统一 Schema 字段 | HappyHorse 字段 | 映射说明 |
|------------------|-----------------|----------|
| `prompt` | `input.prompt` | 视频描述文本 |
| `first_frame` | `input.media[].type=first_frame` | 首帧图片（必填） |
| `resolution` | `parameters.resolution` | `720p` → `720P`（uppercase） |
| `duration_seconds` | `parameters.duration` | 视频时长（秒） |
| `watermark` | `parameters.watermark` | 统一 schema 默认 `false`；上游省略时默认 `true` |
| `model_specific.parameters` | `parameters.*` | 与 typed parameters 深度合并，可传 `seed` |

**不支持的统一 Schema 字段：** `last_frame`、`reference_images`、`reference_videos`、`reference_audios`、`with_audio`。
