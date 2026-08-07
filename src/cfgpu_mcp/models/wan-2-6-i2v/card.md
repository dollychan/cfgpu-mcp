# 万相 2.6 图生视频 (wan2.6-i2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.6-i2v` |
| 能力标签 | image_to_video, audio_generate |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.6 图生视频模型：输入一张首帧图片（+ 可选驱动音频）+ 文本提示词，生成视频。可由音频驱动（如让图中角色按音频 rap）。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **audio_generate** | 可传入驱动音频（audio_url），让画面与音频同步 |

> input 使用扁平字段 `img_url`（首帧，必填）和 `audio_url`（可选），**不使用 media 数组**。不支持首尾帧、参考图片/视频。

## 参数说明

| 统一 Schema 字段 | wan2.6-i2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词 |
| first_frame | input.img_url | 首帧图片 URL（必填） |
| reference_audios[0] | input.audio_url | 驱动音频 URL（可选，取第一个） |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

## 请求示例

```json
{
  "model": "wan2.6-i2v",
  "input": {
    "prompt": "一个由喷漆画成的少年从墙上活过来，用极快语速演唱英文 rap...",
    "img_url": "https://.../rap.png",
    "audio_url": "https://.../rap.mp3"
  },
  "parameters": {"resolution": "720P", "duration": 5}
}
```

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL（24 小时内有效）

## 响应结构

创建任务响应（snake_case）：

```json
{"output":{
  "task_status":"PENDING",
  "task_id":"36598b68-c4f5-423c-92a1-2d144692c1d0"},
  "request_id":"e25956ba-fa12-9eda-8bcb-a04225e8ef70"}
```

查询任务结果 GET `/video/tasks/{task_id}`（camelCase）：

```json
{"requestId":"c6b9559f-4c28-98b0-86ea-2ed499172652",
"model":"wan2.6-i2v",
"output":{"taskId":"36598b68-c4f5-423c-92a1-2d144692c1d0",
"taskStatus":"SUCCEEDED",
"submitTime":"2026-06-30 18:07:20.235",
"scheduledTime":"2026-06-30 18:07:20.275",
"endTime":"2026-06-30 18:10:08.044",
"origPrompt":"...",
"videoUrl":"https://dashscope-a717.oss-accelerate.aliyuncs.com/...mp4?Expires=1782900606&..."},
"usage":{"duration":5,"inputVideoDuration":0,"outputVideoDuration":5,"videoCount":1,"sr":720,"ratio":"16:9"}
}
```

| 字段 | 说明 |
|------|------|
| `output.taskId` | 任务 ID（创建响应为 snake_case `output.task_id`） |
| `output.taskStatus` | 任务状态：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`（创建响应为 `output.task_status`；`CANCELED` / `UNKNOWN` 等同于失败） |
| `output.videoUrl` | 生成的视频 URL（24 小时有效） |
| `usage.duration` / `usage.outputVideoDuration` | 计费时长（秒） |
| `usage.sr` / `usage.ratio` | 输出分辨率（短边，如 `720`）/ 宽高比 |

> 本系列按秒计费、单价随分辨率分档，计费口径是 `usage.duration` + `usage.sr`，**不是** token。

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 必填输入 | 首帧图片（first_frame → img_url） |
| 可选输入 | 单个驱动音频（reference_audios[0] → audio_url） |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
