# Kling Video O1 (可灵 O1)

可灵 O1（可灵视频 O1 模型）是可灵 AI 推出的全球首个统一多模态视频生成模型。模型通过创新的多模态视觉语言（MVL）架构，实现视频生成、编辑与理解的无缝融合，支持图片、视频和文字等多模态输入，能进行全能创作编辑，解决视频一致性难题，提供多种创意组合。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `kling-video-o1` |
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit |
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
| mode | string | - | std | 生成模式：`std`（标准）/ `pro`（高质量），由 `quality_tier` 映射（`best` → `pro`） |
| seconds | string | ✓ | "5" | 视频时长（秒），字符串形式。视频编辑（`refer_type=base`）时不传，时长跟随源视频 |
| sound | string | - | - | 是否生成有声视频：`on` / `off`，由 `with_audio` 映射 |
| image_list | array | - | - | 图片输入数组，元素为 `{"image": url, "type": ...}`；`type` 可为 `first_frame`（首帧）/ `end_frame`（尾帧），**省略 `type` 即普通参考图**，带 `type` 与不带 `type` 的元素可混用 |
| video_list | array | - | - | 视频输入数组，元素为 `{"video_url": url, "refer_type": ...}`；`refer_type` 为 `feature`（参考其运镜/风格）或 `base`（作为被编辑的源视频） |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Kling 字段 | 映射说明 |
|------------------|------------|----------|
| prompt | prompt | 直接透传 |
| resolution + aspect_ratio | size | 映射成像素 `宽x高`，`aspect_ratio=adaptive` 时按 16:9 处理 |
| quality_tier | mode | `best` → `pro`，其余 → `std` |
| duration_seconds | seconds | 转成字符串透传；不支持 `-1` 智能时长 |
| with_audio | sound | `true` → `on`，`false` → `off` |
| first_frame | image_list[] | `{"image": url, "type": "first_frame"}` |
| last_frame | image_list[] | `{"image": url, "type": "end_frame"}`；需与 `first_frame` 同时给出 |
| reference_images | image_list[] | `{"image": url}`（不带 `type`） |
| reference_videos | video_list[] | `{"video_url": url, "refer_type": "feature"}` |
| reference_audios | -（不支持） | 请求体没有音频输入槽位，`supports()` 直接拒绝 |
| model_specific | -（合并到顶层） | 末位合并，可覆盖上述字段 |

**视频编辑（`refer_type=base`）**：统一 Schema 只有一个 `reference_videos` 槽位，无法区分「参考」与「编辑」，因此默认按 `feature` 下发。要做视频编辑，用 `model_specific` 整体覆盖 `video_list`：

```json
{"model_specific": {"video_list": [{"video_url": "https://src.mp4", "refer_type": "base"}]}}
```

adapter 在合并 `model_specific` 之后检查最终 `video_list`，若含 `refer_type=base` 则移除 `seconds`（除非调用方在 `model_specific` 里显式给了 `seconds`）。

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
- 需要显式时长，不支持 `duration_seconds=-1`。

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

### 视频编辑（base 源视频 + 风格图，无 `seconds`）

```json
{
  "model": "kling-video-o1",
  "prompt": "把背景换成沙滩",
  "mode": "pro",
  "size": "1920x1080",
  "video_list": [
    { "video_url": "https://src.mp4", "refer_type": "base" }
  ],
  "image_list": [
    { "image": "https://style.png", "type": "first_frame" }
  ]
}
```

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
