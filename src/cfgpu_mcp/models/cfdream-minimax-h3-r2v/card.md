# MiniMax H3 参考素材生视频 (cfdream/minimax-h3-r2v)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `cfdream/minimax-h3-r2v` |
| 任务（tasks） | reference_to_video, video_edit, synced_audio_output, voice_reference_video |
| 成本档位 | 1/5 |
| 速度档位 | 1/5 |

MiniMax H3 的 `ref2va` 权重，由自建 comfy-gateway 提供（不是 CFGPU 平台模型）。
用参考素材（图 / 视频 / 音频）驱动生成：保持人物或物体的身份一致、复用一段动作、
或让口型对上一段给定的音频。

**至少要给一个 `reference_*`**，否则请改用 `cfdream/minimax-h3`。
**不接受 `first_frame` / `last_frame`** —— 那是另一套 `fl2va` 权重，两者不能混用。

## ★ 最容易踩的坑：每个参考素材都必须在 prompt 里被点名

这套权重**只使用 prompt 里点名的参考素材**；没点名的会被忽略，而且上游不报错 ——
你拿到的是一条纯文生视频，白烧一次 GPU。所以 MCP 在发出前就拒绝这种请求。

点名的写法：给每个素材一个句柄（放在与素材列表同序同长的 `image_refs` /
`video_refs` / `audio_refs` 里），在 prompt 里写 `[[句柄]]`，把它当名词嵌进句子：

> `"[[hero]] is walking through a neon-lit alley, camera follows from behind."`
> 配 `"reference_images": [...], "image_refs": ["hero"]`

MCP 会把 `[[hero]]` 换成这个模型要的写法再发出；不要自己写模型的标签或序号 ——
序号随素材顺序变，换模型时写法也会变。

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
3. **三档分辨率全开**，但耗时随像素数成倍涨（480p:720p:1080p ≈ 1:2.25:5，480p/5s 实测 ~96s），而这是一块串行的自建 GPU。默认 `480p`。
4. **不支持 `duration_seconds: -1`**。

## 参数说明

| 统一 Schema 字段 | 网关字段 | 映射说明 |
|------------------|----------|----------|
| prompt | prompt | **每个参考素材都要用 `[[句柄]]` 点名**，MCP 发出前渲染成模型的标签 |
| image_refs / video_refs / audio_refs | — | 与对应素材列表同序同长的句柄，只用于解析 `[[句柄]]`，不上行 |
| reference_images | reference_images | ≤9 |
| reference_videos | reference_videos | ≤3，24fps，单条 2–15s |
| reference_audios | reference_audios | ≤3 |
| duration_seconds | duration_seconds | 4–15，不支持 -1 |
| resolution | resolution | `480p` / `720p` / `1080p`，耗时约 1 : 2.25 : 5 |
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
  "prompt": "[[hero]] walks through a neon-lit alley at night, camera follows from behind, rain on the pavement",
  "reference_images": ["https://example.com/person.jpg"],
  "image_refs": ["hero"],
  "duration_seconds": 5,
  "resolution": "480p",
  "aspect_ratio": "16:9"
}
```

音频驱动（口型对上给定音轨）：

```json
{
  "model": "cfdream/minimax-h3-r2v",
  "prompt": "[[host]] is speaking to the camera, saying the words in [[line]]",
  "reference_images": ["https://example.com/portrait.jpg"],
  "image_refs": ["host"],
  "reference_audios": ["https://example.com/line.wav"],
  "audio_refs": ["line"],
  "duration_seconds": 5,
  "resolution": "480p"
}
```

音色克隆（锁定角色音色、说新台词）：上传目标人物的一段语音作为参考，锁定视频中生成角色的音色，让角色用这个声音说 prompt 里的新台词（`voice_reference_video`）。 prompt 要写明台词，并说明这段参考音频只提供音色。

```json
{
  "model": "cfdream/minimax-h3-r2v",
  "prompt": "[[nurse]] smiles at the camera and says: \"欢迎来到诊所，我是小姚。\" Her voice matches the timbre of [[voice]]; do not repeat the words in [[voice]].",
  "reference_images": ["https://example.com/portrait.jpg"],
  "image_refs": ["nurse"],
  "reference_audios": ["https://example.com/voice-sample.wav"],
  "audio_refs": ["voice"],
  "duration_seconds": 5,
  "resolution": "480p"
}
```

## 异步与超时

单卡串行，同一时刻只跑一个任务，排队时间可能远大于生成时间。
稳态 5s 视频约 96s GPU；**从 `cfdream/minimax-h3` 切换过来要换权重**，
那一单会额外多约 47s；720p / 1080p 按像素数成倍往上。本侧轮询上限 1500s，超时不取消任务 ——
网关那边跑完仍会落库，之后再用同一个 `request_id` 调 `task_status` 依然拿得到产物。

**本模型固定异步**（继承自 `cfdream/minimax-h3` 的 `force_async: true`）：`generate_video`
无论 `wait` 传什么都立刻返回回执，不必传 `wait`；凭 `request_id` 取产物，没有另一个
`task_id` 要等。

## 产物有效期

返回的是有签名的临时链接，默认 24h 过期，`expires_at` 是**真实过期时刻**
（不是"收到响应后 24h"）。需要长期保存请自行转存。
