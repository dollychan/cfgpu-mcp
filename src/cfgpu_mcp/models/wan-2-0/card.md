# WAN 2.0 (Seedance 2.0)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan-video` | 
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit, video_extend, audio_generate, web_search |
| 成本档位 | 3/5 |
| 速度档位 | 2/5 |

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频 |
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **first_last_frame** | 首帧 + 尾帧图片 + 文本生成视频（精准控制起止画面） |
| **multi_modal_reference** | 多模态参考生视频：图片(0-9) + 视频(0-3) + 音频(0-3) + 文本 |
| **video_edit** | 基于参考视频进行编辑（替换元素、修改内容） |
| **video_extend** | 延长已有视频时长 |
| **audio_generate** | 生成与画面同步的有声视频（人声、音效、背景音乐） |
| **web_search** | 联网搜索增强（仅文生视频支持） |

## 模型版本对比

| 模型 | CFGPU Model ID | 特点 |
|------|----------------|------|
| WAN 2.0 | `wan-video` | 最高品质，全能力支持 |
| WAN 2.0 Fast | `wan-video-fast` | 更快速度，成本优化 |

**注意：** 推荐追求品质用 WAN 2.0，追求速度/成本用 WAN 2.0 Fast。

## 参数说明

### 核心参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 模型 ID |
| content | array | ✓ | - | 输入内容数组（文本、图片、视频、音频） |

### Content 输入类型

#### 文本信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✓ | 固定值：`text` |
| text | string | ✓ | 视频描述，支持中英文，建议中文≤500字，英文≤1000词 |

#### 图片信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✓ | 固定值：`image_url` |
| image_url.url | string | ✓ | 图片 URL、Base64 或素材 ID（`asset://<ASSET_ID>`） |
| role | string | 条件必填 | 图片用途：`first_frame`、`last_frame`、`reference_image` |

**图片输入要求：**
- 格式：jpeg, png, webp, bmp, tiff, gif
- 宽高比：(0.4, 2.5)
- 宽高像素：(300, 6000)
- 单张大小：≤ 30 MB
- 请求体大小：≤ 64 MB（大文件勿用 Base64）

**图片数量规则：**
| 场景 | 图片数量 | role 值 |
|------|----------|---------|
| 图生视频-首帧 | 1 张 | `first_frame` 或不填 |
| 图生视频-首尾帧 | 2 张 | 第一张 `first_frame`，第二张 `last_frame`（均必填） |
| 多模态参考 | 1-9 张 | 每张均为 `reference_image`（必填） |

**Base64 格式：**
```
data:image/<格式>;base64,<Base64编码>
```
如：`data:image/png;base64,{base64_image}`

#### 视频信息（仅 Seedance 2.0/2.0 fast）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✓ | 固定值：`video_url` |
| video_url.url | string | ✓ | 视频 URL 或素材 ID |
| role | string | 条件必填 | 固定值：`reference_video` |

**视频输入要求：**
- 格式：mp4, mov
- 分辨率：480p, 720p
- 时长：[2, 15] 秒，最多 3 个视频，总时长 ≤ 15 秒
- 宽高比：[0.4, 2.5]
- 宽高像素：[300, 6000]
- 画面像素：[409600, 927408]
- 单个大小：≤ 50 MB
- 帧率：[24, 60]

#### 音频信息（仅 Seedance 2.0/2.0 fast）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✓ | 固定值：`audio_url` |
| audio_url.url | string | ✓ | 音频 URL、Base64 或素材 ID |
| role | string | 条件必填 | 固定值：`reference_audio` |

**音频输入要求：**
- 格式：wav, mp3
- 时长：[2, 15] 秒，最多 3 段，总时长 ≤ 15 秒
- 单个大小：≤ 15 MB

**重要：** 不可单独输入音频，必须至少包含 1 个参考视频或图片。

### 视频输出参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| resolution | string | - | 720p | 分辨率：480p、720p 或 1080p |
| ratio | string | - | adaptive | 宽高比，详见下方表格 |
| duration | integer | - | 5 | 时长（秒）：[4, 15] 或 -1（智能） |
| generate_audio | boolean | - | true | 是否生成有声视频 |

**ratio 可选值：**
| 值 | 说明 |
|-----|------|
| 16:9 | 标准宽屏 |
| 4:3 | 传统比例 |
| 1:1 | 正方形 |
| 3:4 | 竖版 |
| 9:16 | 手机竖屏 |
| 21:9 | 电影宽屏 |
| adaptive | 根据输入自动选择（推荐） |

