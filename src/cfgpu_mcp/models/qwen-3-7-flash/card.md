# Qwen3.7-Flash

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | understand（视觉理解 / 图像推理 / 视频理解） |
| CFGPU 模型 ID | `qwen3.7-flash` |
| 调用方式 | 同步（POST `/model/v1/chat/completions` 直接返回结果） |

Qwen3.7-Flash 使用与 Qwen3.6-Plus 相同的视觉理解请求结构：统一的 `prompt`、`images`、
`video`、`system_prompt`、`max_tokens`、`temperature` 和 `model_specific` 参数映射到
OpenAI 兼容的 chat/completions 请求。调用返回文本结果，而不是图片或视频文件。

## 请求体

请求体结构与 `qwen3.6-plus` 一致；唯一的模型选择字段为：

```json
{
  "model": "qwen3.7-flash",
  "messages": [],
  "stream": false
}
```
