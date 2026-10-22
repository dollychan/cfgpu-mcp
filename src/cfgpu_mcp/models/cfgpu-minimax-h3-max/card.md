# MiniMax H3 Max

## 基本信息

| 属性 | 值 |
|---|---|
| 模型名（`model` 参数） | `minimax-h3-max` |
| 任务类型 | video |
| 任务（tasks） | text_to_video, image_to_video, first_last_frame, reference_to_video, video_edit, synced_audio_output |
| 成本档位 | 1/5 |
| 速度档位 | 3/5 |

MiniMax H3 的**极速生成**版本。请求、查询格式与 `MiniMax-H3` 相同，差别只在下面列出的取值范围。

## 参数

| 参数 | 取值 | 说明 |
|---|---|---|
| `prompt` | 必填，非空 | 单条最多 7000 字符 |
| `duration_seconds` | 5–15 整数 | 省略时为 5；**不支持 4 秒** |
| `resolution` | `480p` \| `768p` | 省略时为 `768p`；**不支持 `2k`**，`720p` / `1080p` 同样不支持 |
| `aspect_ratio` | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 见下 |
| `prompt_extend` | 布尔，可省略 | 提示词扩写：省略时上游默认均衡扩写；`false` 关闭，`true` 均衡扩写 |
| `watermark` | 布尔，默认 `false` | AIGC 标识水印 |

提示词扩写对应上游 `extra.prompt_expansion_mode`（`disabled` / `balanced` / `quality`）。
`prompt` 里用了 `[[...]]` 标记且未设置 `prompt_extend` 时，扩写会被关闭，以免改写已写好的分镜、
台词和素材引用。要用质量优先扩写，传 `model_specific={"extra": {"prompt_expansion_mode": "quality"}}`；
`extra` 只接受这一个字段和这三个取值，其他写法在本地即被拒绝。

模型固定生成原生音频（`synced_audio_output`）；`with_audio` 不属于本模型的接口参数，
传入 `false` 也无法关闭音频。

**`adaptive` 在文生视频下会被替换成 `16:9`，不会报错**，替换结果由 preflight（`validate_only`）
在 `corrected_args` 里报出。图生视频不发送画幅，由输入图片推断；有参考素材时 `adaptive` 合法。

## 输入素材规则

- `first_frame`、`last_frame` 各最多 1 张，且 `last_frame` 必须与 `first_frame` 一起使用。
- `reference_images` 最多 9 张。
- `reference_videos` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- `reference_audios` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- 首尾帧输入与所有 `reference_*` 输入互斥。

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
  "model": "minimax-h3-max",
  "resolution": "480p",
  "duration_seconds": 5,
  "aspect_ratio": "16:9"
}
```

上游异步，`generate_video` 默认同步返回产物。结果没收到时用提交时的 `request_id` 调
`task_status` / `task_wait` 取回；同一个 `request_id` 重复提交不会二次下单。

## 错误

参数错误多为 `prompt` 为空、`duration_seconds` 不在 5–15、`resolution` 传了 `480p` / `768p` 以外的档位、
`extra` 写法不合法，或首尾帧与参考素材同时给出；此外还有鉴权失败、余额不足、内容审核拒绝、限流。
错误原因原样透传，`error_type` 与 `retryable` 已按类型归一。
