# 万相 2.7 图生视频 (wan2.7-i2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.7-i2v` |
| 能力标签 | image_to_video, first_last_frame, video_extend, audio_generate |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.7 图生视频模型：以首帧图片生成视频，也支持首尾帧控制、驱动音频口型同步和以视频片段续生。

## 价格

按视频时长（秒）计费，单价随输出分辨率分档：

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **first_last_frame** | 首帧 + 尾帧控制起止画面；可额外提供驱动音频 |
| **audio_generate** | 首帧模式可提供 WAV/MP3 驱动音频实现口型同步；未提供时模型自动生成配套音效 |
| **video_extend** | 一个 2–10 秒源视频片段续生；可附加尾帧 |

支持的媒体组合：`first_frame`、`first_frame + driving_audio`、`first_frame + last_frame`、`first_frame + last_frame + driving_audio`、`first_clip`、`first_clip + last_frame`。每种媒体类型最多一个。项目统一字段中，`reference_audios[0]` 映射为 `driving_audio`，`reference_videos[0]` 映射为续生源 `first_clip`；后者不是参考生视频能力。

## 参数说明

| 统一 Schema 字段 | wan2.7-i2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词 |
| negative_prompt | input.negative_prompt | 不希望出现的内容，最长 500 字符 |
| first_frame | input.media[]（type=first_frame） | 与 `reference_videos[0]` 二选一 |
| last_frame | input.media[]（type=last_frame） | 可与首帧或续生源视频组合 |
| reference_audios[0] | input.media[]（type=driving_audio） | 仅首帧模式；驱动音频支持 WAV/MP3，2–30 秒 |
| reference_videos[0] | input.media[]（type=first_clip） | 唯一源视频，表示视频续生，支持 MP4/MOV，2–10 秒 |
| resolution | parameters.resolution | `720P` 或 `1080P`，默认 `1080P` |
| prompt_extend | parameters.prompt_extend | 是否在生成前用大语言模型扩写提示词，默认 `true` |
| watermark | parameters.watermark | 是否添加水印，默认 `false` |
| duration_seconds | parameters.duration | 整数 2–15 秒，默认 5；续生时为包含输入片段的总输出时长 |
| model_specific.parameters.seed | parameters.seed | 0–2147483647；相同参数和种子仅产生相似结果 |
| model_specific | `parameters` 子对象与已生成参数合并；其余顶层键透传 | 示例：`{"parameters":{"seed":12345}}` |

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL（24 小时内有效）

## 请求示例

```json
{
  "model": "wan2.7-i2v",
  "input": {
    "prompt": "一只猫在草地上奔跑",
    "media": [
      {
        "type": "first_frame",
        "url": "https://xxxxxx"
      }
    ]
  },
  "parameters": {
    "resolution": "720P",
    "prompt_extend": true,
    "watermark": false,
    "duration": 5
  }
}
```

## 响应结构

创建任务响应（snake_case）：

```json
{"output":{
  "task_status":"PENDING",
  "task_id":"36598b68-c4f5-423c-92a1-2d144692c1d0"},
  "request_id":"e25956ba-fa12-9eda-8bcb-a04225e8ef70"}
```

查询任务结果：直连官方接口为 snake_case；也兼容网关返回的 camelCase：

```json
{"request_id":"c6b9559f-4c28-98b0-86ea-2ed499172652",
"model":"wan2.7-i2v",
"output":{"task_id":"36598b68-c4f5-423c-92a1-2d144692c1d0",
"task_status":"SUCCEEDED",
"submit_time":"2026-06-30 18:07:20.235",
"scheduled_time":"2026-06-30 18:07:20.275",
"end_time":"2026-06-30 18:10:08.044",
"orig_prompt":"...",
"video_url":"https://dashscope-a717.oss-accelerate.aliyuncs.com/...mp4?Expires=1782900606&..."},
"usage":{"duration":5,"input_video_duration":0,"output_video_duration":5,"video_count":1,"SR":720}
}
```

| 字段 | 说明 |
|------|------|
| `output.task_id` | 任务 ID（网关 camelCase 也兼容） |
| `output.task_status` | 任务状态：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`；`CANCELED` / `UNKNOWN` 等同于失败 |
| `output.video_url` | 生成的视频 URL（24 小时有效，MP4 / H.264） |
| `usage.duration` / `usage.output_video_duration` | 计费时长 / 输出时长（秒） |
| `usage.SR` | 输出分辨率档位，如 `720` 表示 720P |

> 本系列按秒计费、单价随分辨率分档，计费口径是 `usage.duration` + `usage.sr`，**不是** token。

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 必填输入 | 首帧图片，或一个续生源视频（`first_clip`） |
| 视频时长 | 显式 2–15 秒（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
