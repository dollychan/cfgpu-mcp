# 万相 2.7 视频编辑 (wan2.7-videoedit)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| adapter_id | `wan-2-7-videoedit` |
| CFGPU 模型 ID | `wan2.7-videoedit` |
| 能力标签 | video_edit |
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
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

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
  "parameters": {"resolution": "720P", "duration": 5}
}
```

## 异步任务流程

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL（24 小时内有效）

## 响应结构

标准视频任务结构（同万相 2.7 系列），关键字段 `content.videoUrl`、`status`、`usage.totalTokens`。

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 必填输入 | 1 个源视频（reference_videos） |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
