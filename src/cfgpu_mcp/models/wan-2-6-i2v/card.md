# 万相 2.6 图生视频 (wan2.6-i2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.6-i2v` |
| 任务（tasks） | image_to_video, audio_driven_video |
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
| **audio_driven_video** | 可传入驱动音频（audio_url），让画面与音频同步 |

> input 使用扁平字段 `img_url`（首帧，必填）和 `audio_url`（可选），**不使用 media 数组**。不支持首尾帧、参考图片/视频。

## 参数说明

| 统一 Schema 字段 | wan2.6-i2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词 |
| negative_prompt | input.negative_prompt | 不希望出现的内容，最长 500 字符 |
| first_frame | input.img_url | 首帧图片 URL（必填） |
| reference_audios[0] | input.audio_url | 驱动音频 URL（可选，取第一个） |
| resolution | parameters.resolution | `720P` 或 `1080P`，默认 `1080P` |
| prompt_extend | parameters.prompt_extend | 是否在生成前用大语言模型扩写提示词，默认 `true` |
| watermark | parameters.watermark | 是否添加水印，默认 `false` |
| duration_seconds | parameters.duration | 整数 2–15 秒，默认 5，不支持 -1 智能时长 |
| model_specific.parameters.shot_type | parameters.shot_type | `single` 或 `multi`；仅 `prompt_extend=true` 时生效 |
| model_specific.parameters.seed | parameters.seed | 0–2147483647；相同参数和种子仅产生相似结果 |
| model_specific | `parameters` 子对象与已生成参数合并；其余顶层键透传 | 示例：`{"parameters":{"shot_type":"multi","seed":12345}}` |

> `parameters.audio` 仅适用于 `wan2.6-i2v-flash`，不会发送给本模型 `wan2.6-i2v`。音频同步应提供 `reference_audios[0]`，它会映射为 `input.audio_url`。

## 请求示例

```json
{
  "model": "wan2.6-i2v",
  "input": {
    "prompt": "一个由喷漆画成的少年从墙上活过来，用极快语速演唱英文 rap...",
    "negative_prompt": "模糊，低质量",
    "img_url": "https://.../rap.png",
    "audio_url": "https://.../rap.mp3"
  },
  "parameters": {
    "resolution": "720P",
    "prompt_extend": true,
    "shot_type": "multi",
    "watermark": false,
    "duration": 5,
    "seed": 12345
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

查询任务结果：直连官方接口为 snake_case；也兼容网关返回的 camelCase：

```json
{"request_id":"c6b9559f-4c28-98b0-86ea-2ed499172652",
"model":"wan2.6-i2v",
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
| 必填输入 | 首帧图片（first_frame → img_url） |
| 可选输入 | 单个驱动音频（reference_audios[0] → audio_url） |
| 视频时长 | 显式 2–15 秒（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
