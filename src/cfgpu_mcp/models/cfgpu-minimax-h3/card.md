# MiniMax H3

## 基本信息

| 属性 | 值 |
|---|---|
| MCP 模型名 | `MiniMax-H3` |
| 上游模型 ID | `MiniMax-H3` |
| Provider | `cfgpu-daily`（测试期；上线后改为 `cfgpu`） |
| 创建接口 | `POST /video/generations` |
| 查询接口 | `GET /video/tasks/{task_id}` |

创建/查询走的是 CFGPU 统一的视频任务路由（和万相、可灵、HappyHorse 等同一条），
**不是** MiniMax 原生的 `/v2/video_generation`；换句话说，路径是 CFGPU 的，请求体
是 MiniMax 的。测试期它挂在 `cfgpu-daily` 这个独立 provider 上，凭证来自
`CFGPU_DAILY_API_TOKEN`——不是调用方逐请求带上来的 CFGPU 令牌（只有内建的 `cfgpu`
provider 会读那个 header）。上线后把 adapter.yaml 里的 `provider:` 改成 `cfgpu`
即可，其余不动。

## 计费

| 分辨率 | 价格 |
|---|---|
| `768P`（统一参数 `720p`） | 0.4 元 / 秒 |
| `2K`（统一参数 `1080p`） | 0.8 元 / 秒 |

按秒计价，`duration_seconds` 直接决定花费：1080p / 15s 一次约 12 元。

## 参数映射

| 统一参数 | MiniMax 请求参数 | 说明 |
|---|---|---|
| `prompt` | `content[].text` | 必须包含非空文本，单条最多 7000 字符 |
| `duration_seconds` | `duration` | 4–15 秒整数，不支持 `-1`；省略时取 5 |
| `resolution: 720p` | `resolution: 768P` | MiniMax 原生档位 |
| `resolution: 1080p` | `resolution: 2K` | MiniMax 原生档位 |
| `aspect_ratio` | `ratio` | 文生视频不接受 `adaptive`，缺省值会被替换为 `16:9` |
| `watermark` | `aigc_watermark` | AIGC 标识水印，默认 `false`，每次都显式发送 |

`480p` 不受支持——那是 `MiniMax-H3-Max`（另一个模型 ID，且不支持 2K、中间帧与
参考素材）的档位，这里不是「没映射」而是真的没有。图生视频不发送 `ratio`，由输入
图片推断；参考素材生视频可使用 `adaptive`（上游默认值）。`with_audio` 和
`prompt_extend` 不属于该接口参数，不会发送。

**文生视频的 `adaptive` 会被替换，而不是报错。** `adaptive` 是本层 schema 的默认值，
意思是「你来定」，不是调用方点名要一个上游不认的值——把它当成校验错误，会让
`generate_video(prompt=...)` 这种最朴素的调用根本路由不到本模型。替换值是 `16:9`，
并由 `validation_corrections` 报进 preflight 的 `corrected_args`。只有文生视频这么做：
有参考素材时 `adaptive` 合法且是上游默认，替换等于越过素材自己的画幅。

## 路由

本模型是 `model="auto"` 在 **balanced 一档**的视频默认落点（`default_for: [balanced]`）。
另外两档不受影响：`fast` 归 `doubao-seedance-2-0-fast`（6 分，确实更快），`best` 归
`doubao-seedance-2-5`（`quality_rank` 压倒性领先）。被 `supports()` 排除时（480p、
超过 15 秒、首尾帧与参考素材混用等）自动让位——`default_for` 是偏好，不是钉死。

`cost_tier: 2` 有依据：按秒计费的一组里 0.4 元/秒与记 cost 2 的 `cf-imagine-video`
（0.385）基本持平。`speed_tier: 3` 是**未实测的中性值**——本模型进 balanced 顶档靠的
是 `default_for` 这个显式声明，不是把速度档掰大；有真实时延数据后可直接更正，不会
牵动路由。

支持比例：`adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。

## Content 规则

- `first_frame`、`last_frame` 各最多 1 张，且 `last_frame` 必须与 `first_frame` 一起使用。
- `reference_images` 最多 9 张。
- `reference_videos` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- `reference_audios` 最多 3 个，单段 2–15 秒、总时长不超过 15 秒。
- 首尾帧输入与所有 `reference_*` 输入互斥。
- 参考视频、音频的总时长无法仅从 URL 本地判断，超过 15 秒时由上游返回参数错误。

输入媒体限制（请求体总大小 ≤ 64 MB，大文件请用公网 URL）：图片 JPG/JPEG/PNG/WEBP/
HEIC/HEIF，单文件 ≤ 30 MB，边长 [256, 5760] px，宽高比 [0.4, 2.5]；视频 MP4/MOV
（H.264/H.265 + AAC/MP3），单文件 ≤ 50 MB，帧率 [23.976, 60]；音频 WAV/MP3，
单文件 ≤ 15 MB。

上游还支持「中间帧」图生视频，本层未开放对应的参数槽位。

## 示例

```json
{
  "prompt": "A cinematic shot of a train crossing a snowy valley at golden hour.",
  "model": "MiniMax-H3",
  "resolution": "720p",
  "duration_seconds": 5,
  "aspect_ratio": "16:9"
}
```

上游实际创建请求中的关键字段为：

```json
{
  "model": "MiniMax-H3",
  "content": [{"type": "text", "text": "A cinematic shot of a train crossing a snowy valley at golden hour."}],
  "resolution": "768P",
  "duration": 5,
  "aigc_watermark": false,
  "ratio": "16:9"
}
```

上例显式传了 `16:9`；若省略 `aspect_ratio`（默认 `adaptive`），文生视频发出的
`ratio` 同样是 `16:9`。

## 响应结构

创建响应是**平铺**的，只有一个 `task_id`：

```json
{"task_id": "task_01K2..."}
```

查询响应把一切套在 `task` 下：

```json
{"task":{"id":"424010985738629","task_type":"generation","status":"succeeded","model":"MiniMax-H3","created_at":1785125529,"updated_at":1785125946,"content":{"url":"https://your-cdn.example.com/h3-generated-2k-output.mp4"},"resolution":"2K","duration":5,"ratio":"16:9","modality":"video","usage":{"total_seconds":5,"input_seconds":0,"output_seconds":5,"input_image_count":0}}}
```

两种形状**不一致**，而 CFGPU 的任务层是跨上游共用的、是否保留 MiniMax 这层信封并不
由本模型决定，所以 `MinimaxH3Adapter._task()` 对两种都成立：有 `task` 信封就拆开，
没有就直接读顶层。这样写的成本是零，猜错的成本是任务提交并计费了却永远读不回来。

`task.status` 会被标准化（`queued` → `pending`），成功时 `task.content.url` 作为视频
URL 返回，`task.usage` 原样保留为 usage 元数据。失败原因由
`task_manager._extract_error_message` 从 `task.error.message` 取出。

## 错误码

上游为 OpenAI 风格错误响应，HTTP 状态码即真实错误码，`error.message` 结尾括号内是
内部码：`400`（参数错误，如 `content must include a non-empty text item`）、`401`
（鉴权失败）、`402`（余额不足）、`422`（内容审核拒绝）、`429`（限流）、`500`。
