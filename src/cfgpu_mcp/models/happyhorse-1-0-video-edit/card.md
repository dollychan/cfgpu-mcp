# happyhorse-1.0-video-edit

HappyHorse-1.0-Video-Edit 支持视频编辑，通过自然语言指令编辑视频，可参考最多 5 张图片进行局部或全局编辑视频元素，能够精准复刻视频动态过程，实现更强表现能力。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `happyhorse-1.0-video-edit` |
| 能力标签 | video_edit |
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
| **video_edit** | 自然语言指令编辑源视频，可叠加最多 5 张参考图进行局部或全局编辑，精准复刻视频动态过程 |

**不支持：** text_to_video（纯文生视频）、image_to_video（首帧图生视频）、first_frame/last_frame、reference_audios、480p 分辨率。

## 参数说明

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | ✓ | 自然语言编辑指令 |
| `reference_videos` | list[str] | ✓ | 源视频 URL（单个，待编辑视频） |
| `reference_images` | list[str] | | 参考图片 URL 列表，最多 5 张 |

### 视频输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resolution` | string | `1080P` | 分辨率：`720P` 或 `1080P`，adapter 自动大写 |
| `watermark` | boolean | `true` | 是否添加水印（统一 schema 参数，直接传入） |
| `model_specific.seed` | integer | - | 随机数种子，取值范围 [0, 2147483647] |

> 输出视频的时长与宽高比跟随源视频，因此 `duration_seconds` / `aspect_ratio` 不写入 payload。

## 示例

### 视频编辑

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer <API-TOKEN>" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "happyhorse-1.0-video-edit",
    "input": {
        "prompt": "让视频中的马头人身角色穿上图片中的条纹毛衣",
        "media": [
            {"type": "video", "url": "https://example.com/source.mp4"},
            {"type": "reference_image", "url": "https://example.com/sweater.jpg"}
        ]
    },
    "parameters": {
        "resolution": "720P"
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
  "output": {
    "task_id": "task-abc123",
    "task_status": "PENDING"
  },
  "request_id": "..."
}
```

### 查询任务 GET `/video/tasks/{task_id}`

```json
{
  "output": {
    "task_id": "task-abc123",
    "task_status": "SUCCEEDED",
    "video_url": "https://cdn.example.com/video.mp4",
    "orig_prompt": "让视频中的马头人身角色穿上图片中的条纹毛衣",
    "submit_time": "2026-06-10 10:00:00.000",
    "scheduled_time": "2026-06-10 10:00:01.000",
    "end_time": "2026-06-10 10:00:30.000"
  },
  "usage": {
    "input_video_duration": 5,
    "output_video_duration": 5,
    "duration": 5,
    "SR": 720,
    "video_count": 1
  },
  "request_id": "..."
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
- 必须提供单个源视频（`reference_videos`）；参考图最多 5 张
- 输出时长跟随源视频，不接受自定义 `duration`

## 与统一 Schema 的映射

| 统一 Schema 字段 | HappyHorse 字段 | 映射说明 |
|------------------|-----------------|----------|
| `prompt` | `input.prompt` | 自然语言编辑指令 |
| `reference_videos` | `input.media[].type=video` | 源视频（单个，必填） |
| `reference_images` | `input.media[].type=reference_image` | 参考图片数组（最多 5 张） |
| `resolution` | `parameters.resolution` | `720p` → `720P`（uppercase） |
| `watermark` | 顶层 `watermark` | 统一 schema 参数，直接映射到 payload 顶层 |
| `model_specific` | `parameters.*` 或顶层 | 可传 `seed` 等额外参数 |

**不支持的统一 Schema 字段：** `first_frame`、`last_frame`、`reference_audios`、`with_audio`、`duration_seconds`（跟随源视频）、`aspect_ratio`（跟随源视频）。
