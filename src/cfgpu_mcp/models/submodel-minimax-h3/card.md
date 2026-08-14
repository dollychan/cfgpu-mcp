# MiniMax H3（Submodel）

## 基本信息

| 属性 | 值 |
|---|---|
| MCP 模型名 | `submodel/minimax-h3` |
| 上游模型 ID | `MiniMax-H3` |
| Provider | `submodel`（`https://h3.submodel.ai`） |
| 创建接口 | `POST /v2/video_generation` |
| 查询接口 | `GET /v2/query/video_generation/{task_id}` |

## 参数映射

| 统一参数 | Submodel 请求参数 | 说明 |
|---|---|---|
| `prompt` | `content[].text` | 必须包含非空文本 |
| `duration_seconds` | `duration` | 4–15 秒，不支持 `-1` |
| `resolution: 720p` | `resolution: 768P` | Submodel 原生档位 |
| `resolution: 1080p` | `resolution: 2K` | Submodel 原生档位 |
| `aspect_ratio` | `ratio` | 文生视频必须显式指定且不能为 `adaptive` |

`480p` 不受支持。图生视频不发送 `ratio`，由输入图片推断；参考素材生视频可使用
`adaptive`（上游默认值）。`with_audio` 和 `watermark` 不属于该接口参数，不会发送。

支持比例：`adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。

## Content 规则

- `first_frame`、`last_frame` 各最多 1 张，且 `last_frame` 必须与 `first_frame` 一起使用。
- `reference_images` 最多 9 张。
- `reference_videos` 最多 3 个，总时长不超过 15 秒。
- `reference_audios` 最多 3 个，总时长不超过 15 秒。
- 首尾帧输入与所有 `reference_*` 输入互斥。
- 参考视频、音频的总时长无法仅从 URL 本地判断，超过 15 秒时由上游返回参数错误。

## 示例

```json
{
  "prompt": "A cinematic shot of a train crossing a snowy valley at golden hour.",
  "model": "submodel/minimax-h3",
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
  "ratio": "16:9"
}
```

## 响应结构

创建响应是**平铺**的，只有一个 `task_id`：

```json
{"task_id": "task_01K2..."}
```

这一层用基类的 `ModelAdapter.extract_task_id()`（`resp.get("id") or resp.get("task_id")`）
即可取到，所以 `SubmodelH3Adapter` 不覆写它 —— 注意它与下面的查询响应**形状不同**：
查询把一切套在 `task` 下（`task.id`），创建则没有这层信封。

查询响应结构
```json
{"task":{"id":"task_0003ZHE0T90P1H3RDD8HHHA65P","task_type":"generation","status":"succeeded","model":"MiniMax-H3","created_at":1786691126,"updated_at":1786691333,"content":{"url":"https://h3.submodel.ai/output/h3-resul.../result.mp4"},"resolution":"768P","duration":5,"ratio":"16:9","usage":{"total_seconds":5,"input_seconds":0,"output_seconds":5,"input_image_count":0}}}
```

创建成功后保存 `task_id`；查询响应的 `task.status` 会被标准化，成功时
`task.content.url` 作为视频 URL 返回，`task.usage` 原样保留为 usage 元数据。
