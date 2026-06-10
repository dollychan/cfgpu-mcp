# Doubao-Seedream-3.0-t2i

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `doubao-seedream-3-0-t2i-250415` |
| 能力标签 | text_to_image |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 价格

| 计费项 | 单价 |
|--------|------|
| 文生图 | 0.259 元 / 张 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_image** | 纯文本生成单张图像 |

## 参数说明

### 核心参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | ✓ | - | 图像描述，支持中英文 |
| model | string | ✓ | - | 模型 ID：`doubao-seedream-3-0-t2i-250415` |

### 尺寸参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| size | string | - | 2048x2048 | 生成图像尺寸，支持分辨率档位（2K/3K/4K）或精确像素 |

### 输出控制参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| response_format | string | - | url | 返回格式：url 或 b64_json |
| sequential_image_generation | string | - | disabled | 组图模式：auto 或 disabled |
| stream | boolean | - | false | 流式输出模式 |
| watermark | boolean | - | true | 是否添加"AI生成"水印 |

## 示例

### 文生图

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/images/generations' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer <API-TOKEN>' \
--data '{
    "model": "doubao-seedream-3-0-t2i-250415",
    "prompt": "星际穿越，黑洞，黑洞里冲出一辆快支离破碎的复古列车，抢视觉冲击力，电影大片，末日既视感，动感，对比色，oc渲染，光线追踪，动态模糊，景深，超现实主义，深蓝，画面通过细腻的丰富的色彩层次塑造主体与场景，质感真实，暗黑风背景的光影效果营造出氛围，整体兼具艺术幻想感，夸张的广角透视效果，耀光，反射，极致的光影，强引力，吞噬",
    "sequential_image_generation": "disabled",
    "response_format": "url",
    "size": "2K",
    "stream": false,
    "watermark": true
}'
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 输出图片链接有效期 | 24 小时 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Seedream 字段 | 映射说明 |
|------------------|----------------|----------|
| prompt | prompt | 直接映射 |
| resolution | size | 2K/3K/4K 档位映射 |
| model_specific | - | 可传入 sequential_image_generation, stream 等 |
