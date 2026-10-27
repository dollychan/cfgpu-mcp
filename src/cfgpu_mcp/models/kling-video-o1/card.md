# Kling Video O1 (可灵 O1)

可灵 O1（可灵视频 O1 模型）是可灵 AI 推出的全球首个统一多模态视频生成模型。模型通过创新的多模态视觉语言（MVL）架构，实现视频生成、编辑与理解的无缝融合，支持图片、视频和文字等多模态输入，能进行全能创作编辑，解决视频一致性难题，提供多种创意组合。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `kling-video-o1` |
| 任务（tasks） | text_to_video, image_to_video, first_last_frame, reference_to_video, framed_reference_video, video_edit, synced_audio_output |
| 成本档位 | 4/5 |
| 速度档位 | 2/5 |

## 价格

按秒计费，分辨率与是否含音频/视频输入分档：

| 分辨率 | 场景 | 单价 |
|--------|------|------|
| (0, 720P] | 有视频输入的有声视频 | 0.945 元 / 秒 |
| (0, 720P] | 有视频输入的无声视频 | 0.945 元 / 秒 |
| (0, 720P] | 没有视频输入的有声视频 | 0.63 元 / 秒 |
| (0, 720P] | 没有视频输入的无声视频 | 0.63 元 / 秒 |
| (720P, ∞) | 有视频输入的有声视频 | 1.26 元 / 秒 |
| (720P, ∞) | 有视频输入的无声视频 | 1.26 元 / 秒 |
| (720P, ∞) | 没有视频输入的有声视频 | 0.84 元 / 秒 |
| (720P, ∞) | 没有视频输入的无声视频 | 0.84 元 / 秒 |

## 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 固定值：`kling-video-o1` |
| prompt | string | ✓ | - | 视频描述，支持中英文 |
| size | string | - | 1280x720 | 输出像素尺寸 `宽x高`，由统一 Schema 的 `resolution` + `aspect_ratio` 映射得到 |
| mode | string | - | pro | 生成模式：`std`（标准）/ `pro`（高质量），由 `quality_tier` 映射（`best` → `pro`）；adapter 总是显式发送 |
| seconds | string | ✓ | "5" | 视频时长（秒），字符串形式，取值 3–10。视频编辑（`refer_type=base`）时不传，时长跟随源视频 |
| sound | string | - | off | 是否生成有声视频：`on` / `off`，由 `with_audio` 映射；**有 `video_list` 时只能为 `off`** |
| image_list | array | - | - | 图片输入数组，元素为 `{"image": url, "type": ...}`；`type` 可为 `first_frame`（首帧）/ `end_frame`（尾帧），**省略 `type` 即普通参考图**，带 `type` 与不带 `type` 的元素可混用 |
| video_list | array | - | - | 视频输入数组，元素为 `{"video_url": url, "refer_type": ...}`；`refer_type` 为 `feature`（参考其运镜/风格）或 `base`（作为被编辑的源视频） |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Kling 字段 | 映射说明 |
|------------------|------------|----------|
| prompt | prompt | 直接透传 |
| resolution + aspect_ratio | size | 映射成像素 `宽x高`。可灵只有 16:9 / 9:16 / 1:1，没有 adaptive：`aspect_ratio=adaptive` 按 16:9 下发，`validate_only` 在 `corrected_args` 里钉成 `16:9` |
| quality_tier | mode | `best` → `pro`，其余 → `std` |
| duration_seconds | seconds | 转成字符串透传；不支持 `-1` 智能时长 |
| with_audio | sound | `true` → `on`，`false` → `off`。**preflight 会把它钉进 `corrected_args`**：`sound` 在本模型是真实请求字段（万相 2.6/2.7、HappyHorse 压根不发它），上游默认 `off`，统一 Schema 却默认 `true` 且总会发送，起决定作用的是一个与模型自身相反的静默默认值。不钉住的话，按调用方原始参数生成的审批卡上根本没有音频这一行。**带参考视频时一律下发 `off`**，并钉成 `with_audio: false`（上游报 `sound must be off when video_list is provided`；`with_audio` 默认 `true`，所以不拒绝，否则不提声音的默认请求也会失败）。源视频原声另由 `model_specific.keep_original_sound`（`yes` / `no`，也接受 `true` / `false`）控制 |
| first_frame | image_list[] | `{"image": url, "type": "first_frame"}`；有参考视频时不可用（图片只能作参考图，`supports()` 拒绝） |
| last_frame | image_list[] | `{"image": url, "type": "end_frame"}`；需与 `first_frame` 同时给出 |
| reference_images | image_list[] | `{"image": url}`（不带 `type`） |
| reference_videos | video_list[] | `{"video_url": url, "refer_type": "feature"}`；`model_specific.refer_type` / `keep_original_sound` 应用到每一项 |
| reference_audios | -（不支持） | 请求体没有音频输入槽位，`supports()` 直接拒绝 |
| model_specific | -（合并到顶层） | `refer_type` / `keep_original_sound` 由 adapter 取出、写进 `video_list` 每一项，不发到顶层；其余键末位合并，可覆盖上述字段 |

**视频编辑（`refer_type=base`）**：统一 Schema 只有一个 `reference_videos` 槽位，无法区分「参考」与「编辑」，因此默认按 `feature` 下发。要做视频编辑，源视频照常放 `reference_videos`，再加 `model_specific.refer_type`：

```json
{"reference_videos": ["https://src.mp4"], "model_specific": {"refer_type": "base", "keep_original_sound": "yes"}}
```

