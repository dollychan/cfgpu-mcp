# MiniMax H3 Turbo

## 基本信息

| 属性 | 值 |
|---|---|
| 模型名（`model` 参数） | `minimax-turbo-h3` |
| 任务类型 | video |
| 任务（tasks） | text_to_video, image_to_video, first_last_frame, reference_to_video, framed_reference_video, video_edit, synced_audio_output, voice_reference_video |
| 成本档位 | 1/5 |
| 速度档位 | 3/5 |

MiniMax H3 的降级版本。请求、查询格式与 `MiniMax-H3` 相同；目前唯一的参数差别是**不支持 `2k`**。

## 计费

按秒计费（输入视频、输出视频），`duration_seconds` 直接决定视频费用。具体单价待补充。

## 参数

| 参数 | 取值 | 说明 |
|---|---|---|
| `prompt` | 必填，非空 | 单条最多 7000 字符 |
| `duration_seconds` | 4–15 整数 | 省略时为 5；`480p` 最小为 5 秒 |
| `resolution` | `480p` \| `768p` | 省略时为 `768p`；**不支持 `2k`**，`720p` / `1080p` 同样不支持 |
| `aspect_ratio` | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 见下 |
| `watermark` | 布尔，默认 `false` | AIGC 标识水印 |

模型固定生成原生音频（`synced_audio_output`）；`with_audio` 不属于本模型的接口参数，
传入 `false` 也无法关闭音频。`prompt_extend` 同样不属于接口参数，传了不会生效。

`480p` / `768p` 是上游 MiniMax 的原生档位（`480P` / `768P`），本适配器只做大写。传 `2k`、`720p` 或
`1080p` 会**直接报错**并列出支持档位，不做替换。省略 `resolution` 时用 `768p`。

**`adaptive` 在文生视频下会被替换成 `16:9`，不会报错。** `adaptive` 是 schema 默认值，
含义是「你来定」，所以最朴素的 `generate_video(prompt=...)` 依然能路由到本模型；替换
结果会由 preflight（`validate_only`）在 `corrected_args` 里报出来。图生视频不发送画幅，
由输入图片推断；有参考素材时 `adaptive` 合法，按素材自身画幅走。

## 输入素材规则

- `first_frame`、`last_frame` 各最多 1 张，且 `last_frame` 必须与 `first_frame` 一起使用。
- `reference_images` 最多 9 张。
- `reference_videos` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- `reference_audios` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。**参考音频可用于音色克隆与锁定**：上传目标人物的一段语音作为参考，锁定视频中生成角色的音色，让角色用这个声音说 prompt 里的新台词（`voice_reference_video`）。
- 首尾帧输入与所有 `reference_*` 输入不能同时作为参数（参数约束，不是任务做不到）：要保留参考素材、又要某张图作开场画面时，把它也放进 `reference_images`，在 prompt 里写「以 [[handle]] 作为视频首帧」。
- 参考视频、音频的总时长无法从 URL 本地判断，超过 15 秒时由上游返回参数错误。

媒体格式限制（请求体总大小 ≤ 64 MB，大文件请用公网 URL）：图片 JPG/JPEG/PNG/WEBP/
HEIC/HEIF，单文件 ≤ 30 MB，边长 [256, 5760] px，宽高比 [0.4, 2.5]；视频 MP4/MOV
（H.264/H.265 + AAC/MP3），单文件 ≤ 50 MB，帧率 [23.976, 60]；音频 WAV/MP3，
单文件 ≤ 15 MB。

## 路由

不是任何档位的 `model="auto"` 默认落点（balanced 默认仍是 `MiniMax-H3`）；按名字调用即可。

## 调用示例

```json
{
  "prompt": "A cinematic shot of a train crossing a snowy valley at golden hour.",
  "model": "minimax-turbo-h3",
  "resolution": "480p",
  "duration_seconds": 5,
  "aspect_ratio": "16:9"
}
```

上游异步，但 `generate_video` **默认同步返回产物** —— 服务端替你轮询（上限 900 秒），
调用方不必传 `wait`。

结果没收到时（调用被中断、连接断开、本侧超时）用**提交时用的那个 `request_id`** 调
`task_status` / `task_wait` 取回：任务以它为主键落库，同一个 `request_id` 重复提交只会
返回既有任务、不会二次下单，而换一个新 id 重投就是二次计费。没有另一个 `task_id` 要等。

## 错误

参数错误多为 `prompt` 为空、`duration_seconds` 越界、`resolution` 传了 `480p` / `768p` 以外的档位，
或首尾帧与参考素材同时给出；此外还有鉴权失败、余额不足、内容审核拒绝、限流。
错误原因原样透传，`error_type` 与 `retryable` 已按类型归一。