**adaptive 适配规则：**
- 文生视频：根据提示词智能选择
- 首帧/首尾帧：根据首帧图片比例自动选择
- 多模态参考：根据意图判断，优先级：视频 > 图片

**分辨率与宽高比像素对照：**

| 分辨率 | 宽高比 | 宽高像素值 |
|--------|--------|------------|
| 480p | 16:9 | 864×496 |
| 480p | 4:3 | 752×560 |
| 480p | 1:1 | 640×640 |
| 480p | 3:4 | 560×752 |
| 480p | 9:16 | 496×864 |
| 480p | 21:9 | 992×432 |
| 720p | 16:9 | 1280×720 |
| 720p | 4:3 | 1112×834 |
| 720p | 1:1 | 960×960 |
| 720p | 3:4 | 834×1112 |
| 720p | 9:16 | 720×1280 |
| 720p | 21:9 | 1470×630 |
| 1080p | 16:9 | 1920×1080 |
| 1080p | 4:3 | 1664×1248 |
| 1080p | 1:1 | 1440×1440 |
| 1080p | 3:4 | 1248×1664 |
| 1080p | 9:16 | 1080×1920 |
| 1080p | 21:9 | 2240×960 |

### 联网搜索参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| tools.type | string | - | - | 工具类型：`web_search` |

**说明：**
- 仅文生视频支持联网搜索
- 模型自主判断是否搜索（商品、天气等）
- 实际搜索次数通过 `usage.tool_usage.web_search` 返回
- 会增加时延

### 其他参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| watermark | boolean | - | true | 是否添加水印 |

## Prompt 优化建议

### 语言支持
- 支持中英文
- 建议中文 ≤ 500 字，英文 ≤ 1000 词

### 结构建议

**文生视频 Prompt：**
1. **主体描述**：明确主要对象
2. **动作描述**：描述运动方式、变化过程
3. **镜头语言**：推镜、拉镜、平移、环绕、跟随
4. **氛围/风格**：光影、色调、艺术风格

**镜头语言示例：**
| 中文 | 英文 | 效果 |
|------|------|------|
| 镜头逐渐拉近 | camera zooms in | 聚焦主体 |
| 360度环绕运镜 | 360 orbit shot | 展示全貌 |
| 第一人称视角 | POV shot | 身临其境 |
| 镜头向左平移 | pan left | 展示环境 |

### 有声视频 Prompt 建议

将对话内容用双引号标注，优化音频生成效果：

```
男人叫住女人说："你记住，以后不可以用手指指月亮。"
```

模型会自动生成匹配的人声、音效和背景音乐。

## 示例

### 文生视频

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上，花瓣上有几颗晶莹的露珠"
    }
  ],
  "ratio": "16:9",
  "duration": 5,
  "watermark": false
}
```

### 图生视频（首帧）

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "镜头缓慢推进，猫咪慢慢睁开眼睛"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/cat.jpg"
      },
      "role": "first_frame"
    }
  ],
  "ratio": "adaptive",
  "duration": 5,
  "generate_audio": true,
  "watermark": false
}
```

### 图生视频（首尾帧）

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "一只猫咪从睡姿变成站姿"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/cat_sleep.jpg"
      },
      "role": "first_frame"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/cat_stand.jpg"
      },
      "role": "last_frame"
    }
  ],
  "ratio": "adaptive",
  "duration": 5,
  "generate_audio": false,
  "watermark": false
}
```

### 多模态参考生视频

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐。第一人称视角果茶宣传广告..."
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/pic1.jpg"
      },
      "role": "reference_image"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/pic2.jpg"
      },
      "role": "reference_image"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/video1.mp4"
      },
      "role": "reference_video"
    },
    {
      "type": "audio_url",
      "audio_url": {
        "url": "https://example.com/audio1.mp3"
      },
      "role": "reference_audio"
    }
  ],
  "generate_audio": true,
  "ratio": "16:9",
  "duration": 11,
  "watermark": false
}
```

### 编辑视频

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "将视频1礼盒中的香水替换成图片1中的面霜，运镜不变"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/cream.jpg"
      },
      "role": "reference_image"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/perfume_video.mp4"
      },
      "role": "reference_video"
    }
  ],
  "generate_audio": true,
  "ratio": "16:9",
  "duration": 5,
  "watermark": true
}
```

### 延长视频

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "视频1中的拱形窗户打开，进入美术馆室内，接视频2，之后镜头进入画内，接视频3"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/video1.mp4"
      },
      "role": "reference_video"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/video2.mp4"
      },
      "role": "reference_video"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/video3.mp4"
      },
      "role": "reference_video"
    }
  ],
  "generate_audio": true,
  "ratio": "16:9",
  "duration": 8,
  "watermark": true
}
```

