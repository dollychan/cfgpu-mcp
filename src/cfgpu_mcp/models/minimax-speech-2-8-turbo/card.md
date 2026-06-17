---
inherits: minimax-speech-2-8-hd
---

# MiniMax 语音 2.8 Turbo

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | audio (语音合成 / text-to-speech) |
| CFGPU 模型 ID | `MiniMax/speech-2.8-turbo` |
| 能力标签 | text_to_speech, emotion, pronunciation_dict |
| 调用方式 | 同步（POST 直接返回结果） |
| 成本档位 | 1/5 |
| 速度档位 | 4/5 |

Turbo 版本在 HD 的基础上进一步优化速度与成本，参数与请求结构完全一致，仅 `model` 字段不同。

## 价格

| 计费项 | 价格 |
|--------|------|
| 按字符数收费 | 0.21 元 / 万字符 |
