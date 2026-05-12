# Doubao Seedream 5.0 lite

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `doubao-seedream-5-0-260128` |
| 能力标签 | text_to_image, image_to_image, multi_image_fusion, multi_image_group, web_search |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_image** | 纯文本生成单张图像 |
| **image_to_image** | 单张参考图 + 文本生成图像 |
| **multi_image_fusion** | 多张参考图（2-14）+ 文本生成单张融合图像 |
| **multi_image_group** | 生成组图（内容关联的多张图片），支持文生组图、单图生组图、多图生组图 |
| **web_search** | 联网搜索增强，提升时效性内容生成质量 |

## 参数说明

### 核心参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | ✓ | - | 图像描述，支持中英文，建议不超过300汉字或600英文单词 |
| model | string | ✓ | - | 模型 ID：`doubao-seedream-5-0-260128` |

### 尺寸参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| size | string | - | 2048x2048 | 生成图像尺寸，两种方式见下方详细说明 |

**size 参数两种指定方式：**

**方式 1 - 分辨率档位**（模型自动判断宽高比）：
| 档位 | 说明 |
|------|------|
| 2K | 总像素约 368万-400万 |
| 3K | 总像素约 900万-940万 |
| 4K | 总像素约 1600万-1670万 |

**方式 2 - 精确像素**：
- 总像素范围：[3,686,400, 16,777,216]
- 宽高比范围：[1/16, 16]
- 格式：`<宽>x<高>`，如 `2048x2048`

**推荐像素值对照表：**

| 分辨率 | 宽高比 | 宽高像素值 |
|--------|--------|------------|
| 2K | 1:1 | 2048x2048 |
| 2K | 4:3 | 2304x1728 |
| 2K | 3:4 | 1728x2304 |
| 2K | 16:9 | 2848x1600 |
| 2K | 9:16 | 1600x2848 |
| 2K | 21:9 | 3136x1344 |
| 3K | 1:1 | 3072x3072 |
| 3K | 16:9 | 4096x2304 |
| 3K | 9:16 | 2304x4096 |
| 4K | 1:1 | 4096x4096 |
| 4K | 16:9 | 5504x3040 |
| 4K | 9:16 | 3040x5504 |

### 参考图参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image | string/array | - | - | 参考图片，支持 URL 或 Base64，最多 14 张 |

**图片输入要求：**
- 格式：jpeg, png, webp, bmp, tiff, gif, heic, heif
- 宽高比范围：[1/16, 16]
- 宽高像素：> 14px
- 单张大小：≤ 30MB
- 总像素：≤ 6000x6000 (36,000,000 px)
- 最多传入：14 张参考图

**Base64 格式：**
```
data:image/<格式>;base64,<Base64编码>
```
注意：格式需小写，如 `data:image/png;base64,{base64_image}`

### 组图参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| sequential_image_generation | string | - | disabled | 组图模式：auto（自动判断）或 disabled（关闭） |
| sequential_image_generation_options.max_images | integer | - | 15 | 组图最大数量，范围 [1, 15] |

**组图数量约束：**
- 输入参考图数量 + 生成的图片数量 ≤ 15 张

### 输出控制参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| response_format | string | - | url | 返回格式：url 或 b64_json |
| output_format | string | - | jpeg | 输出文件格式：jpeg 或 png |
| stream | boolean | - | false | 流式输出模式 |
| watermark | boolean | - | true | 是否添加"AI生成"水印 |

### 联网搜索参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tools.type | string | - | - | 工具类型：web_search |

**说明：**
- 开启联网搜索后，模型会自主判断是否搜索互联网内容（商品、天气等）
- 实际搜索次数通过 `usage.tool_usage.web_search` 返回
- 会增加一定时延

### 提示词优化参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| optimize_prompt_options.mode | string | - | standard | 优化模式：standard（高质量慢）或 fast（快速） |

## Prompt 优化建议

### 语言支持
- 支持中英文
- 建议不超过 300 汉字或 600 英文单词
- 字数过多会导致信息分散，模型可能忽略细节

### 结构建议
1. **主体描述**：明确主要对象是什么
2. **风格描述**：指明艺术风格（扁平化、写实、油画等）
3. **构图描述**：说明画面布局、视角
4. **细节描述**：补充颜色、光影、氛围等

### 示例 Prompt
```
制作一张上海未来5日的天气预报图，采用现代扁平化插画风格，
清晰展示每日天气、温度和穿搭建议。整体为横向排版，
标题为"上海未来5日天气预报"，包含5个等宽的垂直卡片，
从左到右依次排列。整体风格为现代、干净、友好的扁平化矢量插画风格，
线条清晰，色彩柔和。
```

## 示例

### 文生图

```json
{
  "model": "doubao-seedream-5-0-260128",
  "prompt": "一只可爱的猫咪在阳光下打盹，毛茸茸的，温馨氛围",
  "size": "2048x2048",
  "response_format": "url",
  "watermark": false
}
```

### 图生图（单张参考）

```json
{
  "model": "doubao-seedream-5-0-260128",
  "prompt": "将这张图片转换为油画风格",
  "image": "https://example.com/cat.jpg",
  "size": "2K",
  "response_format": "url",
  "watermark": false
}
```

### 多图融合

```json
{
  "model": "doubao-seedream-5-0-260128",
  "prompt": "融合这些图片的风格，创作一幅山水画",
  "image": [
    "https://example.com/ref1.jpg",
    "https://example.com/ref2.jpg",
    "https://example.com/ref3.jpg"
  ],
  "size": "3K",
  "response_format": "url",
  "watermark": false
}
```

### 联网搜索生图

```json
{
  "model": "doubao-seedream-5-0-260128",
  "prompt": "制作一张上海未来5日的天气预报图",
  "size": "2048x2048",
  "tools": [{"type": "web_search"}],
  "output_format": "png",
  "response_format": "url",
  "watermark": false
}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| Prompt 最大长度 | 300 汉字 / 600 英文单词 |
| 参考图数量上限 | 14 张 |
| 单张图片大小上限 | 30 MB |
| 总像素上限 | 6000x6000 (36,000,000 px) |
| 输出图片宽高比范围 | [1/16, 16] |
| 输出图片链接有效期 | 24 小时 |
| 组图最大数量 | 15 张（含参考图） |

## 响应结构

```json
{
  "model": "doubao-seedream-5-0-260128",
  "created": 1757321139,
  "data": [
    {
      "url": "https://...",
      "size": "3104x1312"
    }
  ],
  "usage": {
    "generated_images": 1,
    "output_tokens": xxx,
    "total_tokens": xxx,
    "tool_usage": {
      "web_search": 1
    }
  }
}
```

## 错误处理

| 错误类型 | 原因 | 建议 |
|----------|------|------|
| content_blocked | Prompt 或图片包含敏感内容 | 修改内容，避免敏感词 |
| invalid_params | 参数超出范围 | 检查 size、image 数量是否符合约束 |
| image_download_failed | 参考图 URL 无法访问 | 确保 URL 公网可访问 |
| quota_exceeded | 配额不足 | 检查账户余额 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Seedream 字段 | 映射说明 |
|------------------|----------------|----------|
| prompt | prompt | 直接映射 |
| aspect_ratio | size（方式1） | 作为提示词辅助，模型自动判断 |
| resolution | size | 2K/3K/4K 档位映射 |
| reference_images | image | URL 数组 |
| n | sequential_image_generation_options.max_images | 组图数量 |
| model_specific | - | 可传入 output_format, tools, optimize_prompt_options 等 |