### 联网搜索（仅文生视频）

```json
{
  "model": "wan-video",
  "content": [
    {
      "type": "text",
      "text": "微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部，一颗鲜红的心脏正在有力地、规律地收缩扩张。"
    }
  ],
  "generate_audio": true,
  "ratio": "16:9",
  "duration": 11,
  "watermark": true,
  "tools": [
    {
      "type": "web_search"
    }
  ]
}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| Prompt 最大长度 | 500 汉字 / 1000 英文词 |
| 视频时长范围 | [4, 15] 秒（Seedance 2.0） |
| 视频时长范围 | [4, 12] 秒（Seedance 2.0 fast） |
| 分辨率选项 | 480p, 720p, 1080p |
| 参考图片数量上限 | 9 张 |
| 参考视频数量上限 | 3 个，总时长 ≤ 15 秒 |
| 参考音频数量上限 | 3 段，总时长 ≤ 15 秒 |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
| 在线推理 RPM | 600 |
| 在线推理并发数 | 10 |

**场景互斥：**
- 图生视频-首帧、图生视频-首尾帧、多模态参考生视频是三种互斥场景，不可混用

## 异步任务流程

Seedance 为异步接口，需轮询查询状态：

1. **创建任务**：POST `/video/generations`，返回 `task_id`
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回视频 URL，24 小时内下载

## 响应结构

查询任务结果 GET `/video/tasks/{task_id}` 返回：

```json
{
  "id": "cgt-xxx-8c5wf",
  "model": "wan-video",
  "status": "succeeded",
  "error": null,
  "createdAt": 1778641628,
  "updatedAt": 1778641776,
  "content": {
    "videoUrl": "https://...",
    "lastFrameUrl": null
  },
  "seed": 15233,
  "resolution": "720p",
  "ratio": "9:16",
  "duration": 5,
  "frames": null,
  "framesPerSecond": 24,
  "generateAudio": false,
  "draft": false,
  "draftTaskId": null,
  "usage": {
    "completionTokens": 108900,
    "totalTokens": 108900
  },
  "completionTokens": null,
  "totalTokens": null
}
```

**关键字段说明：**
| 字段 | 说明 |
|------|------|
| `id` | 任务 ID |
| `status` | 任务状态：`pending` / `running` / `succeeded` / `failed` |
| `content.videoUrl` | 生成的视频 URL（24 小时有效） |
| `content.lastFrameUrl` | 尾帧 URL（如有） |
| `seed` | 生成种子 |
| `resolution` / `ratio` / `duration` | 实际输出参数 |
| `usage.totalTokens` | Token 消耗 |

## 错误处理

| 错误类型 | 原因 | 建议 |
|----------|------|------|
| content_blocked | Prompt 或媒体包含敏感内容 | 修改内容，避免敏感词 |
| invalid_params | 参数超出范围 | 检查 duration、resolution、ratio 是否符合约束 |
| media_download_failed | 参考媒体 URL 无法访问 | 确保 URL 公网可访问 |
| quota_exceeded | 配额不足 | 检查账户余额 |
| audio_only | 只传入音频，无图片/视频 | 至少包含 1 个参考视频或图片 |
| mixed_scenarios | 混用首帧/首尾帧/多模态参考 | 选择一种场景，不可混用 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Seedance 字段 | 映射说明 |
|------------------|----------------|----------|
| prompt | content[].text | 文本部分 |
| first_frame | content[].image_url（role=first_frame） | 首帧图片 |
| last_frame | content[].image_url（role=last_frame） | 尾帧图片 |
| reference_images | content[].image_url（role=reference_image） | 参考图片数组 |
| duration_seconds | duration | 视频时长 |
| aspect_ratio | ratio | 宽高比 |
| resolution | resolution | 分辨率档位 |
| with_audio | generate_audio | 是否生成音频 |
| model_specific | - | 可传入 tools, watermark 等 |

**运动控制映射说明：**
- Seedance 不直接支持 `motion_intensity` 和 `camera_movement` 参数
- 建议将这些信息编入 prompt，如"镜头缓慢推进"、"动作幅度大"
- `camera_fixed` 参数在 Seedance 2.0/2.0 fast 中暂不支持