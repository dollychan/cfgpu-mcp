# 万相 2.7 文生视频 (wan2.7-t2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| adapter_id | `wan-2-7-t2v` |
| CFGPU 模型 ID | `wan2.7-t2v` |
| 能力标签 | text_to_video |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.7 文生视频模型：纯文本提示词生成视频，具备电影级叙事能力（可在提示词中分镜描述多个镜头）。

## 价格

按视频时长（秒）计费，单价随输出分辨率分档：

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频 |

> 仅文生视频。不支持首帧/尾帧、参考视频/图片/音频。

## 参数说明

| 统一 Schema 字段 | wan2.7-t2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词，支持分镜描述 |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

> 文生视频不带 `media` 数组。

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL（24 小时内有效）

## 请求示例

```json
{
  "model": "wan2.7-t2v",
  "input": {
    "prompt": "一段紧张刺激的侦探追查故事，展现电影级叙事能力。第1个镜头[0-3秒] 全景：雨夜的纽约街头，霓虹灯闪烁，一位身穿黑色风衣的侦探快步行走。..."
  },
  "parameters": {
    "resolution": "720P",
    "duration": 5
  }
}
```

## 响应结构
创建视频结构:

```json
{"output":{
  "task_status":"PENDING",
  "task_id":"36598b68-c4f5-423c-92a1-2d144692c1d0"},
  "request_id":"e25956ba-fa12-9eda-8bcb-a04225e8ef70"}
```

查询任务结果 GET `/video/tasks/{task_id}` 返回标准视频任务结构：

```json
{"requestId":"c6b9559f-4c28-98b0-86ea-2ed499172652",
"model":"wan2.7-t2v",
"output":{"taskId":"36598b68-c4f5-423c-92a1-2d144692c1d0",
"taskStatus":"SUCCEEDED","submitTime":"2026-06-30 18:07:20.235",
"scheduledTime":"2026-06-30 18:07:20.275",
"endTime":"2026-06-30 18:10:08.044",
"origPrompt":"一段紧张刺激的侦探追查故事，展现电影级叙事能力。第1个镜头[0-3秒] 全景：雨夜的纽约街头，霓虹灯闪烁，一位身穿黑色风衣的侦探快步行走。 第2个镜头[3-6秒] 中景：侦探进入一栋老旧建筑，雨水打湿了他的外套，门在他身后缓缓关闭。 第3个镜头[6-9秒] 特写：侦探的眼神坚毅专注，远处传来警笛声，他微微皱眉思考。 第4个镜头[9-12秒] 中景：侦探在昏暗走廊中小心前行，手电筒照亮前方。 第5个镜头[12-15秒] 特写：侦探发现关键线索，脸上露出恍然大悟的表情。",
"videoUrl":"https://dashscope-a717.oss-accelerate.aliyuncs.com/1d/84/20260630/3980f064/7904451-metadata_user_2e83035bf62f2589.mp4?Expires=1782900606&OSSAccessKeyId=LTAI5tJjG6wsHad1Sf7iezX4&Signature=c6%2FqN%2FjHNfu0pFWHsAfPhBbkveI%3D"},
"usage":{"duration":5,"inputVideoDuration":0,"outputVideoDuration":5,"videoCount":1,"sr":720,"ratio":"16:9"}
} 
```

| 字段 | 说明 |
|------|------|
| `output.taskId` | 任务 ID（创建响应为 snake_case `output.task_id`） |
| `output.taskStatus` | 任务状态：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`（创建响应为 `output.task_status`） |
| `output.videoUrl` | 生成的视频 URL（24 小时有效） |
| `usage.duration` / `usage.outputVideoDuration` | 计费时长（秒） |
| `usage.ratio` / `usage.sr` | 输出宽高比 / 分辨率 |

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 输入 | 仅文本提示词（无 media） |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |

## Prompt 优化建议

支持电影级分镜叙事，可按镜头编排：

```
第1个镜头[0-3秒] 全景：...
第2个镜头[3-6秒] 中景：...
第3个镜头[6-9秒] 特写：...
```
