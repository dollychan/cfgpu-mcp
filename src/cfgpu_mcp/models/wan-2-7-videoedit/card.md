# 万相 2.7 视频编辑 (wan2.7-videoedit)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.7-videoedit` |
| 任务（tasks） | video_edit |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.7 视频编辑模型：输入一段源视频 + 参考图片 + 文本指令，对视频进行编辑（如替换元素）。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **video_edit** | 基于源视频 + 参考图片进行编辑（替换元素、修改内容） |

> 需提供 1 个源视频（reference_videos），可选参考图片（reference_images）。不支持首帧/尾帧、参考音频。

## 参数说明

| 统一 Schema 字段 | wan2.7-videoedit 字段 | 映射说明 |
|------------------|------------------------|----------|
| prompt | input.prompt | 编辑指令 |
| reference_videos | input.media[]（type=video） | 源视频 URL（单个） |
| reference_images | input.media[]（type=reference_image） | 参考图片 URL 数组 |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| aspect_ratio | parameters.ratio | 显式设置时才传；省略时沿用输入视频比例 |
| prompt_extend | parameters.prompt_extend | 是否在生成前用大语言模型扩写提示词，默认 `true` |
| watermark | parameters.watermark | 是否添加水印，默认 `false` |
| duration_seconds | parameters.duration | 2–10 秒时截断输入视频；省略时沿用完整输入视频（上游默认 0） |
| negative_prompt | input.negative_prompt | 最多 500 字符 |
| model_specific.parameters.audio_setting | parameters.audio_setting | `auto` / `origin` |
| model_specific.parameters.seed | parameters.seed | 0–2147483647；与基础 parameters 深度合并 |

## 请求示例

```json
{
  "model": "wan2.7-videoedit",
  "input": {
    "prompt": "将视频中女孩的衣服替换为图片中的衣服",
    "media": [
      {"type": "video", "url": "https://.../T2VA_22.mp4"},
      {"type": "reference_image", "url": "https://.../change-clothes.png"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "prompt_extend": true,
    "watermark": false
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
"model":"wan2.7-videoedit",
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
| 必填输入 | 1 个源视频（reference_videos） |
| 视频时长 | 可选 2–10 秒；省略时保留完整输入视频 |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
