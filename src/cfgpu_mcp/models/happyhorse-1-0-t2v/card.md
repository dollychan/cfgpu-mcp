# happyhorse-1.0-t2v

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `happyhorse-1.0-t2v` |
| 能力标签 | text_to_video, image_to_video, multi_modal_reference |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频 |
| **image_to_video** | 首帧图片 + 文本生成视频 |
| **multi_modal_reference** | 多张参考图片 + 文本生成视频 |

**不支持：** last_frame（尾帧）、reference_videos（参考视频）、reference_audios（参考音频）、480p 分辨率。

## 参数说明

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | ✓ | 视频描述文本 |
| `first_frame` | string | | 首帧图片 URL |
| `reference_images` | list[str] | | 参考图片 URL 列表；与 `first_frame` 互斥 |

### 视频输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resolution` | string | `1080P` | 分辨率：`720P` 或 `1080P`，均可经 unified schema `resolution`（`720p` / `1080p`）传入，adapter 自动大写 |
| `aspect_ratio` | string | `16:9` | 宽高比，见下方表格；`adaptive` 将被忽略，使用 API 默认值 |
| `duration_seconds` | integer | 5 | 视频时长（秒） |
| `watermark` | boolean | `true` | 是否添加水印（统一 schema 参数，直接传入） |
| `model_specific.seed` | integer | - | 随机数种子，取值范围 [0, 2147483647] |

**支持的宽高比：**

| 值 | 说明 |
|----|------|
| `16:9` | 标准宽屏（默认） |
| `9:16` | 手机竖屏 |
| `1:1` | 正方形 |
| `4:3` | 传统比例 |
| `3:4` | 竖版 |
| `4:5` | 竖版（via model_specific） |
| `5:4` | 横版（via model_specific） |

注意：unified schema 中的 `21:9` 和 `adaptive` 不在 HappyHorse 支持列表中。`adaptive` 会被忽略（API 默认 `16:9`）；`21:9` 会直接透传，API 可能返回参数错误。

## 价格

| 分辨率范围 | 单价 |
|-----------|------|
| (0, 720P] | 0.945 元 / 秒 |
| (720P, 无限] | 1.68 元 / 秒 |

## 示例

### 文生视频

```json
{
  "model": "happyhorse-1.0-t2v",
  "input": {
    "prompt": "一座由硬纸板和瓶盖搭建的微型城市，在夜晚焕发出生机。一列硬纸板火车缓缓驶过，小灯点缀其间，照亮前路。"
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5
  }
}
```

### 图生视频（首帧）

```json
{
  "model": "happyhorse-1.0-t2v",
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
}
```

### 参考图生视频

```json
{
  "model": "happyhorse-1.0-t2v",
  "input": {
    "prompt": "身着红色旗袍的女性，镜头侧面中景，随后切换低角度仰拍",
    "media": [
      {"type": "reference_image", "url": "https://example.com/ref1.jpg"},
      {"type": "reference_image", "url": "https://example.com/ref2.jpg"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5
  }
}
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
  "model": "happyhorse-1.0-t2v",
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

## 与统一 Schema 的映射

| 统一 Schema 字段 | HappyHorse 字段 | 映射说明 |
|------------------|-----------------|----------|
| `prompt` | `input.prompt` | 视频描述文本 |
| `first_frame` | `input.media[].type=first_frame` | 首帧图片 |
| `reference_images` | `input.media[].type=reference_image` | 参考图片；与 first_frame 互斥 |
| `resolution` | `parameters.resolution` | `720p` → `720P`（uppercase） |
| `aspect_ratio` | `parameters.ratio` | `adaptive` 时不传，API 默认 `16:9` |
| `duration_seconds` | `parameters.duration` | 视频时长（秒） |
| `watermark` | 顶层 `watermark` | 统一 schema 参数，直接映射到 payload 顶层 |
| `model_specific` | `parameters.*` 或顶层 | 可传 `seed` 等额外参数 |

**不支持的统一 Schema 字段：** `last_frame`、`reference_videos`、`reference_audios`、`with_audio`（无音频控制）。
