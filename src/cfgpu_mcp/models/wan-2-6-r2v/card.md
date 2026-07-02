# 万相 2.6 参考生视频 (wan2.6-r2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan2.6-r2v` |
| 能力标签 | multi_modal_reference |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

万相 2.6 参考生视频模型：输入参考媒体（视频/图片）+ 文本提示词，生成视频。提示词中可用 character1 等引用参考主体。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 720P] | 统一计价 | 0.63 元 / 秒 |
| 分辨率 (720P, 无限] | 统一计价 | 1.05 元 / 秒 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **multi_modal_reference** | 参考视频/图片 + 文本生成视频 |

> input 使用扁平的 `reference_urls` 数组（视频/图片 URL，**无 type 标签**），不使用 media 数组。需至少 1 个参考 URL。

## 参数说明

| 统一 Schema 字段 | wan2.6-r2v 字段 | 映射说明 |
|------------------|-----------------|----------|
| prompt | input.prompt | 文本提示词，可引用 character1 等 |
| reference_videos + reference_images | input.reference_urls | 合并为一个 URL 列表（视频在前，图片在后） |
| resolution | parameters.resolution | 分辨率档位，大写后透传（720p → 720P） |
| duration_seconds | parameters.duration | 视频时长（秒），需显式指定（不支持 -1 智能时长） |
| model_specific | （顶层合并） | 透传额外参数 |

## 请求示例

```json
{
  "model": "wan2.6-r2v",
  "input": {
    "prompt": "character1在沙发上开心地看电影",
    "reference_urls": ["https://.../vace.mp4"]
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

标准视频任务结构，关键字段 `content.videoUrl`、`status`、`usage.totalTokens`。

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 必填输入 | 至少 1 个参考视频或参考图片 |
| 视频时长 | 显式指定（不支持 -1 智能时长） |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