`supports()` 在本地校验：`refer_type` 只能是 `feature` / `base`，`keep_original_sound` 只能是 `yes` / `no`（或布尔）；二者都需要 `reference_videos`；`base` 要求**恰好一段**参考视频；不能与 `model_specific.video_list` 整体覆盖同时出现。整体覆盖 `video_list` 的旧写法仍可用。adapter 在合并之后检查最终 `video_list`，含 `refer_type=base` 则移除 `seconds`（除非调用方在 `model_specific` 里显式给了 `seconds`）。`list_model_profiles(required_tasks=["video_edit"])` 给出这一写法的 `task_parameters` 模板。

**分辨率 → size 对照（部分）：**

| resolution | aspect_ratio | size |
|------------|--------------|------|
| 480p | 16:9 | 854x480 |
| 720p | 16:9 | 1280x720 |
| 720p | 9:16 | 720x1280 |
| 1080p | 16:9 | 1920x1080 |
| 1080p | 9:16 | 1080x1920 |
| 1080p | 1:1 | 1080x1080 |

## 能力与限制

- 支持文生视频、图生视频（首帧）、首尾帧、多图/视频参考、视频编辑。
- 不支持 `reference_audios`：请求体没有音频输入槽位。
- `last_frame` 必须与 `first_frame` 同时给出（尾帧 `end_frame` 依赖首帧）。
- 有参考视频时，图片只能作参考图（`reference_images`），不能作首帧 / 尾帧；参考视频 + 参考图的组合正常放行。
- 需要显式时长 3–10 秒，不支持 `duration_seconds=-1`。
- 有参考视频时 `sound` 只能为 `off`（2026-10-22 实测：`sound must be off when video_list is provided`），adapter 一律下发 `off` 并在 `validate_only` 钉成 `with_audio: false`。

## 异步任务流程

可灵 O1 为异步接口，需轮询查询状态：

1. **创建任务**：POST `/video/generations`，返回 `id`（task_id）
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务 `completed` 后从 `taskResult.videos[].url` 取视频 URL（24 小时内有效）

## 示例

### 文生视频

```json
{
  "model": "kling-video-o1",
  "prompt": "一只可爱的橘猫在阳光下奔跑，慢镜头，电影质感",
  "size": "1920x1080",
  "mode": "pro",
  "seconds": "5"
}
```

### 首帧 + 参考图

```json
{
  "model": "kling-video-o1",
  "prompt": "参考这些图生成视频",
  "mode": "std",
  "size": "720x1280",
  "seconds": "5",
  "image_list": [
    { "image": "https://ref1.png", "type": "first_frame" },
    { "image": "https://ref2.png" }
  ]
}
```

### 首尾帧

```json
{
  "model": "kling-video-o1",
  "prompt": "首帧变尾帧",
  "mode": "std",
  "size": "720x720",
  "seconds": "5",
  "image_list": [
    { "image": "https://start.png", "type": "first_frame" },
    { "image": "https://end.png", "type": "end_frame" }
  ]
}
```

### 视频编辑（base 源视频 + 参考图，无 `seconds`）

```json
{
  "model": "kling-video-o1",
  "prompt": "把背景换成沙滩",
  "mode": "pro",
  "size": "1920x1080",
  "sound": "off",
  "video_list": [
    { "video_url": "https://src.mp4", "refer_type": "base", "keep_original_sound": "yes" }
  ],
  "image_list": [
    { "image": "https://style.png" }
  ]
}
```

参考图不带 `type`：**只要有参考视频（`feature` 或 `base`），图片就只能作为参考图**，不能是首帧 / 尾帧。

## 响应结构

创建视频响应结构：
```json
{"mode":"pro",
"seconds":"5",
"updated_at":1782873292,
"size":"1920x1080",
"billing_type_description":"pro x 无参考视频 x 无声",
"job_type_description":"可灵 Omni-Video",
"created_at":1782873292,"model":"kling-video-o1",
"id":"qvideo-1383109830-1782873292947656139",
"object":"video",
"status":"queued"}
```


查询任务结果 GET `/video/tasks/{task_id}` 返回标准视频任务结构：

```json
{"id":"qvideo-1383109830-1782873292947656139",
"object":"video",
"model":"kling-video-o1",
"mode":"pro",
"status":"completed",
"createdAt":1782873292,
"updatedAt":1782873373,
"completedAt":1782873373,
"seconds":"5",
"size":"1920x1080",
"taskResult":{"videos":[{"id":"qvideo-1383109830-1782873292947656139-1",
"url":"https://...",
"duration":"5"}]
},
"error":null}
```

### 计费字段（响应无 `usage`，由 adapter 组装）

可灵按秒计费、单价随分辨率与是否有视频输入/音频分档，但任务响应里**没有 `usage` 对象**，计费数值散在顶层。adapter 将其组装为与万相 / HappyHorse 一致的形状：

| 结果 `usage` 字段 | 来源 | 说明 |
|---|---|---|
| `duration` | `seconds`（字符串 `"5"`，转为数字） | 计费时长（秒）。视频编辑任务时长跟随源视频、不带 `seconds`，则取 `taskResult.videos[0].duration` |
| `sr` | `size` 的**短边**（`"1920x1080"` → `1080`） | 分辨率档位按短边划分，竖屏与横屏同档 |
| `ratio` | 由 `size` 反查 | 可灵只回传像素 `size`，不回传 `ratio`；该值同时用于结果的 `aspect_ratio` |

例：`"seconds":"5"` + `"size":"1920x1080"` → `usage: {"duration": 5, "sr": 1080, "ratio": "16:9"}`。
