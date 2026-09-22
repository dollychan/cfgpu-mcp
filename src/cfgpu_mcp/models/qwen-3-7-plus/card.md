# Qwen3.7-Plus

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | understand（视觉理解 / 图像推理 / 视频理解） |
| CFGPU 模型 ID | `qwen3.7-plus` |
| 调用方式 | 同步（POST `/model/v1/chat/completions` 直接返回结果） |

Qwen3.7-Plus 使用 Qwen 家族统一的视觉理解请求结构：`prompt`、`images`、`video`、
`system_prompt`、`max_tokens`、`temperature` 与 `model_specific` 映射到 OpenAI 兼容的
chat/completions 请求。调用返回文本结果，而不是图片或视频文件。

## 请求体

请求体结构与其他 Qwen vision 模型一致；模型选择字段为：

```json
{
  "model": "qwen3.7-plus",
  "messages": [],
  "stream": false
}
```
