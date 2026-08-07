# 万相 2.7 参考生视频 (wan2.7-r2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.7-r2v` |
| 能力标签 | multi_modal_reference |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.7 参考生视频模型：输入参考视频和/或参考图片 + 文本提示词，生成视频。提示词中可用「视频1」「视频2」「图片3」等引用各参考媒体。

## 价格

按视频时长（秒）计费，单价随输出分辨率分档：

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **multi_modal_reference** | 参考视频 + 参考图片 + 文本生成视频 |

> 需至少提供 1 个参考视频或参考图片。不支持首帧/尾帧、纯文生视频、参考音频。

## 参数说明

| 统一 Schema 字段 | wan2.7-r2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词，可引用「视频N」「图片N」 |
| reference_videos | input.media[]（type=reference_video） | 参考视频 URL 数组 |
| reference_images | input.media[]（type=reference_image） | 参考图片 URL 数组 |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

> media 数组顺序：先参考视频，后参考图片（与提示词中的引用序号对应）。

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL（24 小时内有效）

## 请求示例

```json
{
  "model": "wan2.7-r2v",
  "input": {
    "prompt": "视频2抱着图片3在咖啡厅里弹奏一支舒缓的美式乡村民谣，视频1笑着看着视频2",
    "media": [
      {
        "type": "reference_video",
        "url": "https://.../wan-r2v-role1.mp4"
      },
      {
        "type": "reference_video",
        "url": "https://.../wan-r2v-role2.mp4"
      },
      {
        "type": "reference_image",
        "url": "https://.../wan-r2v-object4.png"
      }
    ]
  },
  "parameters": {
    "resolution": "720P",
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

查询任务结果 GET `/video/tasks/{task_id}`（camelCase）：

```json
{"requestId":"c6b9559f-4c28-98b0-86ea-2ed499172652",
"model":"wan2.7-r2v",
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
| 必填输入 | 至少 1 个参考视频或参考图片 |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
