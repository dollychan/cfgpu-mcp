# Qwen3-VL 30B A3B Thinking

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | understand (视觉理解 / 图像推理 / 视频理解) |
| CFGPU 模型 ID | `qwen3-vl-30b-a3b-thinking` |
| 能力标签 | image_understanding, image_reasoning, video_understanding, long_video, long_document, tool_calling, visual_agent, long_context |
| 调用方式 | 同步（POST `/model/v1/chat/completions` 直接返回结果） |
| 上下文 | 128K |
| 成本档位 | 2/5 |
| 速度档位 | 4/5 |

Qwen3-VL 系列第二大 MoE 模型的 Thinking 版本，响应速度快，具备更强的多模态理解与推理、视觉智能体、长视频长文档等超长上下文支持能力；全面升级图像/视频理解、空间感知与万物识别能力，胜任复杂现实任务。返回的是**文本结果**（理解 / 推理 / 描述），不是图片或视频文件。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 输入长度 (0, 无限] 且输出长度 (0, 无限] | 输入 | 0.0005025 元 / K Tokens |
| | 输出 | 0.005025 元 / K Tokens |

## 参数说明

| 统一 Schema 字段 | chat/completions 映射 | 默认值 | 说明 |
|------------------|------------------------|--------|------|
| prompt | messages[user].content[].text | - | 对图像/视频的指令或问题（必填） |
| images | messages[user].content[].image_url.url | - | 公网图片 URL 列表，图像理解/推理 |
| video | messages[user].content[].video_url.url | - | 单个公网视频 URL，视频理解 |
| system_prompt | messages[system].content | `You are a helpful assistant.` | 系统提示词 |
| max_tokens | max_tokens | （模型默认） | 最大输出 token 数 |
| temperature | temperature | （模型默认） | 采样温度 |
| model_specific | （顶层合并） | - | 其他直传参数，如 top_p、tools |

> 返回结构：`choices[0].message.content` 为最终回答（映射为结果 `text`）；
> Thinking 版本另有 `choices[0].message.reasoning_content` 推理过程（映射为
> `reasoning`，在 `return_metadata=true` 时返回）。

## 示例

### 图像理解

```json
{
  "stream": false,
  "model": "qwen3-vl-30b-a3b-thinking",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片，并指出其中的异常之处。"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}
      ]
    }
  ]
}
```

### 视频理解

```json
{
  "stream": false,
  "model": "qwen3-vl-30b-a3b-thinking",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "请帮我详细描述这个视频里发生了什么，并列出关键事件的时间线。"},
        {"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4"}}
      ]
    }
  ]
}
```
