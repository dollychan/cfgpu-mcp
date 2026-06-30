# happyhorse-1.0-r2v

HappyHorse-1.0-R2V 支持参考生视频，更加稳定的主体与场景参考，支持最多 9 张图片参考，能够精准保持创作意图，实现更强表现能力。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `happyhorse-1.0-r2v` |
| 能力标签 | multi_modal_reference |
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
| **multi_modal_reference** | 参考生视频：最多 9 张参考图片 + 文本生成视频，稳定保持主体与场景参考 |

**不支持：** text_to_video（纯文生视频）、image_to_video（首帧图生视频）、last_frame（尾帧）、reference_videos、reference_audios、480p 分辨率。

## 参数说明

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | ✓ | 视频描述文本；可用 `[Image N]` 引用第 N 张参考图 |
| `reference_images` | list[str] | ✓ | 参考图片 URL 列表，最多 9 张 |

### 视频输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resolution` | string | `1080P` | 分辨率：`720P` 或 `1080P`，adapter 自动大写 |
| `aspect_ratio` | string | `16:9` | 宽高比：16:9、9:16、1:1、4:3、3:4、4:5、5:4 |
| `duration_seconds` | integer | 5 | 视频时长（秒） |
| `watermark` | boolean | `true` | 是否添加水印（统一 schema 参数，直接传入） |
| `model_specific.seed` | integer | - | 随机数种子，取值范围 [0, 2147483647] |

## 示例

### 参考生视频

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "happyhorse-1.0-r2v",
    "input": {
        "prompt": "[Image 1]中身着红色旗袍的女性，轻抬玉手展开[Image 2]中的折扇，[Image 3]中的流苏耳坠随头部转动轻盈摆动，多视角全方位展现东方韵味。",
        "media": [
            {"type": "reference_image", "url": "https://example.com/1.jpg"},
            {"type": "reference_image", "url": "https://example.com/2.jpg"},
            {"type": "reference_image", "url": "https://example.com/3.jpg"}
        ]
    },
    "parameters": {
        "resolution": "720P",
        "ratio": "16:9",
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
  "model": "happyhorse-1.0-r2v",
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
  "model": "happyhorse-1.0-r2v",
  "output": {
    "taskId": "task-abc123",
    "taskStatus": "SUCCEEDED",
    "videoUrl": "https://cdn.example.com/video.mp4",
    "origPrompt": "...",
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
- 参考生视频依赖 `reference_images`（最多 9 张），不使用首帧 `first_frame`

## 与统一 Schema 的映射

| 统一 Schema 字段 | HappyHorse 字段 | 映射说明 |
|------------------|-----------------|----------|
| `prompt` | `input.prompt` | 视频描述文本 |
| `reference_images` | `input.media[].type=reference_image` | 参考图片数组（最多 9 张） |
| `resolution` | `parameters.resolution` | `720p` → `720P`（uppercase） |
| `aspect_ratio` | `parameters.ratio` | `adaptive` 时不传，API 默认 `16:9` |
| `duration_seconds` | `parameters.duration` | 视频时长（秒） |
| `watermark` | 顶层 `watermark` | 统一 schema 参数，直接映射到 payload 顶层 |
| `model_specific` | `parameters.*` 或顶层 | 可传 `seed` 等额外参数 |

**不支持的统一 Schema 字段：** `first_frame`、`last_frame`、`reference_videos`、`reference_audios`、`with_audio`。
