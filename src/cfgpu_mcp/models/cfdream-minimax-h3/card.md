# MiniMax H3 (cfdream/minimax-h3)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `cfdream/minimax-h3` |
| 能力标签 | text_to_video, image_to_video, first_last_frame |
| 成本档位 | 1/5 |
| 速度档位 | 1/5 |

MiniMax H3 本地权重，由自建 comfy-gateway 提供（不是 CFGPU 平台模型）。
一个 model id 覆盖三种场景，用的是同一套 `fl2va` 权重、同一个节点：

- 不传首尾帧 → 文生视频
- 只传 `first_frame` → 图生视频
- 同时传 `first_frame` + `last_frame` → 首尾帧生视频

**原生带声音**：模型直接生成立体声音轨，不是后期配音。`with_audio: false` 才关掉。

**参考素材请改用 `cfdream/minimax-h3-r2v`** —— 那是另一套 `ref2va` 权重，
两者不能混用，所以是两个 model id 而不是一个模型的两种模式。本模型传
`reference_images` / `reference_videos` / `reference_audios` 会被直接拒绝。

## 三件必须知道的事

这三条都是**不报错的坑**：不写就是静默降级，跑完了才发现内容不对。

### 1. 实际时长 ≠ 请求时长

帧数被量化到 `length ≡ 5 (mod 17)`，fps 固定 24。所以：

| `duration_seconds` | 实际帧数 | 实际时长 |
|---|---|---|
| 4 | 107 → 抬到 124（低于训练区间下限） | 5.17s |
| 5 | 124 | **5.17s** |
| 6 | 158 | 6.58s |
| 10 | 243 | 10.13s |
| 15 | 362 | 15.08s |

误差最大约 +0.68s（帧步长 17 ÷ 24fps），**永远只多不少**。要精确时长请自己裁剪。
注意 4s 和 5s 出的是同一条片子长度：训练区间下限就是 124 帧。
成功响应的 `usage` 里带回实际值（`length` / `fps` / `actual_duration`），
以及运行时算出的真实 `width` / `height` —— 请以那里为准，不要以请求为准。

### 2. `seed` 用来复现

不传 → 网关每次随机（**必须随机**：H3 参考图生效与否对 seed 敏感，
写死会把偶发失败变成确定性失败）。
成功响应里 `seed` 一定回传。想复现同一个结果：

```json
{"model_specific": {"seed": 4667556858703757508}}
```

`seed` 在网关那边是顶层字段，`model_specific` 会被平铺上去，不用自己嵌套。

### 3. 分辨率越高越慢，而且是成倍的

`480p` / `720p` / `1080p` 三档全部开放（2026-08-14；此前只开 480p，那是标定状态
不是能力上限）。实际 `width` × `height` 由网关在运行时按 0.4 / 0.9 / 2.0 MP
配合 `aspect_ratio` 算出，以响应 `usage` 里的值为准 —— 480p @ 16:9 是 864×480。

**耗时随像素数走**：480p 的 5s 视频实测约 96s GPU，720p 约 2.25 倍，1080p 约 5 倍。
1080p 配上 15s 时长在分钟量级的高位（~20min）。这是一块**串行**的自建 GPU，
没有并发 —— 高档位请求会同时占住后面排队的所有人。默认给 `480p`，
确有画质需要再往上抬。

## 参数说明

| 统一 Schema 字段 | 网关字段 | 映射说明 |
|------------------|----------|----------|
| prompt | prompt | 文本提示词 |
| first_frame | first_frame | 可选。图生视频的首帧 |
| last_frame | last_frame | 可选。与 first_frame 同用则是首尾帧生视频 |
| duration_seconds | duration_seconds | 4–15，**不支持 -1 智能时长**。见上文量化说明 |
| resolution | resolution | `480p` / `720p` / `1080p`，耗时约 1 : 2.25 : 5 |
| aspect_ratio | aspect_ratio | `adaptive` 等价于 `16:9` |
| with_audio | with_audio | 默认 true，原生立体声 |
| model_specific | （顶层合并） | `seed` 走这里；另见下方调参旋钮 |
| quality_tier / watermark | — | 网关无对应实现，静默忽略 |

素材 URL 由**网关**下载（不是模型直接取），必须是公网可达的 https 直链；
私网 / 内网地址会被拦截。下载失败是**异步**失败，出现在 `task_status` 而不是提交时。

### model_specific 里的调参旋钮

标定期用，默认值已经是官方图的取值，一般不需要动：

| 键 | 默认 | 说明 |
|---|---|---|
| `seed` | 随机 | 见上文 |
| `steps` | 20 | 采样步数 |
| `scheduler` | `simple` | 官方图用的就是 simple |

## 请求示例

```json
{
  "model": "cfdream/minimax-h3",
  "prompt": "waves crashing on a rocky shore at dusk, slow camera push-in",
  "duration_seconds": 5,
  "resolution": "480p",
  "aspect_ratio": "16:9",
  "with_audio": true
}
```

首尾帧：

```json
{
  "model": "cfdream/minimax-h3",
  "prompt": "the flower blooms",
  "first_frame": "https://example.com/bud.jpg",
  "last_frame": "https://example.com/bloom.jpg",
  "duration_seconds": 5,
  "resolution": "480p"
}
```

## 异步与超时

异步模型：`generate_video` 默认 `wait=true` 会替你轮询；`wait=false` 则拿
`task_id` 后自行 `task_status` / `task_wait`。

单卡串行，同一时刻只跑一个任务，所以**排队时间可能远大于生成时间**。
稳态 5s 视频 @480p 约 96s GPU，冷启动（换权重）再加约 47s；720p / 1080p 按像素数
成倍往上（约 2.25× / 5×）。本侧轮询上限 1500s —— 这个数是为最贵的组合
（1080p + 15s，外推 ~20min）留的，不是常态耗时。

超过 1500s 本侧会报 timeout，但**任务不会被取消** —— 网关那边跑完仍会落库，
之后再 `task_status` 同一个 `task_id` 依然拿得到产物。

## 产物有效期

返回的是有签名的临时链接，默认 24h 过期，`expires_at` 是**真实过期时刻**
（不是"收到响应后 24h"）。需要长期保存请自行转存。
