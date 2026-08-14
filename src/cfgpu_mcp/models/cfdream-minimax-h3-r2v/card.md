# MiniMax H3 参考素材生视频 (cfdream/minimax-h3-r2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `cfdream/minimax-h3-r2v` |
| 能力标签 | reference_to_video, multi_modal_reference |
| 成本档位 | 1/5 |
| 速度档位 | 1/5 |

MiniMax H3 的 `ref2va` 权重，由自建 comfy-gateway 提供（不是 CFGPU 平台模型）。
用参考素材（图 / 视频 / 音频）驱动生成：保持人物或物体的身份一致、复用一段动作、
或让口型对上一段给定的音频。

**至少要给一个 `reference_*`**，否则请改用 `cfdream/minimax-h3`。
**不接受 `first_frame` / `last_frame`** —— 那是另一套 `fl2va` 权重，两者不能混用。

## ★ 最容易踩的坑：素材必须在 prompt 里被标签引用

传了参考素材却没在 `prompt` 里写标签，**模型不会用它们，而且不会报任何错**。
最后你拿到的是一条纯文生视频，白烧一次 GPU 还找不到原因。

标签是 1-based，**编号按数组顺序**：

| 参数 | 标签 |
|---|---|
| `reference_images[0]` | `<Picture 1>` |
| `reference_images[1]` | `<Picture 2>` |
| `reference_videos[0]` | `<Video 1>` |
| `reference_audios[0]` | `<Audio 1>` |

写法就是把标签当名词嵌进句子：

> `"<Picture 1> is walking through a neon-lit alley, camera follows from behind."`

网关检测到"有素材但 prompt 里缺对应标签"时只记 warning、**不拒绝**
（调用方可能有意只做弱引导），所以这条防线在你这边。

## 素材上限

| 参数 | 上限 | 附加要求 |
|---|---|---|
| `reference_images` | 9 | 公网 https 直链 |
| `reference_videos` | 3 | **必须 24fps，单条 2–15s** |
| `reference_audios` | 3 | |

参考视频的 24fps 是**硬要求**：网关本期不转码，不满足直接判失败。
上游节点不会替你重采样 —— 喂 30fps 进去不会报错，只会让时间轴悄悄错掉，
所以网关宁可拦下来。

素材由**网关**下载，必须公网可达；私网 / 内网地址会被拦截。
下载失败是**异步**失败，出现在 `task_status` 而不是提交时。

## 三件必须知道的事

与 `cfdream/minimax-h3` 完全相同，这里不重复推导，只列结论：

1. **实际时长 ≠ 请求时长**：帧数量化到 `≡5 (mod 17)`，24fps。5s → 5.17s，
   最大 +0.68s，永远只多不少。以响应 `usage` 里的 `actual_duration` /
   `width` / `height` 为准。
2. **`seed` 用 `model_specific: {"seed": N}` 复现**；不传则每次随机。
   **这个模型尤其重要**：H3 的参考图生效与否对 seed 敏感 —— 同一组素材换个 seed
   可能从"像"变成"不像"。拿到满意结果请立刻记下响应里的 `seed`。
3. **只开放了 480p**，且不支持 `duration_seconds: -1`。

## 参数说明

| 统一 Schema 字段 | 网关字段 | 映射说明 |
|------------------|----------|----------|
| prompt | prompt | **必须含 `<Picture N>` / `<Video N>` / `<Audio N>` 标签** |
| reference_images | reference_images | ≤9 |
| reference_videos | reference_videos | ≤3，24fps，单条 2–15s |
| reference_audios | reference_audios | ≤3 |
| duration_seconds | duration_seconds | 4–15，不支持 -1 |
| resolution | resolution | 当前只接受 `480p` |
| aspect_ratio | aspect_ratio | `adaptive` 等价于 `16:9` |
| with_audio | with_audio | 默认 true，原生立体声 |
| model_specific | （顶层合并） | `seed` / `ref_image_size` / `steps` / `scheduler` |
| first_frame / last_frame | — | **不接受**，请改用 `cfdream/minimax-h3` |
| quality_tier / watermark | — | 网关无对应实现，静默忽略 |

### model_specific 里的调参旋钮

| 键 | 默认 | 说明 |
|---|---|---|
| `seed` | 随机 | 见上文，这个模型上尤其值得记 |
| `ref_image_size` | `match` | 身份保真度旋钮。`max` 更保真但可能慢好几倍 |
| `steps` | 20 | 采样步数 |
| `scheduler` | `simple` | 官方图用的就是 simple |

## 请求示例

```json
{
  "model": "cfdream/minimax-h3-r2v",
  "prompt": "<Picture 1> walks through a neon-lit alley at night, camera follows from behind, rain on the pavement",
  "reference_images": ["https://example.com/person.jpg"],
  "duration_seconds": 5,
  "resolution": "480p",
  "aspect_ratio": "16:9"
}
```

音频驱动（口型对上给定音轨）：

```json
{
  "model": "cfdream/minimax-h3-r2v",
  "prompt": "<Picture 1> is speaking to the camera, saying the words in <Audio 1>",
  "reference_images": ["https://example.com/portrait.jpg"],
  "reference_audios": ["https://example.com/line.wav"],
  "duration_seconds": 5,
  "resolution": "480p"
}
```

## 异步与超时

单卡串行，同一时刻只跑一个任务，排队时间可能远大于生成时间。
稳态 5s 视频约 96s GPU；**从 `cfdream/minimax-h3` 切换过来要换权重**，
那一单会额外多约 47s。本侧轮询上限 900s，超时不取消任务 ——
网关那边跑完仍会落库，之后再 `task_status` 同一个 `task_id` 依然拿得到产物。

## 产物有效期

返回的是有签名的临时链接，默认 24h 过期，`expires_at` 是**真实过期时刻**
（不是"收到响应后 24h"）。需要长期保存请自行转存。
