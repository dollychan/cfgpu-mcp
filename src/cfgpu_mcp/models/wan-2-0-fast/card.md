# WAN 2.0 Fast (Seedance 2.0 fast)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `wan-video-fast` | 
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit, video_extend, audio_generate, web_search |
| 成本档位 | 2/5 |
| 速度档位 | 4/5 |

## 与 WAN 2.0 的关系

**能力完全一致**，仅差异于品质与速度：

| 特性 | WAN 2.0 | WAN 2.0 Fast |
|------|---------|--------------|
| 品质定位 | 最高品质 | 成本优化 |
| 生成速度 | 较慢 | 更快 |
| 成本 | 较高 | 较低 |
| 能力 | 完全一致 | 完全一致 |

**推荐场景：**
- 追求最高品质 → 使用 `wan-video`
- 追求速度与成本优化 → 使用 `wan-video-fast`

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

## 参数说明

**参数与 `wan-2-0` 完全一致。**

详细参数说明请参考 [wan-2-0/card.md](../wan-2-0/card.md)。

## 示例

### 文生视频

```json
{
  "model": "wan-video-fast",
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
  "model": "wan-video-fast",
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
  "watermark": false
}
```

### 多模态参考生视频

```json
{
  "model": "wan-video-fast",
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
  "ratio": "16:9",
  "duration": 11,
  "watermark": false
}
```

### 联网搜索（仅文生视频）

```json
{
  "model": "wan-video-fast",
  "content": [
    {
      "type": "text",
      "text": "微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部。"
    }
  ],
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

与 `wan-2-0` 一致，详见 [wan-2-0/card.md](../wan-2-0/card.md)。

## 响应结构

与 `wan-2-0` 响应格式一致，`content.videoUrl` 为视频下载链接。

`wan-video-fast` 查询结果示例：

```json
{
  "id": "cgt-20260513110708-8c5wf",
  "model": "wan-video-fast",
  "status": "succeeded",
  "error": null,
  "createdAt": 1778641628,
  "updatedAt": 1778641776,
  "content": {
    "videoUrl": "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/...",
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

关键区别：`model` 字段为 `wan-video-fast`。其余字段结构与 `wan-2-0` 完全一致。


## 与统一 Schema 的映射

与 `wan-2-0` 映射一致，详见 [wan-2-0/card.md](../wan-2-0/card.md)。