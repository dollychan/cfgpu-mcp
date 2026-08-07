# Kling V3 Omni (可灵 V3 全能版)

Kling-V3-Omni 是全能多模态版本，将文/图生视频、视频编辑以及基于多参考图的角色和风格一致性控制，完美统一在了单一模型中。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `kling-v3-omni` |
| 能力标签 | text_to_video, image_to_video, first_last_frame, multi_modal_reference, video_edit |
| 成本档位 | 5/5 |
| 速度档位 | 2/5 |

## 价格

按秒计费，分辨率与是否含音频/视频输入分档：

| 分辨率 | 场景 | 单价 |
|--------|------|------|
| (0, 720P] | 有视频输入的有声视频 | 0.945 元 / 秒 |
| (0, 720P] | 有视频输入的无声视频 | 0.945 元 / 秒 |
| (0, 720P] | 没有视频输入的有声视频 | 0.84 元 / 秒 |
| (0, 720P] | 没有视频输入的无声视频 | 0.63 元 / 秒 |
| (720P, 1080P] | 有视频输入的有声视频 | 1.26 元 / 秒 |
| (720P, 1080P] | 有视频输入的无声视频 | 1.26 元 / 秒 |
| (720P, 1080P] | 没有视频输入的有声视频 | 1.05 元 / 秒 |
| (720P, 1080P] | 没有视频输入的无声视频 | 0.84 元 / 秒 |
| (1080P, ∞) | 统一计价 | 3.15 元 / 秒 |

## 参数说明

与 `kling-video-o1`（可灵 O1）共用同一套 flat 请求格式：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 固定值：`kling-v3-omni` |
| prompt | string | ✓ | - | 视频描述，支持中英文 |
| size | string | - | 1280x720 | 输出像素尺寸 `宽x高`，由统一 Schema 的 `resolution` + `aspect_ratio` 映射得到 |
| mode | string | - | std | 生成模式：`std` / `pro`，由 `quality_tier` 映射（`best` → `pro`） |
| seconds | string | ✓ | "5" | 视频时长（秒），字符串形式。视频编辑（`refer_type=base`）时不传，时长跟随源视频 |
| sound | string | - | - | 是否生成有声视频：`on` / `off`，由 `with_audio` 映射 |
| image_list | array | - | - | 图片输入数组，元素为 `{"image": url, "type": ...}`；`type` 可为 `first_frame` / `end_frame`，省略 `type` 即普通参考图 |
| video_list | array | - | - | 视频输入数组，元素为 `{"video_url": url, "refer_type": ...}`；`refer_type` 为 `feature`（参考其运镜/风格）或 `base`（被编辑的源视频） |

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
| reference_audios | -（不支持） | 请求体没有音频输入槽位 |
| model_specific | -（合并到顶层） | 末位合并，可覆盖上述字段 |

**视频编辑（`refer_type=base`）**：默认按 `feature` 下发，要做编辑用 `model_specific` 整体覆盖 `video_list`（详见 `kling-video-o1` 卡片）。

## 能力与限制

- 支持文生视频、图生视频（首帧）、首尾帧、多图/视频参考一致性控制、视频编辑。
- 不支持 `reference_audios`：请求体没有音频输入槽位。
- `last_frame` 必须与 `first_frame` 同时给出。
- 需要显式时长，不支持 `duration_seconds=-1`。

## 示例

### 文生视频

```json
{
  "model": "kling-v3-omni",
  "prompt": "一只可爱的橘猫在阳光下奔跑，慢镜头，电影质感",
  "size": "1920x1080",
  "mode": "pro",
  "seconds": "5"
}
```

### 参考视频运镜（有声）

```json
{
  "model": "kling-v3-omni",
  "prompt": "跟随参考视频运镜",
  "mode": "pro",
  "size": "1920x1080",
  "seconds": "5",
  "sound": "on",
  "video_list": [
    { "video_url": "https://ref.mp4", "refer_type": "feature" }
  ]
}
```

## 异步任务流程

可灵 V3 Omni 为异步接口，需轮询查询状态：

1. **创建任务**：POST `/video/generations`，返回 `id`（task_id）
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务 `completed` 后从 `taskResult.videos[].url` 取视频 URL（24 小时内有效）

## 计费字段（响应无 `usage`，由 adapter 组装）

响应结构同可灵 O1，任务响应里**没有 `usage` 对象**，计费数值散在顶层 `seconds` / `size`。adapter 将其组装为与万相 / HappyHorse 一致的形状：

| 结果 `usage` 字段 | 来源 | 说明 |
|---|---|---|
| `duration` | `seconds`（字符串 `"5"`，转为数字） | 计费时长（秒）。视频编辑任务时长跟随源视频、不带 `seconds`，则取 `taskResult.videos[0].duration` |
| `sr` | `size` 的**短边**（`"1920x1080"` → `1080`） | 分辨率档位按短边划分，竖屏与横屏同档 |
| `ratio` | 由 `size` 反查 | 只回传像素 `size`，不回传 `ratio`；该值同时用于结果的 `aspect_ratio` |

例：`"seconds":"5"` + `"size":"1920x1080"` → `usage: {"duration": 5, "sr": 1080, "ratio": "16:9"}`。
