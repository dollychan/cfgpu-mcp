# Doubao Seedream 5.0 Pro

Seedream 5.0 Pro 是字节跳动发布的最新图像创作模型，将图像创作推进到可控生产的新阶段。本次更新的主要亮点是**编辑更可控、生产更落地、效果更自然**。模型为同步图像生成模型，支持文生图、单图生图、多图生图（2–10 张参考图生成单张图片）。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `doubao-seedream-5-0-pro` |
| 同步/异步 | 同步（`is_async: false`，POST 响应即返回结果） |
| 能力标签 | text_to_image, image_to_image, multi_image_fusion |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 1K] | 统一计价 | 0.4 元 / 张 |
| 分辨率 (1K, 无限] | 统一计价 | 0.8 元 / 张 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_image** | 纯文本生成单张图像 |
| **image_to_image** | 单张参考图 + 文本生成图像 |
| **multi_image_fusion** | 多张参考图片（2–10）+ 文本提示词生成单张图片 |

### 不支持的能力

- ❌ **组图生成**（`sequential_image_generation` 不可用；模型仅生成单张图片）
- ❌ **联网搜索**（不支持 `tools.type: web_search`）
- ❌ **流式输出**（不支持 `stream: true`）

> 如需组图、联网搜索或流式输出，请使用 `doubao-seedream-5-0-lite`。

## 参数说明

### 核心参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | ✓ | - | 图像描述，支持中英文 |
| model | string | ✓ | - | 模型 ID：`doubao-seedream-5-0-pro` |

### 尺寸参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| size | string | - | 1024x1024 | 生成图像尺寸，两种方式见下方详细说明 |

支持以下两种指定方式，**不可混用**：

#### 方式 1 — 精确像素（宽 x 高）

直接指定生成图像的宽高像素值。

| 项 | 取值 |
|----|------|
| 默认值 | `1024x1024` |
| 总像素取值范围 | [921600, 4624220]，即 [1280x720, 2048x2048×1.1025] |
| 宽高比取值范围 | [1/16, 16] |
| 格式 | `<宽>x<高>`，如 `2048x1024` |

> 说明：总像素是对单张图宽度和高度的像素乘积限制，而非对宽度或高度单独值的限制。需同时满足总像素范围和宽高比范围。

**有效示例**：`2048x1024` — 总像素 2,097,152，在 [921600, 4624220] 内；宽高比 2，在 [1/16, 16] 内。

**无效示例**：`512x512` — 总像素 262,144，低于 921,600 下限。

#### 方式 2 — 分辨率档位

指定分辨率档位，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，由模型判断生成图片大小。

| 档位 | 说明 |
|------|------|
| 1K | 模型自动判断宽高比（无固定像素对照表） |
| 2K | 固定宽高像素对照表见下方 |

采用方式 2 并在 prompt 中描述特定宽高比时，模型实际映射的宽高像素参考值（仅 2K）：

| 分辨率 | 宽高比 | 宽高像素值 |
|--------|--------|------------|
| 2K | 1:1 | 2048x2048 |
| 2K | 4:3 | 2304x1728 |
| 2K | 3:4 | 1728x2304 |
| 2K | 16:9 | 2848x1600 |
| 2K | 9:16 | 1600x2848 |
| 2K | 3:2 | 2496x1664 |
| 2K | 2:3 | 1664x2496 |
| 2K | 21:9 | 3136x1344 |

### 参考图参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image | string/array | - | - | 参考图片，支持 URL 或 Base64；多图生图需 2–10 张 |

### 输出控制参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| response_format | string | - | url | 返回格式：url 或 b64_json |
| watermark | boolean | - | true | 是否添加"AI生成"水印 |

## 示例

### 文生图

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/images/generations' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer <API_KEY>' \
--data '{
    "model": "doubao-seedream-5-0-pro",
    "prompt": "星际穿越，黑洞，黑洞里冲出一辆快支离破碎的复古列车，抢视觉冲击力，电影大片，末日既视感，动感，对比色，oc渲染，光线追踪，动态模糊，景深，超现实主义，深蓝，暗黑风背景的光影效果营造出氛围，整体兼具艺术幻想感，夸张的广角透视效果，耀光，反射，极致的光影，强引力，吞噬",
    "sequential_image_generation": "disabled",
    "response_format": "url",
    "size": "2K",
    "stream": false,
    "watermark": true
}'
```

### 图生图（单张参考）

```json
{
  "model": "doubao-seedream-5-0-pro",
  "prompt": "将这张图片转换为油画风格",
  "image": "https://example.com/cat.jpg",
  "size": "2K",
  "response_format": "url",
  "watermark": false
}
```

### 多图生图

```json
{
  "model": "doubao-seedream-5-0-pro",
  "prompt": "融合这些图片的风格，创作一幅山水画",
  "image": [
    "https://example.com/ref1.jpg",
    "https://example.com/ref2.jpg"
  ],
  "size": "2K",
  "response_format": "url",
  "watermark": false
}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 组图生成 | ❌ 不支持（`sequential_image_generation` 不可用） |
| 联网搜索 | ❌ 不支持 |
| 流式输出 | ❌ 不支持 |
| 参考图数量上限 | 10 张（多图生图需 2–10 张） |
| 总像素范围 | [921600, 4624220]（约 [1280x720, 2048x2048×1.1025]） |
| 宽高比范围 | [1/16, 16] |
| 分辨率档位 | 1K、2K |
| 输出图片链接有效期 | 24 小时 |

## 响应结构

```json
{
  "model": "doubao-seedream-5-0-pro",
  "created": 1757321139,
  "data": [
    {
      "url": "https://...",
      "size": "2048x2048"
    }
  ],
  "usage": {
    "generated_images": 1,
    "output_tokens": 0,
    "total_tokens": 0
  }
}
```

## 错误处理

| 错误类型 | 原因 | 建议 |
|----------|------|------|
| content_blocked | Prompt 或图片包含敏感内容 | 修改内容，避免敏感词 |
| invalid_params | 参数超出范围（如 size 总像素超出 [921600, 4624220]） | 检查 size、image 数量是否符合约束 |
| image_download_failed | 参考图 URL 无法访问 | 确保 URL 公网可访问 |
| quota_exceeded | 配额不足 | 检查账户余额 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Seedream Pro 字段 | 映射说明 |
|------------------|-------------------|----------|
| prompt | prompt | 直接映射 |
| aspect_ratio | size（方式 1） | 组合进 size 像素值；或在方式 2 作为提示词辅助，模型自动判断 |
| resolution | size | 1K/2K 档位映射；1K 透传为 `"1K"`（无固定像素表），2K 映射为精确像素 |
| reference_images | image | URL 数组；单张为 string，多张为 array（2–10） |
| n | — | Pro 仅支持 n=1；n>1 会报错拒绝（不支持组图） |
| watermark | watermark | 直接映射 |
| model_specific | - | 可传入 response_format 等额外字段 |
