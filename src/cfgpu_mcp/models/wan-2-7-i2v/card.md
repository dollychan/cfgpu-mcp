# 万相 2.7 图生视频 (wan2.7-i2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| adapter_id | `wan-2-7-i2v` |
| CFGPU 模型 ID | `wan2.7-i2v` |
| 能力标签 | image_to_video |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.7 图生视频模型：输入一张首帧图片 + 文本提示词，生成视频。

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

> 仅支持图生视频（首帧）。不支持纯文生视频、首尾帧、多模态参考媒体。

## 参数说明

| 统一 Schema 字段 | wan2.7-i2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词 |
| first_frame | input.media[]（type=first_frame） | 首帧图片 URL（必填） |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

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
    "duration": 5
  }
}
```

## 响应结构

查询任务结果 GET `/video/tasks/{task_id}` 返回标准视频任务结构：

```json
{
  "id": "cgt-xxx",
  "model": "wan2.7-i2v",
  "status": "succeeded",
  "content": {
    "videoUrl": "https://...",
    "lastFrameUrl": null
  },
  "seed": 15233,
  "resolution": "720p",
  "ratio": "9:16",
  "duration": 5,
  "usage": {
    "completionTokens": 108900,
    "totalTokens": 108900
  }
}
```

| 字段 | 说明 |
|------|------|
| `id` | 任务 ID |
| `status` | 任务状态：`pending` / `running` / `succeeded` / `failed` |
| `content.videoUrl` | 生成的视频 URL（24 小时有效） |
| `usage.totalTokens` | Token 消耗 |

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 必填输入 | 首帧图片（first_frame） |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
