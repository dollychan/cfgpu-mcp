# MiniMax H3

## 基本信息

| 属性 | 值 |
|---|---|
| 模型名（`model` 参数） | `MiniMax-H3` |
| 任务类型 | video |
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, audio_generate |
| 成本档位 | 1/5 |
| 速度档位 | 3/5 |

## 计费

| `resolution` | 价格 |
|---|---|
| `720p` | 0.1 元 / 秒 |
| `1080p` | 0.5 元 / 秒 |

按秒计价，`duration_seconds` 直接决定花费：1080p / 15 秒一次约 8 元。

## 参数

| 参数 | 取值 | 说明 |
|---|---|---|
| `prompt` | 必填，非空 | 单条最多 7000 字符 |
| `duration_seconds` | 4–15 整数 | 省略时为 5 |
| `resolution` | `720p` \| `1080p` | 不支持 `480p` |
| `aspect_ratio` | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 见下 |
| `watermark` | 布尔，默认 `false` | AIGC 标识水印 |

模型固定生成原生音频（`audio_generate`）；`with_audio` 不属于本模型的接口参数，
传入 `false` 也无法关闭音频。`prompt_extend` 同样不属于接口参数，传了不会生效。

**`adaptive` 在文生视频下会被替换成 `16:9`，不会报错。** `adaptive` 是 schema 默认值，
含义是「你来定」，所以最朴素的 `generate_video(prompt=...)` 依然能路由到本模型；替换
结果会由 preflight（`validate_only`）在 `corrected_args` 里报出来。图生视频不发送画幅，
由输入图片推断；有参考素材时 `adaptive` 合法，按素材自身画幅走。

## 输入素材规则

- `first_frame`、`last_frame` 各最多 1 张，且 `last_frame` 必须与 `first_frame` 一起使用。
- `reference_images` 最多 9 张。
- `reference_videos` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- `reference_audios` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- 首尾帧输入与所有 `reference_*` 输入互斥。
- 参考视频、音频的总时长无法从 URL 本地判断，超过 15 秒时由上游返回参数错误。

媒体格式限制（请求体总大小 ≤ 64 MB，大文件请用公网 URL）：图片 JPG/JPEG/PNG/WEBP/
HEIC/HEIF，单文件 ≤ 30 MB，边长 [256, 5760] px，宽高比 [0.4, 2.5]；视频 MP4/MOV
（H.264/H.265 + AAC/MP3），单文件 ≤ 50 MB，帧率 [23.976, 60]；音频 WAV/MP3，
单文件 ≤ 15 MB。

## 路由

本模型是 `model="auto"` 在 **balanced 一档**的视频默认落点。`fast` 归
`doubao-seedance-2-0-fast`，`best` 归 `doubao-seedance-2-5`。请求超出本模型能力时
（`480p`、超过 15 秒、首尾帧与参考素材混用等）自动让位给下一顺位模型。

## 调用示例

```json
{
  "prompt": "A cinematic shot of a train crossing a snowy valley at golden hour.",
  "model": "MiniMax-H3",
  "resolution": "720p",
  "duration_seconds": 5,
  "aspect_ratio": "16:9"
}
```

异步模型：`generate_video` 返回 `task_id`，用 `task_status` / `task_wait` 取结果；
`wait=True` 时服务端代为轮询（默认上限 900 秒）。

## 错误

参数错误多为 `prompt` 为空、`duration_seconds` 越界、`resolution` 传了 `480p`，
或首尾帧与参考素材同时给出；此外还有鉴权失败、余额不足、内容审核拒绝、限流。
错误原因原样透传，`error_type` 与 `retryable` 已按类型归一。
