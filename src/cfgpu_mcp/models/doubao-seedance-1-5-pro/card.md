# Doubao Seedance 1.5 Pro

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `doubao-seedance-1-5-pro-251215` |
| 能力标签 | text_to_video, image_to_video, first_last_frame, audio_generate, sample_mode |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 与 Seedance 2.0 的对比

| 特性 | Seedance 2.0 | Seedance 2.0 fast | Seedance 1.5 Pro |
|------|-------------|-------------------|-----------------|
| 文生视频 | ✅ | ✅ | ✅ |
| 图生视频-首帧 | ✅ | ✅ | ✅ |
| 图生视频-首尾帧 | ✅ | ✅ | ✅ |
| 多模态参考 | ✅ | ✅ | ❌ |
| 编辑视频 | ✅ | ✅ | ❌ |
| 延长视频 | ✅ | ✅ | ❌ |
| 生成有声视频 | ✅ | ✅ | ✅ |
| 联网搜索增强 | ✅ | ✅ | ❌ |
| 样片模式 | ❌ | ❌ | ✅ |
| 离线推理 | ❌ | ❌ | ✅ |
| 分辨率 | 480p, 720p, 1080p | 480p, 720p, 1080p | 480p, 720p, 1080p |
| 最大时长 | 15 秒 | 12 秒 | 12 秒 |

**推荐场景：**
- 需要 1080p 高清分辨率 → 使用 `doubao-seedance-1-5-pro`
- 需要样片模式（快速预览）→ 使用 `doubao-seedance-1-5-pro`
- 需要多模态参考/视频编辑/延长 → 使用 `wan-2-0`
- 追求最高质量全功能 → 使用 `wan-2-0`

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频 |
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **first_last_frame** | 首帧 + 尾帧图片 + 文本生成视频（精准控制起止画面） |
| **audio_generate** | 生成与画面同步的有声视频（人声、音效、背景音乐） |
| **sample_mode** | 样片模式：快速生成低质量预览，确认效果后再生成正式版 |

## 参数说明

### 核心参数

API 请求结构与 `wan-2-0` 一致（详见 [wan-2-0/card.md](../wan-2-0/card.md)），以下列出差异点。

### 视频输出参数（差异）

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| resolution | string | - | 720p | 分辨率：480p、720p 或 **1080p** |
| ratio | string | - | adaptive | 宽高比（与 wan-2-0 一致） |
| duration | integer | - | 5 | 时长（秒）：[4, 12] 或 -1（智能）|
| generate_audio | boolean | - | true | 是否生成有声视频 |

**分辨率新增 1080p：**

| 分辨率 | 宽高比 | 宽高像素值 |
|--------|--------|------------|
| 1080p | 16:9 | 1920×1080 |
| 1080p | 4:3 | 1664×1248 |
| 1080p | 1:1 | 1440×1440 |
| 1080p | 3:4 | 1248×1664 |
| 1080p | 9:16 | 1080×1920 |
| 1080p | 21:9 | 2240×960 |

### 样片模式参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| sample_mode | boolean | - | false | 是否启用样片模式（快速低质量预览） |

通过 `model_specific` 传入：

```json
{
  "model_specific": {
    "sample_mode": true
  }
}
```

### 不支持的参数

以下 `wan-2-0` 支持的输入类型在 1.5 Pro 中**不可使用**：
- `video_url`（reference_video）
- `audio_url`（reference_audio）
- `tools.type: web_search`

## 示例

### 文生视频（1080p）

```json
{
  "model": "doubao-seedance-1-5-pro-251215",
  "content": [
    {
      "type": "text",
      "text": "写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上"
    }
  ],
  "ratio": "16:9",
  "resolution": "1080p",
  "duration": 8,
  "generate_audio": true,
  "watermark": false
}
```

### 图生视频（首帧）

```json
{
  "model": "doubao-seedance-1-5-pro-251215",
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
  "resolution": "1080p",
  "duration": 5,
  "generate_audio": false,
  "watermark": false
}
```

### 图生视频（首尾帧）

```json
{
  "model": "doubao-seedance-1-5-pro-251215",
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
  "resolution": "720p",
  "duration": 5,
  "watermark": false
}
```

### 样片模式（快速预览）

```json
{
  "model": "doubao-seedance-1-5-pro-251215",
  "content": [
    {
      "type": "text",
      "text": "宇宙飞船穿越星云，镜头环绕飞行"
    }
  ],
  "ratio": "16:9",
  "resolution": "480p",
  "duration": 5,
  "sample_mode": true,
  "watermark": true
}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| Prompt 最大长度 | 500 汉字 / 1000 英文词 |
| 视频时长范围 | [4, 12] 秒 |
| 分辨率选项 | 480p, 720p, 1080p |
| 参考图片数量上限 | 2 张（仅首帧/首尾帧场景） |
| 多模态参考 | **不支持** |
| 视频/音频参考输入 | **不支持** |
| 输出视频格式 | mp4 |
| 视频链接有效期 | 24 小时 |
| 在线推理 RPM | 600 |
| 在线推理并发数 | 10 |
| 离线推理 TPD | 5000 亿 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Seedance 字段 | 映射说明 |
|------------------|----------------|----------|
| prompt | content[].text | 文本部分 |
| first_frame | content[].image_url（role=first_frame） | 首帧图片 |
| last_frame | content[].image_url（role=last_frame） | 尾帧图片 |
| duration_seconds | duration | 视频时长 |
| aspect_ratio | ratio | 宽高比 |
| resolution | resolution | 分辨率档位（支持 1080p） |
| with_audio | generate_audio | 是否生成音频 |
| model_specific | - | 可传入 sample_mode, watermark 等 |
| reference_images | **不支持** | 1.5 Pro 不支持多模态参考 |
| reference_videos | **不支持** | 1.5 Pro 不支持视频参考 |
| reference_audios | **不支持** | 1.5 Pro 不支持音频参考 |
