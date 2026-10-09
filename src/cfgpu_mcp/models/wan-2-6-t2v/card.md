# 万相 2.6 文生视频 (wan2.6-t2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.6-t2v` |
| 任务（tasks） | text_to_video, audio_driven_video |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.6 文生视频模型：纯文本提示词生成视频，支持电影级分镜叙事。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 文本提示词生成视频 |
| **audio_driven_video** | 可自动生成配套音频，或使用一条驱动音频同步口型 |

> 不使用 `media` 数组；可选的驱动音频使用 `input.audio_url`。

## 参数说明

| 统一 Schema 字段 | wan2.6-t2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词，支持分镜描述 |
| resolution | parameters.size | `720p` → `1280*720`；`1080p` → `1920*1080` |
| aspect_ratio | - | 此模型的公开规格仅提供 16:9 尺寸；`adaptive` 按 16:9 处理 |
| reference_audios[0] | input.audio_url | 可选驱动音频；未提供时模型自动生成音频 |
| negative_prompt | parameters.negative_prompt | 负向提示词 |
| prompt_extend | parameters.prompt_extend | 是否在生成前用大语言模型扩写提示词，默认 `true` |
| watermark | parameters.watermark | 是否添加水印，默认 `false` |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific.parameters.shot_type | parameters.shot_type | `single` / `multi`；仅 `prompt_extend=true` 时生效 |
| model_specific.parameters.seed | parameters.seed | 0–2147483647；与基础 parameters 深度合并 |

## 请求示例

```json
{
  "model": "wan2.6-t2v",
  "input": {
    "prompt": "一段紧张刺激的侦探追查故事...第1个镜头[0-3秒] 全景：雨夜的纽约街头..."
  },
  "parameters": {
    "size": "1280*720",
    "prompt_extend": true,
    "watermark": false,
    "duration": 5
  }
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
"model":"wan2.6-t2v",
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
| 输入 | 文本及可选的一条 `audio_url`（无 media） |
| 视频时长 | 2–15 秒整数（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
